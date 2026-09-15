#!/usr/bin/env python3
"""
Jellyfin Webhook → InfluxDB receiver + SilverBullet note writer.

Each continuous play segment is a separate InfluxDB record. Pausing creates a
gap in the timeline; resuming opens a new segment. The IsPaused field in
PlaybackProgress events drives pause/resume detection.

  PlaybackStart    → open first segment
  PlaybackProgress → detect pause/resume transitions via IsPaused; update heartbeat
  PlaybackStop     → close final segment + maybe create/update SB note

Power-off recovery: progress events keep last_seen_ms fresh. On startup, any
segment open for > STALE_HOURS is closed using the last known position.

Duration = (seg_stop_ticks − seg_start_ticks) / 10_000_000 s
Sessions < MIN_DURATION_S → "session_short" (table only, not timeline).

Note creation threshold: max(300s, runtime * 0.15) — 5 min floor, 15% of runtime.
Convention A: episode/movie notes are anchored to the day playback stopped.
"""
import datetime
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

INFLUX_URL    = os.environ["INFLUX_URL"]
INFLUX_TOKEN  = os.environ["INFLUX_TOKEN"]
INFLUX_ORG    = os.environ["INFLUX_ORG"]
INFLUX_BUCKET = os.environ["INFLUX_BUCKET"]
LISTEN_PORT   = int(os.environ.get("WEBHOOK_PORT", "9096"))

JELLYFIN_URL  = os.environ.get("JELLYFIN_URL", "http://127.0.0.1:8096")
JELLYFIN_KEY  = os.environ.get("JELLYFIN_KEY", "")
SPACE_PATH    = Path(os.environ.get("SILVERBULLET_SPACE", "/data/media/silverbullet"))

STATE_DIR  = Path("/var/lib/jellyfin-webhook")
STATE_FILE = STATE_DIR / "state.json"

MIN_DURATION_S = 60     # below this → session_short in InfluxDB
STALE_HOURS    = 6
NOTE_MIN_S     = 300    # 5-minute absolute floor for note creation
NOTE_PCT       = 0.15   # 15% of runtime required for note creation
FINISHED_PCT   = 0.85   # position >= 85% of runtime → mark as finished


# ── session helpers ───────────────────────────────────────────────────────────

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


# ── line-protocol helpers ─────────────────────────────────────────────────────

def _tag(s: str) -> str:
    return str(s).replace(",", r"\,").replace("=", r"\=").replace(" ", r"\ ")


def _field(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


# ── general helpers ───────────────────────────────────────────────────────────

def now_ms() -> int:
    return int(time.time() * 1000)


def today_iso() -> str:
    return datetime.date.today().isoformat()


def slug(s: str) -> str:
    """Lowercase snake_case slug for filenames."""
    return re.sub(r'[^a-z0-9]+', '_', s.lower()).strip('_')


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _space_uid_gid() -> tuple[int, int]:
    """Return uid/gid of the SilverBullet space owner so notes are readable by SB."""
    try:
        st = SPACE_PATH.stat()
        return st.st_uid, st.st_gid
    except Exception:
        return -1, -1


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, path)
        uid, gid = _space_uid_gid()
        if uid != -1:
            os.chown(path, uid, gid)
        os.chmod(path, 0o664)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


# ── InfluxDB ──────────────────────────────────────────────────────────────────

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


# ── Jellyfin API ──────────────────────────────────────────────────────────────

def jellyfin_get(path: str) -> dict | None:
    if not JELLYFIN_KEY:
        return None
    url = f"{JELLYFIN_URL}/{path.lstrip('/')}"
    req = urllib.request.Request(url, headers={"X-Emby-Token": JELLYFIN_KEY})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[WARN] Jellyfin API {path}: {e}", flush=True)
        return None


def fetch_item_meta(item_id: str) -> dict | None:
    # `/Items/{id}` returns 400 on this Jellyfin build. The `/Items?Ids=…`
    # query-param form is reliable and returns `{"Items": [...]}`.
    payload = jellyfin_get(
        f"/Items?Ids={item_id}&Fields=Genres,People,Studios,ProviderIds,Overview"
    )
    if not payload:
        return None
    items = payload.get("Items") or []
    return items[0] if items else None


