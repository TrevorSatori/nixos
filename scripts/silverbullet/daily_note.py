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
    ("garmin", "motorcycle"):    "🏍️",
    ("garmin", "pornography"):   "🔞",
    ("komga", "comic"):          "💥",
}

# Aggregation: consecutive same-source/type/title events within this gap merge
# into one session. Simple algorithm — only compares to the immediately previous
# session (a different-title event between two same-title events splits them).
AGGREGATION_GAP_MIN = 15
# Sources whose events are already atomic (one real-world session per event) —
# don't merge Garmin activities (morning run vs. evening run must stay separate)
# and comic completions have no time data.
NON_AGGREGATED_SOURCES = {"garmin", "komga"}

# Audiobook notes live under books/, comic notes under comics/. Both are
# markdown with `tags: book` or `tags: comic` respectively; volume completions
# appended as "## Vol. N · <title>\n- Finished: YYYY-MM-DD" sections.
BOOKS_DIR  = Path(os.environ.get("SILVERBULLET_BOOKS_DIR",  "/data/media/silverbullet/books"))
COMICS_DIR = Path(os.environ.get("SILVERBULLET_COMICS_DIR", "/data/media/silverbullet/comics"))
MOVIES_DIR = Path(os.environ.get("SILVERBULLET_MOVIES_DIR", "/data/media/silverbullet/movies"))
SHOWS_DIR  = Path(os.environ.get("SILVERBULLET_SHOWS_DIR",  "/data/media/silverbullet/shows"))


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
    """Sessions for the day, assembled from one query per field.

    InfluxDB stores each field as its own series. Any Flux pipeline that
    brings `title` (string) and `duration_s` (int) back in a single response
    — via pivot, or via map/group/sort to force a common schema — transposes
    string values onto neighbouring rows. That is what silently dropped book
    titles from the activity table.

    So: one request per field, each returning a homogeneous stream, merged on
    (_time, source, type) here in Python where the join is explicit.
    """
    start, stop = day_range_utc(d)

    def fetch_field(field: str) -> dict[tuple, str]:
        flux = f'''from(bucket: "{ACTIVITY_BUCKET}")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "session" or r._measurement == "session_short")
  |> filter(fn: (r) => r._field == "{field}")
  |> keep(columns: ["_time", "_value", "source", "type"])'''
        out: dict[tuple, str] = {}
        for r in influx_query(ACTIVITY_BUCKET, flux):
            t = r.get("_time", "")
            if not t:
                continue
            out[(t, r.get("source", ""), r.get("type", ""))] = r.get("_value", "")
        return out

    durations = fetch_field("duration_s")
    titles    = fetch_field("title")

    out = []
    for key, raw_dur in durations.items():
        t, source, typ = key
        try:
            duration = int(float(raw_dur or 0))
        except ValueError:
            continue
        if duration <= 0:
            continue          # stop sentinel, not a real session
        try:
            ts_utc = datetime.strptime(
                t.rstrip("Z").split(".")[0], "%Y-%m-%dT%H:%M:%S"
            ).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        out.append({
            "time_local": ts_utc.astimezone(LOCAL_TZ),
            "title":      titles.get(key, ""),
            "duration_s": duration,
            "source":     source,
            "type":       typ,
        })
    out.sort(key=lambda a: a["time_local"])
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


FRONT_SEASON = re.compile(r'^season:\s*(\d+)', re.MULTILINE)
# "Series - s01e02 - Episode Title" → capture series + season
_SHOW_TITLE_RX = re.compile(r'^(.+?)\s*-\s*s(\d+)e(\d+)\s*-\s*(.+)$')


def _media_note_lookup() -> tuple[dict[str, str], dict[tuple[str, int], str]]:
    """Build (movies_by_title, shows_by_(title,season)) from note frontmatter."""
    movies: dict[str, str] = {}
    shows:  dict[tuple[str, int], str] = {}
    if MOVIES_DIR.exists():
        for p in MOVIES_DIR.glob("*.md"):
            if p.name == "movies.md":
                continue
            try: text = p.read_text()
            except Exception: continue
            m = FRONT_TITLE.search(text)
            if m:
                movies[m.group(1).strip()] = f"movies/{p.stem}"
    if SHOWS_DIR.exists():
        for p in SHOWS_DIR.glob("*.md"):
            if p.name == "shows.md":
                continue
            try: text = p.read_text()
            except Exception: continue
            t = FRONT_TITLE.search(text)
            s = FRONT_SEASON.search(text)
            if t and s:
                shows[(t.group(1).strip(), int(s.group(1)))] = f"shows/{p.stem}"
    return movies, shows


