#!/usr/bin/env python3
"""
Jellyfin Webhook → InfluxDB receiver.

Each continuous play segment is a separate InfluxDB record. Pausing creates a
gap in the timeline; resuming opens a new segment. The IsPaused field in
PlaybackProgress events drives pause/resume detection.

  PlaybackStart    → open first segment
  PlaybackProgress → detect pause/resume transitions via IsPaused; update heartbeat
  PlaybackStop     → close final segment

Power-off recovery: progress events keep last_seen_ms fresh. On startup, any
segment open for > STALE_HOURS is closed using the last known position.

Duration = (seg_stop_ticks − seg_start_ticks) / 10_000_000 s
Sessions < MIN_DURATION_S → "session_short" (table only, not timeline).
"""
import hashlib
import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

INFLUX_URL    = os.environ["INFLUX_URL"]
INFLUX_TOKEN  = os.environ["INFLUX_TOKEN"]
INFLUX_ORG    = os.environ["INFLUX_ORG"]
INFLUX_BUCKET = os.environ["INFLUX_BUCKET"]
LISTEN_PORT   = int(os.environ.get("WEBHOOK_PORT", "9096"))

STATE_DIR  = Path("/var/lib/jellyfin-webhook")
STATE_FILE = STATE_DIR / "state.json"

MIN_DURATION_S = 60
STALE_HOURS    = 6


# ── helpers ───────────────────────────────────────────────────────────────────

def _tag(s: str) -> str:
    return str(s).replace(",", r"\,").replace("=", r"\=").replace(" ", r"\ ")


