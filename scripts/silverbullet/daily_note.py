#!/usr/bin/env python3
"""
Daily-note writer: for a given local date, generate/refresh a SilverBullet
journal page under Journal/YYYY/MM/YYYY-MM-DD.md.

Sources:
  - activity bucket   → session list (abs / jellyfin / garmin)
  - biometrics bucket → daily summary (resting HR, steps, body battery, stress)

The writer is idempotent:
  - If the file exists, only content between <!-- BEGIN AUTO --> / <!-- END AUTO -->
    markers is rewritten. Everything else (Journal section, custom edits) is preserved.
  - Atomic write (temp file + rename) so SilverBullet never reads a partial file.

Invocation:
  daily_note.py                   → yesterday + today skeleton
  daily_note.py 2026-09-08        → that specific date only
"""
import json
import os
import re
import sys
import tempfile
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SPACE_PATH        = Path(os.environ.get("SILVERBULLET_SPACE", "/data/media/silverbullet"))
INFLUX_URL        = os.environ["INFLUX_URL"]
INFLUX_TOKEN      = os.environ["INFLUX_TOKEN"]
INFLUX_ORG        = os.environ["INFLUX_ORG"]
ACTIVITY_BUCKET   = os.environ.get("INFLUX_ACTIVITY_BUCKET",   "activity")
BIOMETRICS_BUCKET = os.environ.get("INFLUX_BIOMETRICS_BUCKET", "biometrics")
LOCAL_TZ          = ZoneInfo(os.environ.get("TZ", "America/Chicago"))

ICON = {
    ("abs", "audiobook"):        "📚",
    ("jellyfin", "movie"):       "🎬",
    ("jellyfin", "tv_episode"):  "📺",
    ("garmin", "running"):       "🏃",
    ("garmin", "treadmill"):     "🏃",
    ("garmin", "walking"):       "🚶",
    ("garmin", "hiking"):        "🥾",
    ("garmin", "cycling"):       "🚴",
    ("garmin", "indoor_cycling"):"🚴",
    ("garmin", "strength"):      "💪",
    ("garmin", "yoga"):          "🧘",
    ("garmin", "swimming"):      "🏊",
    ("garmin", "cardio"):        "❤️",
}


# ── influx query helper ──────────────────────────────────────────────────────

def influx_query(bucket: str, flux: str) -> list[dict]:
    """POST a Flux query, return list of dicts (one per record). Uses CSV parsing."""
    body = flux.encode("utf-8")
    url  = f"{INFLUX_URL}/api/v2/query?org={INFLUX_ORG}"
    req  = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type":  "application/vnd.flux",
            "Accept":        "application/csv",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")

    # Flux CSV: header row starts with ",result,table,_start,_stop,..."
    rows: list[dict] = []
    for block in raw.strip().split("\n\n"):
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        header = None
        for line in lines:
            cols = line.split(",")
            if cols[:2] == ["", "result"]:
                header = cols
                continue
            if header is None or cols[:2] == ["", ""]:
                continue
            rows.append(dict(zip(header, cols)))
    return rows


# ── range for a given local date ─────────────────────────────────────────────

def day_range_utc(d: date) -> tuple[str, str]:
    start_local = datetime.combine(d,             time.min, tzinfo=LOCAL_TZ)
    stop_local  = datetime.combine(d + timedelta(days=1), time.min, tzinfo=LOCAL_TZ)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (start_local.astimezone(timezone.utc).strftime(fmt),
            stop_local .astimezone(timezone.utc).strftime(fmt))


# ── activities for the day ───────────────────────────────────────────────────