_media_cache: tuple[dict[str, str], dict[tuple[str, int], str]] | None = None


def _linkify_media(typ: str, title: str) -> str:
    """Wiki-link the title if a matching movie/show note exists, else plain."""
    global _media_cache
    if _media_cache is None:
        _media_cache = _media_note_lookup()
    movies, shows = _media_cache
    slug = None
    if typ == "movie":
        slug = movies.get(title)
    elif typ == "tv_episode":
        m = _SHOW_TITLE_RX.match(title)
        if m:
            series, season = m.group(1).strip(), int(m.group(2))
            ep_num, ep_title = int(m.group(3)), m.group(4).strip()
            season_slug = shows.get((series, season))
            if season_slug:
                # Deep-link to the episode's `## Episode N — Title` header
                anchor = f"Episode {ep_num} — {ep_title}" if ep_title else f"Episode {ep_num}"
                return f"[[{season_slug}#{anchor}|{title}]]"
    return f"[[{slug}|{title}]]" if slug else title


def _title_for(a: dict) -> str:
    """Resolve display title (wiki-linked if the source has notes)."""
    if a.get("note_slug"):
        return f"[[{a['note_slug']}|{a['title']}]]"
    if a["source"] in ("abs", "komga"):
        return _linkify_book(a["title"])
    if a["source"] == "jellyfin":
        return _linkify_media(a["type"], a["title"])
    # Garmin titles come straight from Connect and aren't run through
    # ACTIVITY_TYPE_MAP (which only rewrites the `type` tag). Mirror that
    # remap on the display title so the table doesn't contradict the icon.
    # Gated on the already-remapped type so an unmapped Garmin activity that
    # happens to mention the word is left alone.
    if a["source"] == "garmin" and a.get("type") == "pornography":
        return re.sub(r"\bsquash\b", "Pornography", a["title"], flags=re.I)
    return a["title"]


