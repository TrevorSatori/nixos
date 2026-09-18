#!/usr/bin/env python3
"""
ABS (Audiobookshelf) → InfluxDB poller.
Reads listening sessions from the ABS API and writes them to InfluxDB
using line protocol over HTTP. No third-party dependencies.

Measurement schema:
  session,source=abs,type=audiobook,device=<device> \
    title="<title>",duration_s=<int>,wall_s=<int>,session_id="<id>" \
    <started_at_ms>

Each session writes two points: a start carrying the real duration, and a
stop sentinel at start+duration with duration_s=0. Both carry the same title
and session_id — a stop point is identified by duration_s == 0, never by an
empty title. (Writing empty strings there makes InfluxDB shift string values
onto neighbouring rows on range scans.)

Only *closed* sessions are written. ABS sessions are mutable: `timeListening`
keeps climbing until playback stops, and there is no status/endedAt field to
read. Since the watermark advances past anything written, persisting an
in-progress session would freeze a partial duration permanently. Open sessions
are identified via /api/sessions/open and deferred to a later poll.
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
    # The stop sentinel carries the *same* title and session_id as the start
    # point, matching jellyfin_webhook.py. Writing empty strings here corrupts
    # reads: when empty and non-empty string fields interleave in one series,
    # InfluxDB shifts string values onto neighbouring rows on range scans, so
    # titles silently land on the wrong session. `duration_s == 0` is what
    # marks a stop point — never the absence of a title.
    stop_fields  = (
        f'title="{title}",'
        f"duration_s=0i,"
        f"wall_s=0i,"
        f'session_id="{session_id}"'
    )
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


def open_session_ids() -> set[str]:
    """IDs of sessions ABS still considers in progress.

    ABS exposes no status/endedAt field on a session — `timeListening` simply
    keeps climbing until playback stops. Writing an in-progress session would
    freeze a partial duration into InfluxDB that never gets corrected, because
    the watermark below advances past it. So we skip them and pick them up on
    a later poll, once they are closed and their duration is final.
    """
    try:
        data = abs_get("/api/sessions/open")
    except Exception as e:
        # Fail closed: if we can't tell what's open, write nothing new this
        # round rather than risk persisting a partial duration.
        print(f"[WARN] could not fetch open sessions: {e}", flush=True)
        raise
    ids = {s.get("id") for s in (data.get("sessions") or []) if s.get("id")}
    ids |= {s.get("id") for s in (data.get("shareSessions") or []) if s.get("id")}
    return ids


def fetch_sessions_since(last_ms: int) -> tuple[list[dict], list[dict]]:
    """Fetch sessions with startedAt > last_ms (ABS returns desc order).

    Returns (closed, skipped_open). Callers must not advance the watermark
    past anything in `skipped_open`, or those sessions are lost forever.
    """
    open_ids = open_session_ids()
    closed, skipped, page = [], [], 0
    while True:
        data     = abs_get(f"/api/me/listening-sessions?desc=1&itemsPerPage=100&page={page}")
        sessions = data.get("sessions") or []
        if not sessions:
            break
        for s in sessions:
            if (s.get("startedAt") or 0) <= last_ms:
                return closed, skipped   # hit old sessions — stop paginating
            if s.get("id") in open_ids:
                print(f"[INFO] in progress, deferring: {s.get('displayTitle')}", flush=True)
                skipped.append(s)
                continue
            closed.append(s)
        total = data.get("total") or 0
        if total <= (page + 1) * 100:
            break
        page += 1
    return closed, skipped


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

    closed, skipped = fetch_sessions_since(last_ms)
    if not closed:
        print("[INFO] No new closed sessions", flush=True)
        return

    lines  = [line for s in closed for line in session_to_lines(s)]
    status = write_to_influx(lines)

    # Advance the watermark, but never past a session we deferred — otherwise
    # an in-progress session that started *before* a newer closed one would
    # fall out of the query window and never be written.
    newest = max(s.get("startedAt", 0) for s in closed)
    if skipped:
        earliest_open = min(s.get("startedAt", 0) for s in skipped)
        newest = min(newest, earliest_open - 1)
    if newest > last_ms:
        save_state(newest)

    print(
        f"[INFO] Wrote {len(closed)} sessions ({len(lines)} points) → InfluxDB "
        f"(HTTP {status}); {len(skipped)} deferred",
        flush=True,
    )


if __name__ == "__main__":
    print(f"[INFO] ABS→InfluxDB poller starting — polling every {POLL_INTERVAL}s", flush=True)
    while True:
        try:
            poll()
        except Exception as e:
            print(f"[ERROR] {e}", flush=True)
        time.sleep(POLL_INTERVAL)
