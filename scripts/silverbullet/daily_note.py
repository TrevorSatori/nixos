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
    ("komga", "comic"):          "💥",
}

# Audiobook notes live under books/, comic notes under comics/. Both are
# markdown with `tags: book` or `tags: comic` respectively; volume completions
# appended as "## Vol. N · <title>\n- Finished: YYYY-MM-DD" sections.
BOOKS_DIR  = Path(os.environ.get("SILVERBULLET_BOOKS_DIR",  "/data/media/silverbullet/books"))
COMICS_DIR = Path(os.environ.get("SILVERBULLET_COMICS_DIR", "/data/media/silverbullet/comics"))


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


def _book_note_lookup() -> dict[str, str]:
    """Build {title: 'books/slug' | 'comics/slug'} from all book/comic notes."""
    out: dict[str, str] = {}
    for base_dir, prefix in [(BOOKS_DIR, "books"), (COMICS_DIR, "comics")]:
        if not base_dir.exists():
            continue
        for p in base_dir.glob("*.md"):
            try:
                text = p.read_text()
            except Exception:
                continue
            m = FRONT_TITLE.search(text)
            if m:
                out[m.group(1).strip()] = f"{prefix}/{p.stem}"
    return out


_book_notes_cache: dict[str, str] | None = None


def _linkify_book(title: str) -> str:
    """If a book/comic note exists with this title, return a wiki-link. Else the plain title."""
    global _book_notes_cache
    if _book_notes_cache is None:
        _book_notes_cache = _book_note_lookup()
    slug = _book_notes_cache.get(title)
    return f"[[{slug}|{title}]]" if slug else title