def format_activity_line(a: dict, raw: bool = False) -> str:
    """Bullet-style row for the raw-events dropdown (single-event format).
    Plain time, no bold. Aggregated view uses render_activity_table instead."""
    icon  = ICON.get((a["source"], a["type"]), "📝")
    title = _title_for(a)
    if a.get("time_local"):
        hhmm    = a["time_local"].strftime("%H:%M")
        minutes = max(1, a["duration_s"] // 60)
        event_count = 1 if raw else a.get("event_count", 1)
        if event_count > 1 and a.get("end_time"):
            end_hhmm = a["end_time"].strftime("%H:%M")
            return f"- {hhmm}–{end_hhmm}  {icon}  {title}  ·  {minutes} min  ·  {event_count} events"
        return f"- {hhmm}  {icon}  {title}  ·  {minutes} min"
    if a.get("suffix"):
        return f"- {icon}  {title}  ·  {a['suffix']}"
    return f"- {icon}  {title}"


def render_activity_table(aggregated: list[dict]) -> str:
    """5-col table: Time | Type | Title | Duration | Events."""
    lines = [
        "| Time | Type | Title | Duration | Events |",
        "|:---|:---:|:---|:---:|:---:|",
    ]
    for a in aggregated:
        icon  = ICON.get((a["source"], a["type"]), "📝")
        title = _title_for(a)
        if a.get("time_local"):
            hhmm = a["time_local"].strftime("%H:%M")
            if a.get("event_count", 1) > 1 and a.get("end_time"):
                time_str = f"{hhmm}–{a['end_time'].strftime('%H:%M')}"
            else:
                time_str = hhmm
        else:
            time_str = "—"
        if a.get("duration_s") and a["duration_s"] > 0:
            dur = _fmt_dur(a["duration_s"])
        elif a.get("suffix"):
            dur = a["suffix"]
        else:
            dur = ""
        ec = a.get("event_count", 1)
        events = str(ec) if ec > 1 else ""
        lines.append(f"| {time_str} | {icon} | {title} | {dur} | {events} |")
    return "\n".join(lines)


def aggregate_activities(activities: list[dict]) -> list[dict]:
    """Merge consecutive same-source/type/title events within AGGREGATION_GAP_MIN.
    Simple algorithm: only compares to immediately previous session. See rule
    table in NON_AGGREGATED_SOURCES comment for which sources bypass merging."""
    epoch = datetime.min.replace(tzinfo=LOCAL_TZ)
    sorted_acts = sorted(activities, key=lambda a: a.get("time_local") or epoch)
    out: list[dict] = []
    for a in sorted_acts:
        # date-only events (comic completions) or missing time — pass through
        if not a.get("time_local"):
            out.append({**a, "end_time": None, "event_count": 1})
            continue
        end_time = a["time_local"] + timedelta(seconds=a["duration_s"])
        should_merge = (
            out
            and a["source"] not in NON_AGGREGATED_SOURCES
            and out[-1].get("end_time") is not None
            and out[-1].get("source") == a["source"]
            and out[-1].get("type")   == a["type"]
            and out[-1].get("title")  == a["title"]
            and (a["time_local"] - out[-1]["end_time"]).total_seconds() / 60 < AGGREGATION_GAP_MIN
        )
        if should_merge:
            out[-1]["end_time"]    = end_time
            out[-1]["duration_s"] += a["duration_s"]
            out[-1]["event_count"] += 1
        else:
            out.append({**a, "end_time": end_time, "event_count": 1})
    return out


def _fmt_dur(sec: int) -> str:
    if sec >= 3600:
        return f"{sec / 3600:.1f} hr"
    return f"{max(1, sec // 60)} min"


def build_summary_line(activities: list[dict]) -> str:
    """Per-(source,type) totals, alphabetical by source, with grand total."""
    totals: dict[tuple[str, str], int] = {}
    for a in activities:
        if not a.get("time_local"):
            continue  # comic completions have no meaningful duration
        totals[(a["source"], a["type"])] = totals.get((a["source"], a["type"]), 0) + a["duration_s"]
    if not totals:
        return ""
    parts = []
    for (source, typ), sec in sorted(totals.items()):
        icon = ICON.get((source, typ), "📝")
        parts.append(f"{icon} {_fmt_dur(sec)}")
    grand = sum(totals.values())
    return f"**Today**  ·  " + "  ·  ".join(parts) + f"  ·  **{_fmt_dur(grand)}**"


def render_activity_body(activities: list[dict]) -> str:
    """Full body of ## Activity: blank line + summary + blank line + aggregated
    list + optional collapsible raw-events block. Blank line at the start puts
    breathing room between the H2 heading and the summary. The <details> block
    keeps its contents contiguous (no blank lines inside — CommonMark closes
    HTML blocks on blank lines)."""
    if not activities:
        return "_No activity recorded._"
    aggregated = aggregate_activities(activities)
    summary    = build_summary_line(activities)

    parts: list[str] = [""]  # leading blank line under ## Activity
    if summary:
        parts.append(summary)
        parts.append("")
    parts.append(render_activity_table(aggregated))

    if len(activities) > len(aggregated):
        parts.append("")
        # Note the trailing \n on each contiguous line inside <details>:
        # blank lines inside would terminate the HTML block per CommonMark.
        raw_bullets = "\n".join(format_activity_line(a, raw=True) for a in activities)
        parts.append(
            f"<details><summary>Show raw events ({len(activities)})</summary>\n"
            f"{raw_bullets}\n"
            "</details>"
        )
    return "\n".join(parts)


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


def fetch_scalar_tagged(d: date, metric: str, tag_key: str) -> str | None:
    """Return the value of `tag_key` from the last biometric point matching
    (metric) within date d. Used to pull enum-ish string tags like
    training_readiness.status back out of InfluxDB."""
    start, stop = day_range_utc(d)
    flux = f'''from(bucket: "{BIOMETRICS_BUCKET}")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "biometric" and r.metric == "{metric}")
  |> last()
  |> keep(columns: ["{tag_key}"])'''
    rows = influx_query(BIOMETRICS_BUCKET, flux)
    if not rows:
        return None
    val = rows[0].get(tag_key)
    return val if val not in (None, "") else None


def fetch_health(d: date) -> dict:
    sleep_score = fetch_scalar(d, "sleep_score",      "last")
    sleep_dur   = fetch_scalar(d, "sleep_duration_s", "last")
    hrv         = fetch_scalar(d, "hrv_overnight",    "last")
    rhr         = fetch_scalar(d, "resting_hr",       "last")
    ready_score = fetch_scalar(d, "training_readiness", "last")
    ready_lvl   = fetch_scalar_tagged(d, "training_readiness", "status")
    resp        = fetch_scalar(d, "respiration_sleep_avg", "last")
    bb_first    = fetch_scalar(d, "body_battery",     "first")
    bb_last     = fetch_scalar(d, "body_battery",     "last")
    return {
        "sleep_score":    _fmt_num(sleep_score),
        "sleep_duration": _fmt_hm(sleep_dur),
        "hrv":            _fmt_num(hrv),
        "rhr":            _fmt_num(rhr),
        "ready_score":    _fmt_num(ready_score),
        "ready_level":    ready_lvl,
        "respiration":    _fmt_num(resp, decimals=0),
        "body_battery":   f"{fmt_int(bb_first)} → {fmt_int(bb_last)}" if (bb_first or bb_last) else None,
        "steps":          fmt_int(fetch_scalar(d, "steps",  "sum")),
        "stress":         _fmt_num(fetch_scalar(d, "stress", "mean")),
    }


def _fmt_num(s: str | None, decimals: int = 0) -> str | None:
    if s is None:
        return None
    try:
        f = float(s)
        return f"{int(round(f))}" if decimals == 0 else f"{f:.{decimals}f}"
    except (TypeError, ValueError):
        return None


def _fmt_hm(seconds: str | None) -> str | None:
    if seconds is None:
        return None
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return None
    h, rem = divmod(total, 3600)
    m = rem // 60
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


# ── note rendering ───────────────────────────────────────────────────────────

# These two H2 sections are managed by the writer. Their titles must not be
# renamed by the user — the writer identifies them by heading text and rewrites
# their body in place. Any other section (## Journal, ## Tomorrow, whatever the
# user adds) is left untouched.
AUTO_SECTIONS = ("📊 Activity", "❤️ Health")


def render_health_body(h: dict) -> str:
    """2-column table: emoji + label | value. Rows with no data are omitted.
    Falls back to a pending-sync note if nothing is available yet."""
    rows: list[tuple[str, str]] = []

    if h["sleep_score"] or h["sleep_duration"]:
        val = h["sleep_score"] or "—"
        if h["sleep_duration"]:
            val = f"{val} · {h['sleep_duration']}"
        rows.append(("😴 Sleep", val))

    if h["hrv"]:
        rows.append(("🫀 HRV", h["hrv"]))

    if h["rhr"]:
        rows.append(("💓 RHR", h["rhr"]))

    if h["ready_score"] or h["ready_level"]:
        val = h["ready_score"] or "—"
        if h["ready_level"]:
            val = f"{val} · {h['ready_level']}"
        rows.append(("🎯 Readiness", val))

    if h["respiration"]:
        rows.append(("🫁 Resp", h["respiration"]))

    if h["body_battery"]:
        rows.append(("🔋 Body Battery", h["body_battery"]))

    if h["steps"] and h["steps"] != "—":
        rows.append(("👟 Steps", h["steps"]))

    if h["stress"]:
        rows.append(("😌 Avg Stress", h["stress"]))

    if not rows:
        return "_pending sync_"

    lines = ["| | |", "|---|---|"]
    for label, val in rows:
        lines.append(f"| {label} | {val} |")
    return "\n".join(lines)


def render_bodies(d: date, activities: list[dict], health: dict) -> tuple[str, str]:
    act_body    = render_activity_body(activities)
    health_body = render_health_body(health)
    return act_body, health_body


VITAMINS_TEMPLATE = SPACE_PATH / "configs" / "vitamins.md"


def _vitamins_body() -> str:
    """Read the vitamins template file and return contents *without* any YAML
    frontmatter (frontmatter is meta about the template page itself; the
    daily-note writer only wants the body). Only used when creating a *new*
    daily note — never overwrites an existing note's Vitamins section."""
    try:
        text = VITAMINS_TEMPLATE.read_text()
    except FileNotFoundError:
        return ""
    # Strip a leading YAML frontmatter block if present
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5:]
    return text.strip()