def download_cover(item_id: str, dest: Path) -> bool:
    """Download Primary image for item_id to dest. Skip if already exists."""
    if not JELLYFIN_KEY or dest.exists():
        return dest.exists()
    url = f"{JELLYFIN_URL}/Items/{item_id}/Images/Primary?quality=90"
    req = urllib.request.Request(url, headers={"X-Emby-Token": JELLYFIN_KEY})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.read())
        return True
    except Exception as e:
        print(f"[WARN] Cover download {item_id}: {e}", flush=True)
        return False


# ── frontmatter helpers ───────────────────────────────────────────────────────

def fmt_yaml_list(items: list) -> str:
    if not items:
        return "[]"
    return "[" + ", ".join(str(i) for i in items) + "]"


def read_frontmatter(text: str) -> dict:
    """Parse simple key: value frontmatter. No nested structures."""
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    fm = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def update_frontmatter_field(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^({re.escape(key)}:).*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(rf"\1 {value}", text, count=1)
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[:end] + f"\n{key}: {value}" + text[end:]


def increment_frontmatter_int(text: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}:\s*(\d+)", text, re.MULTILINE)
    if not m:
        return text
    return re.sub(
        rf"^({re.escape(key)}:)\s*\d+",
        rf"\1 {int(m.group(1)) + 1}",
        text, count=1, flags=re.MULTILINE,
    )


# ── movie notes ───────────────────────────────────────────────────────────────

def create_or_update_movie_note(meta: dict, total_s: int, finished: bool) -> None:
    title       = meta.get("Name") or "Unknown"
    year        = meta.get("ProductionYear") or ""
    stub        = f"{slug(title)}{f'_{year}' if year else ''}"
    fname       = f"{stub}.md"
    note_path   = SPACE_PATH / "movies" / fname
    # cover_rel is relative from the note (movies/xxx.md), so images resolve
    cover_rel   = f"covers/{stub}.jpg"
    cover_abs   = SPACE_PATH / "movies" / cover_rel

    directors   = [p["Name"] for p in (meta.get("People") or []) if p.get("Type") == "Director"]
    genre_list  = meta.get("Genres") or []
    genre_yaml  = fmt_yaml_list(genre_list)
    genre_str   = ", ".join(genre_list)
    runtime_min = int((meta.get("RunTimeTicks") or 0) / 600_000_000)
    imdb_id     = (meta.get("ProviderIds") or {}).get("Imdb", "")
    imdb_rating = meta.get("CommunityRating") or ""
    director    = directors[0] if directors else ""

    download_cover(meta["Id"], cover_abs)

    if note_path.exists():
        text = note_path.read_text()
        fm   = read_frontmatter(text)
        if fm.get("status") == "finished":
            rewatch = f"\n## 🔁 Rewatch — {today_iso()}\n- \n\n"
            atomic_write(note_path, text.rstrip() + "\n" + rewatch)
            print(f"[NOTE] Movie rewatch: {fname}", flush=True)
        elif finished:
            text = update_frontmatter_field(text, "status", "finished")
            text = update_frontmatter_field(text, "watched", today_iso())
            atomic_write(note_path, text)
            print(f"[NOTE] Movie finished: {fname}", flush=True)
        else:
            print(f"[NOTE] Movie still watching: {fname}", flush=True)
        return

    status  = "finished" if finished else "watching"
    watched = today_iso() if finished else ""
    watch_year = (watched or today_iso())[:4]
    header_line = f"# {title}" + (f" ({year})" if year else "")
    content = (
        f"---\n"
        f"title: \"{title}\"\n"
        f"year: {year}\n"
        f'watch_log: "[[watch_logs/watch_log_{watch_year}]]"\n'
        f'catalog: "[[movies/movies]]"\n'
        f"director: \"{director}\"\n"
        f"genre: {genre_yaml}\n"
        f"runtime: {runtime_min}\n"
        f"rating:\n"
        f"status: {status}\n"
        f"watched: {watched}\n"
        f"cover: {cover_rel}\n"
        f"tags: [movie]\n"
        f"---\n"
        f"{header_line}\n\n"
        f"![cover]({cover_rel})\n\n"
        f"**Director:** {director}\n"
        f"**Genre:** {genre_str}\n"
        f"**Runtime:** {runtime_min} min\n"
        f"\n## 💭 Thoughts\n- \n"
    )
    atomic_write(note_path, content)
    print(f"[NOTE] Movie created: {fname}", flush=True)


# ── show notes ────────────────────────────────────────────────────────────────

def create_or_update_show_note(meta: dict, total_s: int, ep_finished: bool) -> None:
    series_name = meta.get("SeriesName") or meta.get("Name") or "Unknown"
    season_num  = int(meta.get("ParentIndexNumber") or 1)
    ep_num      = int(meta.get("IndexNumber") or 1)
    ep_title    = meta.get("Name") or ""
    season_id   = meta.get("SeasonId") or ""

    fname     = f"{slug(series_name)}_season_{season_num}.md"
    note_path = SPACE_PATH / "shows" / fname
    # cover_rel relative from note (shows/xxx.md) so images resolve
    cover_rel = f"covers/{slug(series_name)}_s{season_num}.jpg"
    cover_abs = SPACE_PATH / "shows" / cover_rel

    season_meta = jellyfin_get(
        f"/Items/{season_id}?Fields=Genres,Studios"
    ) if season_id else None

    ep_count    = (season_meta or {}).get("ChildCount") or ""
    _sg = (season_meta or meta).get("Genres") or []
    genre_yaml  = fmt_yaml_list(_sg)
    genre_str   = ", ".join(_sg)
    studios     = (season_meta or meta).get("Studios") or []
    network     = studios[0].get("Name") if studios else ""
    year        = meta.get("ProductionYear") or ""
    imdb_id     = (meta.get("ProviderIds") or {}).get("Imdb", "")
    imdb_rating = meta.get("CommunityRating") or ""

    download_cover(season_id or meta["Id"], cover_abs)

    ep_header = f"## Episode {ep_num} — {ep_title}" if ep_title else f"## Episode {ep_num}"
    ep_body   = f"{ep_header}\n*{today_iso()} · {total_s // 60} min*\n- \n\n"

    if note_path.exists():
        text = note_path.read_text()

        # Episode already logged → no-op
        if re.search(rf"^## Episode {ep_num}\b", text, re.MULTILINE):
            print(f"[NOTE] Episode already logged: {fname} ep{ep_num}", flush=True)
            return

        # Insert in numeric order — find first ## Episode N where N > ep_num
        insert_at = None
        for m in re.finditer(r"^## Episode (\d+)\b", text, re.MULTILINE):
            if int(m.group(1)) > ep_num:
                insert_at = m.start()
                break

        if insert_at is not None:
            text = text[:insert_at] + ep_body + text[insert_at:]
        else:
            text = text.rstrip() + "\n\n" + ep_body

        text = increment_frontmatter_int(text, "episodes_watched")

        if ep_count and ep_num == int(ep_count):
            text = update_frontmatter_field(text, "status", "finished")
            text = update_frontmatter_field(text, "finished", today_iso())

        atomic_write(note_path, text)
        print(f"[NOTE] Episode added: {fname} ep{ep_num}", flush=True)
        return

    # Fresh season note
    watch_year = today_iso()[:4]
    header_line = f"# {series_name} \u2014 Season {season_num}"
    net_line    = f"**Network:** {network}\n" if network else ""
    content = (
        f"---\n"
        f"title: \"{series_name}\"\n"
        f"season: {season_num}\n"
        f"year: {year}\n"
        f'watch_log: "[[watch_logs/watch_log_{watch_year}]]"\n'
        f'catalog: "[[shows/shows]]"\n'
        f"genre: {genre_yaml}\n"
        f"network: {network}\n"
        f"episode_count: {ep_count}\n"
        f"episodes_watched: 1\n"
        f"status: watching\n"
        f"started: {today_iso()}\n"
        f"finished:\n"
        f"rating:\n"
        f"cover: {cover_rel}\n"
        f"tags: [show]\n"
        f"---\n"
        f"{header_line}\n\n"
        f"![cover]({cover_rel})\n\n"
        f"{net_line}"
        f"**Year:** {year}\n"
        f"**Genre:** {genre_str}\n"
        f"\n## 💭 Thoughts\n- \n\n"
        f"{ep_body}"
    )
    atomic_write(note_path, content)
    print(f"[NOTE] Show created: {fname} ep{ep_num}", flush=True)


# ── threshold + dispatcher ────────────────────────────────────────────────────

def meets_threshold(cumulative_s: int, runtime_s: int) -> bool:
    threshold = max(NOTE_MIN_S, int(runtime_s * NOTE_PCT)) if runtime_s > 0 else NOTE_MIN_S
    return cumulative_s >= threshold


def maybe_create_note(sess: dict, stop_ticks: int, total_s: int) -> None:
    item_id   = sess.get("item_id")
    item_type = sess.get("item_type", "Movie")
    if not item_id or not JELLYFIN_KEY:
        return
    try:
        meta = fetch_item_meta(item_id)
        if not meta:
            return

        runtime_ticks = meta.get("RunTimeTicks") or 0
        runtime_s     = int(runtime_ticks / 10_000_000)
        threshold_s   = max(NOTE_MIN_S, int(runtime_s * NOTE_PCT)) if runtime_s > 0 else NOTE_MIN_S

        if not meets_threshold(total_s, runtime_s):
            print(
                f"[INFO] Below note threshold ({total_s}s / need {threshold_s}s): {sess['title']}",
                flush=True,
            )
            return

        finished = (
            runtime_ticks > 0
            and stop_ticks > 0
            and (stop_ticks / runtime_ticks) >= FINISHED_PCT
        )

        if item_type == "Episode":
            create_or_update_show_note(meta, total_s, finished)
        else:
            create_or_update_movie_note(meta, total_s, finished)

    except Exception as e:
        print(f"[ERROR] Note creation for '{sess.get('title','?')}': {e}", flush=True)


# ── segment write ─────────────────────────────────────────────────────────────

def flush_segment(sess: dict, stop_ms: int, stop_ticks: int, reason: str = "stop") -> int:
    """Write one play segment to InfluxDB. Returns duration_s written (0 if skipped)."""
    start_ms    = sess["segment_start_ms"]
    start_ticks = sess["segment_start_ticks"]
    title       = sess["title"]
    device      = sess["device"]
    item_type   = sess.get("item_type", "Movie")

    duration_s = max(0, int((stop_ticks - start_ticks) / 10_000_000))
    wall_s     = max(0, int((stop_ms - start_ms) / 1000))
    if duration_s == 0:
        duration_s = wall_s

    if duration_s == 0:
        return 0

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
    return duration_s


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

    if key in state:
        prev = state[key]
        if not prev.get("is_paused", False):
            stop_ticks = prev.get("last_pos_ticks", prev["segment_start_ticks"])
            dur = flush_segment(prev, now_ms(), stop_ticks, reason="closed-by-new-start")
            prev["cumulative_s"] = prev.get("cumulative_s", 0) + dur

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
        "cumulative_s":        0,
    }
    save_state(state)
    print(f"[INFO] Start: {state[key]['title']} on {state[key]['device']}", flush=True)