def fetch_activities(d: date) -> list[dict]:
    start, stop = day_range_utc(d)
    flux = f'''from(bucket: "{ACTIVITY_BUCKET}")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "session" or r._measurement == "session_short")
  |> filter(fn: (r) => r._field == "title" or r._field == "duration_s")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.duration_s and int(v: r.duration_s) > 0)
  |> keep(columns: ["_time", "title", "duration_s", "source", "type"])
  |> group()
  |> sort(columns: ["_time"])'''
    rows = influx_query(ACTIVITY_BUCKET, flux)

    out = []
    for r in rows:
        try:
            ts_utc = datetime.strptime(r["_time"].split(".")[0], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        out.append({
            "time_local": ts_utc.astimezone(LOCAL_TZ),
            "title":      r.get("title", ""),
            "duration_s": int(float(r.get("duration_s", "0") or 0)),
            "source":     r.get("source", ""),
            "type":       r.get("type", ""),
        })
    return out


def format_activity_line(a: dict) -> str:
    icon      = ICON.get((a["source"], a["type"]), "📝")
    hhmm      = a["time_local"].strftime("%H:%M")
    minutes   = max(1, a["duration_s"] // 60)
    return f"- {hhmm} {icon} {a['title']} — {minutes} min"


# ── biometrics summary ───────────────────────────────────────────────────────

def fetch_scalar(d: date, metric: str, agg: str) -> str | None:
    """Return a single aggregated biometric value as a string, or None."""
    start, stop = day_range_utc(d)
    fn = {"last": "last()", "sum": "sum()", "first": "first()", "mean": "mean()"}[agg]
    flux = f'''from(bucket: "{BIOMETRICS_BUCKET}")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "biometric" and r.metric == "{metric}")
  |> filter(fn: (r) => r._value >= 0)
  |> {fn}'''
    rows = influx_query(BIOMETRICS_BUCKET, flux)
    if not rows:
        return None
    val = rows[0].get("_value")
    return val if val not in (None, "") else None


def fmt_int(s: str | None) -> str:
    if s is None: return "—"
    try:    return f"{int(float(s)):,}"
    except: return "—"


def fmt_bpm(s: str | None) -> str:
    if s is None: return "—"
    try:    return f"{int(float(s))} bpm"
    except: return "—"


def fetch_health(d: date) -> dict:
    bb_first = fetch_scalar(d, "body_battery", "first")
    bb_last  = fetch_scalar(d, "body_battery", "last")
    bb = f"{fmt_int(bb_first)} → {fmt_int(bb_last)}" if bb_first or bb_last else "—"
    return {
        "resting_hr":   fmt_bpm(fetch_scalar(d, "resting_hr",   "last")),
        "steps":        fmt_int(fetch_scalar(d, "steps",        "sum")),
        "body_battery": bb,
        "avg_stress":   fmt_int(fetch_scalar(d, "stress",       "mean")),
    }


# ── note rendering ───────────────────────────────────────────────────────────

def render(d: date, activities: list[dict], health: dict) -> tuple[str, str]:
    if not activities:
        act_block = "## Activity\n_No activity recorded._"
    else:
        lines = "\n".join(format_activity_line(a) for a in activities)
        act_block = f"## Activity\n{lines}"

    health_block = (
        "## Health\n"
        "| | |\n"
        "|---|---|\n"
        f"| Resting HR   | {health['resting_hr']} |\n"
        f"| Steps        | {health['steps']} |\n"
        f"| Body Battery | {health['body_battery']} |\n"
        f"| Avg Stress   | {health['avg_stress']} |"
    )
    return act_block, health_block


AUTO_BLOCK_RE = re.compile(
    r"<!-- BEGIN AUTO: (\w+) -->.*?<!-- END AUTO -->",
    re.DOTALL,
)


def build_full_note(d: date, act_block: str, health_block: str) -> str:
    day_name = d.strftime("%A, %B %-d")
    return (
        f"---\ntags: daily\ndate: {d.isoformat()}\n---\n"
        f"# {day_name}\n\n"
        f"<!-- BEGIN AUTO: activity -->\n{act_block}\n<!-- END AUTO -->\n\n"
        f"<!-- BEGIN AUTO: health -->\n{health_block}\n<!-- END AUTO -->\n\n"
        f"## Journal\n\n"
        f"## Tomorrow\n- [ ] \n"
    )


def refresh_auto_blocks(existing: str, act_block: str, health_block: str) -> str:
    blocks = {"activity": act_block, "health": health_block}
    def repl(m):
        name = m.group(1)
        return f"<!-- BEGIN AUTO: {name} -->\n{blocks.get(name, m.group(0))}\n<!-- END AUTO -->"
    return AUTO_BLOCK_RE.sub(repl, existing)


# ── file I/O ─────────────────────────────────────────────────────────────────

def note_path(d: date) -> Path:
    return SPACE_PATH / "Journal" / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.isoformat()}.md"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except FileNotFoundError: pass
        raise


def write_note_for(d: date) -> None:
    activities = fetch_activities(d)
    health     = fetch_health(d)
    act_block, health_block = render(d, activities, health)
    path = note_path(d)

    if path.exists():
        existing = path.read_text()
        updated  = refresh_auto_blocks(existing, act_block, health_block)
        if updated == existing:
            print(f"[INFO] {path.name}: unchanged", flush=True)
            return
        atomic_write(path, updated)
        print(f"[INFO] {path.name}: auto blocks refreshed ({len(activities)} activities)", flush=True)
    else:
        atomic_write(path, build_full_note(d, act_block, health_block))
        print(f"[INFO] {path.name}: created ({len(activities)} activities)", flush=True)


def write_today_skeleton(d: date) -> None:
    """Create today's note if it doesn't exist yet — empty auto blocks so the
    Journal section is ready to type into."""
    path = note_path(d)
    if path.exists():
        return
    act_block, health_block = render(d, [], fetch_health(d))
    atomic_write(path, build_full_note(d, act_block, health_block))
    print(f"[INFO] {path.name}: skeleton created for today", flush=True)


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) > 1:
        d = date.fromisoformat(sys.argv[1])
        write_note_for(d)
        return
    today     = datetime.now(LOCAL_TZ).date()
    yesterday = today - timedelta(days=1)
    write_note_for(yesterday)
    write_today_skeleton(today)


if __name__ == "__main__":
    main()