ROUTINE_TEMPLATE = SPACE_PATH / "life" / "routine.md"

# [day: daily] / [day: friday] / [day: mon,wed,fri]
_DAY_ATTR = re.compile(r"\[day:\s*([^\]]+)\]", re.I)
_SECTION_ATTR = re.compile(r"\[section:\s*([^\]]+)\]", re.I)
_ATTRS    = re.compile(r"\s*\[[a-z_]+:\s*[^\]]*\]", re.I)

_WEEKDAY_ALIASES = {
    "mon": "monday", "tue": "tuesday", "tues": "tuesday", "wed": "wednesday",
    "thu": "thursday", "thur": "thursday", "thurs": "thursday",
    "fri": "friday", "sat": "saturday", "sun": "sunday",
}


def _day_matches(spec: str, d: date) -> bool:
    """Does a [day: ...] spec fire on this date?"""
    weekday = d.strftime("%A").lower()
    for raw in spec.split(","):
        tok = raw.strip().lower()
        tok = _WEEKDAY_ALIASES.get(tok, tok)
        if tok in ("daily", "everyday", "every day") or tok == weekday:
            return True
    return False


def _parse_routine(d: date) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Return (chore_lines, sections) for this date.

    Structure decides the shape, not which heading a line sits under:

      * a bare `- ...` line with [day:]  -> a checkbox under "## ✅ Today"
      * a `#` header with [day:]         -> its child lines rendered inline
                                            under the heading named by
                                            [section: ...]

    Header blocks get no checkbox by design: a run is proven by the Garmin
    session in the Activity table, so ticking a box would be duplicate entry
    about something already recorded.

    `sections` is a list of (heading, lines) in the order each section first
    appears in routine.md — the file is the config, there is no separate
    ordering table to keep in sync.

    A header carrying [day:] but no [section:] is skipped and logged rather
    than defaulted, so a typo can't silently file a fishing trip under
    Training.
    """
    try:
        text = ROUTINE_TEMPLATE.read_text()
    except FileNotFoundError:
        return [], []

    chores: list[str] = []
    sections: dict[str, list[str]] = {}   # insertion-ordered
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _DAY_ATTR.search(line)

        # Column 0 only. An indented `### ...` is a fenced/indented code
        # sample — this page documents its own syntax, and lstrip() would
        # turn those examples into live rules.
        if m and line.startswith("#"):
            if _day_matches(m.group(1), d):
                sm = _SECTION_ATTR.search(line)
                if not sm:
                    title_dbg = _ATTRS.sub("", line.lstrip("#").strip()).strip()
                    print(f"[WARN] routine block {title_dbg!r} has [day:] but no "
                          f"[section:] — skipped", flush=True)
                else:
                    heading = sm.group(1).strip()
                    title = _ATTRS.sub("", line.lstrip("#").strip()).strip()
                    body: list[str] = [f"**{title}**"]
                    j = i + 1
                    while (j < len(lines)
                           and not lines[j].startswith("#")
                           and not lines[j].startswith("---")):
                        stripped = lines[j].rstrip()
                        if stripped.strip():
                            body.append(stripped)
                        j += 1
                    body.append("")
                    sections.setdefault(heading, []).extend(body)
                    i = j
                    continue

        elif m and line.startswith("-"):
            if _day_matches(m.group(1), d):
                name = _ATTRS.sub("", line.lstrip("- ").strip()).strip()
                if name:
                    chores.append(f"- [ ] {name}")

        i += 1

    ordered: list[tuple[str, list[str]]] = []
    for heading, body in sections.items():
        while body and body[-1] == "":
            body.pop()
        if body:
            ordered.append((heading, body))
    return chores, ordered


DIET_SCAFFOLD = """- **Breakfast:** 
- **Lunch:** 
- **Dinner:** 
- **Drinks:** 
- **Snacks:** """

DREAMS_SCAFFOLD = """### Dream 1