def handle_progress(p: dict) -> None:
    key   = session_key(p)
    state = load_state()

    if key not in state:
        handle_start(p)
        return

    sess       = state[key]
    is_paused  = bool(p.get("IsPaused", False))
    was_paused = sess.get("is_paused", False)
    cur_ticks  = p.get("PlaybackPositionTicks") or sess.get("last_pos_ticks", 0)
    cur_ms     = now_ms()

    if not was_paused and is_paused:
        dur = flush_segment(sess, cur_ms, cur_ticks, reason="pause")
        sess["cumulative_s"] = sess.get("cumulative_s", 0) + dur
        sess["is_paused"] = True

    elif was_paused and not is_paused:
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
        final_s = 0
        if not sess.get("is_paused", False):
            final_s = flush_segment(sess, stop_ms, stop_ticks, reason="stop")
        else:
            print(f"[INFO] Stopped while paused — no open segment: {sess['title']}", flush=True)

        total_s = sess.get("cumulative_s", 0) + final_s
        maybe_create_note(sess, stop_ticks, total_s)
    else:
        print(f"[WARN] Stop with no matching start: {build_title(p)}", flush=True)


# ── HTTP server ───────────────────────────────────────────────────────────────

class WebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        self.send_response(200)
        self.end_headers()
        try:
            payload = json.loads(body)
            event   = payload.get("NotificationType", "")
            if event != "PlaybackProgress":
                print(
                    f"[INFO] {event}: {payload.get('Name','?')} [{payload.get('DeviceName','?')}]",
                    flush=True,
                )
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
