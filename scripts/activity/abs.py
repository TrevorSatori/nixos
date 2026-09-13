#!/usr/bin/env python3
"""
ABS (Audiobookshelf) → InfluxDB poller.
Reads listening sessions from the ABS API and writes them to InfluxDB
using line protocol over HTTP. No third-party dependencies.

Measurement schema:
  session,source=abs,type=audiobook,device=<device> \
    title="<title>",duration_s=<int>,wall_s=<int>,session_id="<id>" \
    <started_at_ms>
"""
import json
import os
import time
import urllib.request
from pathlib import Path

ABS_URL       = os.environ["ABS_URL"]
ABS_API_KEY   = os.environ["ABS_API_KEY"]
INFLUX_URL    = os.environ["INFLUX_URL"]
INFLUX_TOKEN  = os.environ["INFLUX_TOKEN"]
INFLUX_ORG    = os.environ["INFLUX_ORG"]
INFLUX_BUCKET = os.environ["INFLUX_BUCKET"]
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "600"))

# systemd StateDirectory creates and owns this path
STATE_DIR  = Path("/var/lib/abs-to-influx")
STATE_FILE = STATE_DIR / "state.json"


# ── InfluxDB line protocol helpers ────────────────────────────────────────────

def _escape_tag(s: str) -> str:
    """Escape tag keys and values: commas, equals, spaces."""
    return s.replace(",", r"\,").replace("=", r"\=").replace(" ", r"\ ")


def _escape_str_field(s: str) -> str:
    """Escape string field values: backslashes and double-quotes."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def session_to_lines(s: dict) -> list[str]:
    started_ms = s.get("startedAt") or 0
    updated_ms = s.get("updatedAt") or started_ms
    title      = _escape_str_field(s.get("displayTitle") or "Unknown")
    device     = _escape_tag(
        (s.get("deviceInfo") or {}).get("deviceName") or "unknown"
    )
    duration_s = int(s.get("timeListening") or 0)
    wall_s     = max(0, int((updated_ms - started_ms) / 1000))
    session_id = _escape_str_field(s.get("id") or "")
    end_ms     = started_ms + duration_s * 1000

    tags         = f"source=abs,type=audiobook,device={device}"
    start_fields = (
        f'title="{title}",'
        f"duration_s={duration_s}i,"
        f"wall_s={wall_s}i,"
        f'session_id="{session_id}"'
    )
    stop_fields  = 'title="",duration_s=0i,wall_s=0i,session_id=""'
    return [
        f"session,{tags} {start_fields} {started_ms}",
        f"session,{tags} {stop_fields} {end_ms}",
    ]


# ── ABS API ───────────────────────────────────────────────────────────────────

def abs_get(path: str) -> dict:
    req = urllib.request.Request(
        f"{ABS_URL}{path}",
        headers={"Authorization": f"Bearer {ABS_API_KEY}"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_sessions_since(last_ms: int) -> list[dict]:
    """Fetch all sessions with startedAt > last_ms (ABS returns desc order)."""
    results, page = [], 0
    while True:
        data     = abs_get(f"/api/me/listening-sessions?desc=1&itemsPerPage=100&page={page}")
        sessions = data.get("sessions") or []
        if not sessions:
            break
        for s in sessions:
            if (s.get("startedAt") or 0) > last_ms:
                results.append(s)
            else:
                return results   # hit old sessions — stop paginating
        total = data.get("total") or 0
        if total <= (page + 1) * 100:
            break
        page += 1
    return results


# ── InfluxDB write ────────────────────────────────────────────────────────────

def write_to_influx(lines: list[str]) -> int:
    body = "\n".join(lines).encode("utf-8")
    url  = (
        f"{INFLUX_URL}/api/v2/write"
        f"?org={INFLUX_ORG}&bucket={INFLUX_BUCKET}&precision=ms"
    )
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "text/plain; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status


# ── State ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"last_started_ms": 0}


def save_state(last_ms: int) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"last_started_ms": last_ms}))


# ── Poll loop ─────────────────────────────────────────────────────────────────

def poll() -> None:
    state   = load_state()
    last_ms = state.get("last_started_ms", 0)

    new_sessions = fetch_sessions_since(last_ms)
    if not new_sessions:
        print("[INFO] No new sessions", flush=True)
        return

    lines  = [line for s in new_sessions for line in session_to_lines(s)]
    status = write_to_influx(lines)
    newest = max(s.get("startedAt", 0) for s in new_sessions)
    save_state(newest)
    print(f"[INFO] Wrote {len(new_sessions)} sessions ({len(lines)} points) → InfluxDB (HTTP {status})", flush=True)


if __name__ == "__main__":
    print(f"[INFO] ABS→InfluxDB poller starting — polling every {POLL_INTERVAL}s", flush=True)
    while True:
        try:
            poll()
        except Exception as e:
            print(f"[ERROR] {e}", flush=True)
        time.sleep(POLL_INTERVAL)