def _field(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def now_ms() -> int:
    return int(time.time() * 1000)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def write_to_influx(lines: list[str]) -> int:
    body = "\n".join(lines).encode("utf-8")
    url  = f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={INFLUX_BUCKET}&precision=ms"
    req  = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type":  "text/plain; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status


def session_key(p: dict) -> str:
    return f"{p.get('UserId','')}/{p.get('ItemId','')}/{p.get('DeviceId','')}"


def build_title(p: dict) -> str:
    if p.get("ItemType") == "Episode":
        series  = p.get("SeriesName") or ""
        season  = int(p.get("SeasonNumber") or 0)
        episode = int(p.get("EpisodeNumber") or 0)
        name    = p.get("Name") or ""
        return f"{series} - s{season:02d}e{episode:02d} - {name}"
    return p.get("Name") or "Unknown"


# ── segment write ─────────────────────────────────────────────────────────────

def flush_segment(sess: dict, stop_ms: int, stop_ticks: int, reason: str = "stop") -> None:
    """Write one continuous play segment to InfluxDB and log it."""
    start_ms    = sess["segment_start_ms"]
    start_ticks = sess["segment_start_ticks"]
    title       = sess["title"]
    device      = sess["device"]
    item_type   = sess.get("item_type", "Movie")

    duration_s = max(0, int((stop_ticks - start_ticks) / 10_000_000))
    wall_s     = max(0, int((stop_ms - start_ms) / 1000))
    if duration_s == 0:
        duration_s = wall_s  # fallback if ticks unavailable

    if duration_s == 0:
        return

    itype       = "tv_episode" if item_type == "Episode" else "movie"
    measurement = "session_short" if duration_s < MIN_DURATION_S else "session"
    sid         = hashlib.sha1(f"{start_ms}|{sess.get('item_id','')}".encode()).hexdigest()[:16]

    tags         = f"source=jellyfin,type={_tag(itype)},device={_tag(device)}"
    start_fields = (
        f'title="{_field(title)}",'
        f"duration_s={duration_s}i,"
        f"wall_s={wall_s}i,"
        f'session_id="jf-{sid}"'
    )
    stop_fields = (
        f'title="{_field(title)}",'
        f"duration_s=0i,"
        f"wall_s=0i,"
        f'session_id="jf-{sid}"'
    )

    lines = [
        f"{measurement},{tags} {start_fields} {start_ms}",
        f"{measurement},{tags} {stop_fields} {stop_ms}",
    ]
    status = write_to_influx(lines)
    print(f"[INFO] {reason}: {title} — {duration_s}s → {measurement} (HTTP {status})", flush=True)


# ── startup: recover stale open segments ──────────────────────────────────────

def recover_stale_sessions() -> None:
    state  = load_state()
    cutoff = now_ms() - STALE_HOURS * 3600 * 1000
    stale  = [
        k for k, s in state.items()
        if s.get("last_seen_ms", s.get("segment_start_ms", s.get("start_ms", 0))) < cutoff
    ]
    if not stale:
        return
    for key in stale:
        sess = state.pop(key)
        if not sess.get("is_paused", False):
            stop_ms    = sess.get("last_seen_ms", now_ms())
            stop_ticks = sess.get("last_pos_ticks", sess["segment_start_ticks"])
            flush_segment(sess, stop_ms, stop_ticks, reason="stale-recovery")
        else:
            print(f"[INFO] Discarding stale paused session: {sess['title']}", flush=True)
    save_state(state)


# ── event handlers ────────────────────────────────────────────────────────────

def handle_start(p: dict) -> None:
    key   = session_key(p)
    state = load_state()

    # Close any lingering open segment for this key (resume after crash, etc.)
    if key in state:
        prev = state[key]
        if not prev.get("is_paused", False):
            stop_ticks = prev.get("last_pos_ticks", prev["segment_start_ticks"])
            flush_segment(prev, now_ms(), stop_ticks, reason="closed-by-new-start")

    ticks = p.get("PlaybackPositionTicks") or 0
    state[key] = {
        "title":               build_title(p),
        "device":              p.get("DeviceName") or "unknown",
        "item_type":           p.get("ItemType") or "Movie",
        "item_id":             p.get("ItemId") or "",
        "is_paused":           False,
        "segment_start_ms":    now_ms(),
        "segment_start_ticks": ticks,
        "last_pos_ticks":      ticks,
        "last_seen_ms":        now_ms(),
    }
    save_state(state)
    print(f"[INFO] Start: {state[key]['title']} on {state[key]['device']}", flush=True)


def handle_progress(p: dict) -> None:
    key   = session_key(p)
    state = load_state()

    if key not in state:
        # Missed PlaybackStart — treat this progress as the session start
        handle_start(p)
        return

    sess       = state[key]
    is_paused  = bool(p.get("IsPaused", False))
    was_paused = sess.get("is_paused", False)
    cur_ticks  = p.get("PlaybackPositionTicks") or sess.get("last_pos_ticks", 0)
    cur_ms     = now_ms()

    if not was_paused and is_paused:
        # → paused: close the segment that just ended
        flush_segment(sess, cur_ms, cur_ticks, reason="pause")
        sess["is_paused"] = True

    elif was_paused and not is_paused:
        # → resumed: open a new segment from current position
        sess["is_paused"]           = False
        sess["segment_start_ms"]    = cur_ms
        sess["segment_start_ticks"] = cur_ticks
        print(f"[INFO] Resume: {sess['title']}", flush=True)

    sess["last_pos_ticks"] = cur_ticks
    sess["last_seen_ms"]   = cur_ms
    state[key] = sess
    save_state(state)


def handle_stop(p: dict) -> None:
    key   = session_key(p)
    state = load_state()
    sess  = state.pop(key, None)
    save_state(state)

    stop_ms    = now_ms()
    stop_ticks = p.get("PlaybackPositionTicks") or 0

    if sess:
        if not sess.get("is_paused", False):
            flush_segment(sess, stop_ms, stop_ticks, reason="stop")
        else:
            print(f"[INFO] Stopped while paused — no open segment: {sess['title']}", flush=True)
    else:
        print(f"[WARN] Stop with no matching start: {build_title(p)}", flush=True)


# ── HTTP server ───────────────────────────────────────────────────────────────

class WebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence default access log

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        self.send_response(200)
        self.end_headers()
        try:
            payload = json.loads(body)
            event   = payload.get("NotificationType", "")
            if event != "PlaybackProgress":
                print(f"[INFO] {event}: {payload.get('Name','?')} [{payload.get('DeviceName','?')}]",
                      flush=True)
            if event == "PlaybackStart":
                handle_start(payload)
            elif event == "PlaybackProgress":
                handle_progress(payload)
            elif event == "PlaybackStop":
                handle_stop(payload)
        except Exception as e:
            print(f"[ERROR] {e}", flush=True)


if __name__ == "__main__":
    print(f"[INFO] Jellyfin webhook receiver on 127.0.0.1:{LISTEN_PORT}", flush=True)
    recover_stale_sessions()
    HTTPServer(("127.0.0.1", LISTEN_PORT), WebhookHandler).serve_forever()