### Dream 2


### Dream 3
"""


# Live queries over tasks that live elsewhere (inbox.md and friends).
# Nothing is copied into the note — checking a box writes [x] back to the
# source line, and `/done` stamps [completed: YYYY-MM-DD].
SCHEDULED_QUERY = """${query[[
  from t = index.tasks()
  where table.includes(t.itags, "tasks")
    and not t.done and t.due != nil and t.due <= "%DATE%"
  order by t.due
  select templates.taskItem(t)
]]}"""

COMPLETED_QUERY = """${query[[
  from t = index.tasks()
  where table.includes(t.itags, "tasks")
    and t.done and t.completed == "%DATE%"
  order by t.name
  select templates.taskItem(t)
]]}"""


def build_full_note(d: date, act_body: str, health_body: str) -> str:
    day_name  = d.strftime("%A, %B %-d")
    vitamins  = _vitamins_body()
    vit_block = f"\n{vitamins}\n" if vitamins else "\n"

    # Recurring items are materialised once, at creation. refresh_auto_sections
    # only rewrites Activity and Health, so re-running can never untick a box.
    chores, routine_sections = _parse_routine(d)
    # No trailing blank checkbox: capture belongs in inbox.md, where the
    # board and the archiver can both see it.
    today_block   = "\n".join(chores) if chores else ""
    routine_block = "".join(
        f"\n## {heading}\n" + "\n".join(body) + "\n"
        for heading, body in routine_sections
    )
    return (
        f"---\ncreated: {datetime.now(LOCAL_TZ).isoformat(timespec='seconds')}\ntags: daily\ndate: {d.isoformat()}\nyear: {d.year}\njournal: \"[[journal/{d.year}]]\"\n---\n"
        f"# {day_name}\n\n"
        f"## ✅ Today\n{today_block}\n\n"
        f"{SCHEDULED_QUERY.replace('%DATE%', d.isoformat())}\n\n"
        f"## ✍️ Journal\n\n\n"
        f"## 🍽️ Diet\n{DIET_SCAFFOLD}\n\n"
        f"## 💊 Vitamins\n{vit_block}\n"
        f"{routine_block}"
        f"## 📊 Activity\n{act_body}\n\n"
        f"## ❤️ Health\n{health_body}\n\n"
        f"## 🌙 Dreams\n{DREAMS_SCAFFOLD}"
    )


EMPTY_HEALTH = {
    "sleep_score":    None,
    "sleep_duration": None,
    "hrv":            None,
    "rhr":            None,
    "ready_score":    None,
    "ready_level":    None,
    "respiration":    None,
    "body_battery":   None,
    "steps":          None,
    "stress":         None,
}


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
title: "Daily Notes {year}"
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
    # Section titles include their emoji prefix — must match what
    # build_full_note wrote, otherwise the regex sub is a no-op.
    out = replace_section_body(existing,  "📊 Activity", act_body)
    out = replace_section_body(out,       "❤️ Health",   health_body)
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
    """Create the note for `d` if it doesn't exist yet — empty auto sections so
    the Journal section is ready to type into. Health values stay blank until
    the day is over and the writer regenerates with real daily aggregates.

    Used for both today and tomorrow; a no-op when the note already exists, so
    it can never clobber anything you've written."""
    path = note_path(d)
    if path.exists():
        return
    act_body, health_body = render_bodies(d, [], EMPTY_HEALTH)
    atomic_write(path, build_full_note(d, act_body, health_body))
    print(f"[INFO] {path.name}: skeleton created for today", flush=True)
    ensure_year_index(d.year)


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("date", nargs="?", help="ISO date to regen (YYYY-MM-DD). Default: skeleton for today.")
    p.add_argument("--backfill", type=int, metavar="N",
                   help="Refresh the last N days' notes (creates any missing).")
    args = p.parse_args()

    today = datetime.now(LOCAL_TZ).date()

    if args.backfill is not None:
        for i in range(args.backfill):
            write_note_for(today - timedelta(days=i))
        return

    if args.date:
        write_note_for(date.fromisoformat(args.date))
        return

    # Default: skeleton for today and tomorrow.
    #
    # Tomorrow's note has to exist before midnight for two reasons: you can
    # open it to jot things for the morning, and the archiver targets the
    # note matching a task's completion date — if that note doesn't exist yet
    # the task is skipped and left in the inbox.
    write_today_skeleton(today)
    write_today_skeleton(today + timedelta(days=1))


if __name__ == "__main__":
    main()