def format_activity_line(a: dict) -> str:
    icon = ICON.get((a["source"], a["type"]), "📝")
    if a.get("note_slug"):
        title = f"[[{a['note_slug']}|{a['title']}]]"
    elif a["source"] in ("abs", "komga"):
        title = _linkify_book(a["title"])
    else:
        title = a["title"]
    if a.get("time_local"):
        hhmm    = a["time_local"].strftime("%H:%M")
        minutes = max(1, a["duration_s"] // 60)
        return f"- {hhmm} {icon} {title} — {minutes} min"
    if a.get("suffix"):
        return f"- {icon} {title} — {a['suffix']}"
    return f"- {icon} {title}"


# ── comic completions (from silverbullet notes, not influx) ──────────────────

FRONT_TITLE  = re.compile(r'^title:\s*"?([^"\n]+)"?\s*$', re.MULTILINE)
FRONT_TAGS   = re.compile(r'^tags:\s*(.+)$',              re.MULTILINE)
FRONT_FIN    = re.compile(r'^date_finished:\s*(\S+)',     re.MULTILINE)
VOL_SECTION  = re.compile(
    r'^## Vol\.\s*(\S+)\s*·\s*([^\n]+)\n-\s*Finished:\s*(\S+)',
    re.MULTILINE,
)


def _fmt_vol_range(nums: list[str]) -> str:
    # Preserve order; produce "Vol. 3", "Vol. 1-6", "Vols. 2, 5, 7"
    if len(nums) == 1:
        return f"Vol. {nums[0]}"
    try:
        ints = [int(n) for n in nums]
        if ints == list(range(min(ints), max(ints) + 1)):
            return f"Vol. {min(ints)}–{max(ints)}"
    except ValueError:
        pass
    return "Vols. " + ", ".join(nums)


def fetch_comic_completions(d: date) -> list[dict]:
    """Scan silverbullet comics/*.md for comic activity on `d`. Collapses to one
    line per series: 'series completed (Vol. 1–6)' if the whole series finished,
    otherwise 'Vol. N' (or range) for volumes finished that day."""
    target = d.isoformat()
    out: list[dict] = []
    if not COMICS_DIR.exists():
        return out
    for p in COMICS_DIR.glob("*.md"):
        try:
            text = p.read_text()
        except Exception:
            continue
        tags_m = FRONT_TAGS.search(text)
        if not tags_m or "comic" not in tags_m.group(1).lower():
            continue
        title_m = FRONT_TITLE.search(text)
        title   = title_m.group(1) if title_m else p.stem

        vols_today = [num for num, _v_title, fin in VOL_SECTION.findall(text) if fin == target]
        series_done = bool(FRONT_FIN.search(text) and FRONT_FIN.search(text).group(1) == target)

        if not vols_today and not series_done:
            continue
        if series_done:
            suffix = f"series completed ({_fmt_vol_range(vols_today)})" if vols_today else "series completed"
        else:
            suffix = _fmt_vol_range(vols_today)

        out.append({
            "time_local": None,
            "title":      title,
            "suffix":     suffix,
            "source":     "komga",
            "type":       "comic",
            "note_slug":  f"comics/{p.stem}",
        })
    return out


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

# These two H2 sections are managed by the writer. Their titles must not be
# renamed by the user — the writer identifies them by heading text and rewrites
# their body in place. Any other section (## Journal, ## Tomorrow, whatever the
# user adds) is left untouched.
AUTO_SECTIONS = ("Activity", "Health")


def render_bodies(d: date, activities: list[dict], health: dict) -> tuple[str, str]:
    if not activities:
        act_body = "_No activity recorded._"
    else:
        act_body = "\n".join(format_activity_line(a) for a in activities)
    health_body = (
        "| | |\n"
        "|---|---|\n"
        f"| Resting HR   | {health['resting_hr']} |\n"
        f"| Steps        | {health['steps']} |\n"
        f"| Body Battery | {health['body_battery']} |\n"
        f"| Avg Stress   | {health['avg_stress']} |"
    )
    return act_body, health_body


VITAMINS_TEMPLATE = SPACE_PATH / "configs" / "vitamins.md"


def _vitamins_body() -> str:
    """Read the vitamins template file; return its contents (or empty if
    missing). Only used when creating a *new* daily note — never overwrites
    an existing note's Vitamins section."""
    try:
        return VITAMINS_TEMPLATE.read_text().strip()
    except FileNotFoundError:
        return ""


def build_full_note(d: date, act_body: str, health_body: str) -> str:
    day_name  = d.strftime("%A, %B %-d")
    vitamins  = _vitamins_body()
    vit_block = f"\n{vitamins}\n" if vitamins else "\n"
    return (
        f"---\ntags: daily\ndate: {d.isoformat()}\nyear: {d.year}\n---\n"
        f"# {day_name}\n\n"
        f"## Activity\n{act_body}\n\n"
        f"## Health\n{health_body}\n\n"
        f"## Vitamins\n{vit_block}\n"
        f"## Journal\n"
    )


EMPTY_HEALTH = {"resting_hr": "—", "steps": "—", "body_battery": "—", "avg_stress": "—"}


def replace_section_body(text: str, title: str, new_body: str) -> str:
    """Replace the body of the '## {title}' section in `text`, keeping the
    heading. If the section isn't found, return `text` unchanged."""
    pattern = re.compile(
        rf"^(## {re.escape(title)}\n).*?(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    return pattern.sub(rf"\1{new_body}\n\n", text, count=1)


YEAR_INDEX_TEMPLATE = '''---
tags: yearly_journal
year: {year}
---
# Daily Notes {year}

${{query[[
  from p = index.pages("daily")
  where p.year == {year}
  order by p.date asc
  select "- [[" .. p.name .. "|" .. p.date .. "]]"
]]}}
'''


def ensure_year_index(year: int) -> None:
    """Ensure Journal/YYYY.md exists with a live-query listing of that year's notes."""
    path = SPACE_PATH / "journal" / f"{year:04d}.md"
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, YEAR_INDEX_TEMPLATE.format(year=year))
    print(f"[INFO] Journal/{year}.md: year-index page created", flush=True)


def refresh_auto_sections(existing: str, act_body: str, health_body: str) -> str:
    out = replace_section_body(existing,  "Activity", act_body)
    out = replace_section_body(out,       "Health",   health_body)
    return out


# ── file I/O ─────────────────────────────────────────────────────────────────

def note_path(d: date) -> Path:
    return SPACE_PATH / "journal" / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.isoformat()}.md"


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
    # Time-based sessions first (chronological), then date-only comic completions.
    activities = fetch_activities(d) + fetch_comic_completions(d)
    health     = fetch_health(d)
    act_body, health_body = render_bodies(d, activities, health)
    path = note_path(d)

    if path.exists():
        existing = path.read_text()
        updated  = refresh_auto_sections(existing, act_body, health_body)
        if updated == existing:
            print(f"[INFO] {path.name}: unchanged", flush=True)
        else:
            atomic_write(path, updated)
            print(f"[INFO] {path.name}: refreshed ({len(activities)} activities)", flush=True)
    else:
        atomic_write(path, build_full_note(d, act_body, health_body))
        print(f"[INFO] {path.name}: created ({len(activities)} activities)", flush=True)
    ensure_year_index(d.year)


def write_today_skeleton(d: date) -> None:
    """Create today's note if it doesn't exist yet — empty auto sections so the
    Journal section is ready to type into. Health values stay blank until the
    day is over and the writer regenerates with real daily aggregates."""
    path = note_path(d)
    if path.exists():
        return
    act_body, health_body = render_bodies(d, [], EMPTY_HEALTH)
    atomic_write(path, build_full_note(d, act_body, health_body))
    print(f"[INFO] {path.name}: skeleton created for today", flush=True)
    ensure_year_index(d.year)


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
