#!/usr/bin/env python3
import base64
import json
import os
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

SPACE_PATH = Path(os.environ.get("SILVERBULLET_SPACE", "/data/media/silverbullet"))
KOMGA_URL = os.environ.get("KOMGA_URL", "http://127.0.0.1:25600").rstrip("/")
KOMGA_USER = os.environ.get("KOMGA_USER", "")
KOMGA_PASS = os.environ.get("KOMGA_PASS", "")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "300"))

STATE_FILE = SPACE_PATH / ".komga_state.json"
COMICS_DIR = SPACE_PATH / "comics"
COVERS_DIR = COMICS_DIR / "covers"


def slug(text):
    text = re.sub(r"\s*\([^)]*\)", "", text)
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()


def auth_headers():
    creds = base64.b64encode(f"{KOMGA_USER}:{KOMGA_PASS}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


def komga_get(path):
    req = urllib.request.Request(f"{KOMGA_URL}{path}", headers=auth_headers())
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[ERROR] GET {path}: {e}", flush=True)
        return None


def get_all_series():
    series, page = [], 0
    while True:
        data = komga_get(f"/api/v1/series?page={page}&size=100")
        if not data:
            break
        series.extend(data.get("content", []))
        if data.get("last", True):
            break
        page += 1
    return series


def get_series_books(series_id):
    books, page = [], 0
    while True:
        data = komga_get(f"/api/v1/series/{series_id}/books?page={page}&size=100&sort=metadata.numberSort,asc")
        if not data:
            break
        books.extend(data.get("content", []))
        if data.get("last", True):
            break
        page += 1
    return books


def download_cover(series_id, series_slug):
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    out = COVERS_DIR / f"{series_slug}.jpg"
    cover_rel = f"{'../covers' if '.' in series_slug else 'covers'}/{series_slug}.jpg"
    if out.exists():
        return cover_rel
    req = urllib.request.Request(
        f"{KOMGA_URL}/api/v1/series/{series_id}/thumbnail",
        headers=auth_headers(),
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            out.write_bytes(resp.read())
        return cover_rel
    except Exception as e:
        print(f"[WARN] Cover download failed for {series_slug}: {e}", flush=True)
        return None


def series_display_title(s):
    title = s.get("metadata", {}).get("title", "") or s.get("name", "")
    if title == title.lower():
        # parent.child convention → "Parent - Child"
        if "." in title:
            parent, child = title.split(".", 1)
            return parent.replace("_", " ").title() + " - " + child.replace("_", " ").title()
        title = title.replace("_", " ").title()
    return title


def slug_from_series(s):
    """Use folder name directly if it matches our snake_case convention, else derive from title."""
    name = s.get("name", "")
    if re.match(r"^[a-z][a-z0-9_.]*$", name):
        return name
    return slug(series_display_title(s))


def note_path_for_slug(series_slug):
    if "." in series_slug:
        parent, child = series_slug.split(".", 1)
        return COMICS_DIR / parent / f"{child}.md"
    return COMICS_DIR / f"{series_slug}.md"


def create_series_note(series_id, series_slug, title, cover_rel, date_started):
    COMICS_DIR.mkdir(parents=True, exist_ok=True)
    note_path = note_path_for_slug(series_slug)
    note_path.parent.mkdir(parents=True, exist_ok=True)
    if note_path.exists():
        return
    cover_block = f"![cover]({cover_rel})\n\n" if cover_rel else ""
    content = (
        f"---\n"
        f"tags: comic\n"
        f'title: "{title}"\n'
        f'slug: "{series_slug}"\n'
        f"year: {date_started[:4]}\n"
        f'status: "💥 reading"\n'
        f"date_started: {date_started}\n"
        f"---\n"
        f"# {title}\n\n"
        f"{cover_block}"
        f"## thoughts\n- \n"
    )
    note_path.write_text(content)
    print(f"[INFO] Created: comics/{series_slug}.md", flush=True)


def append_volume(series_slug, vol_number, vol_title, read_date):
    note_path = note_path_for_slug(series_slug)
    if not note_path.exists():
        return
    content = note_path.read_text()
    section = f"\n## Vol. {vol_number} · {vol_title}\n- Finished: {read_date}\n"
    if "## thoughts" in content:
        content = content.replace("## thoughts", section + "\n## thoughts", 1)
    else:
        content += section
    note_path.write_text(content)
    print(f"[INFO] Appended Vol. {vol_number} to comics/{series_slug}.md", flush=True)


def mark_series_finished(series_slug, date_finished):
    note_path = note_path_for_slug(series_slug)
    if not note_path.exists():
        return
    content = note_path.read_text()
    content = content.replace(
        'status: "💥 reading"',
        f'status: "finished"\ndate_finished: {date_finished}',
    )
    note_path.write_text(content)
    print(f"[INFO] Marked finished: comics/{series_slug}.md", flush=True)


def make_reading_log_template(year):
    y = str(year)
    return (
        "---\ntags: reading_log\n---\n\n"
        "# Reading Log " + y + "\n\n"
        "## \U0001f4da Books\n\n"
        "${query[[\n"
        "  from p = index.pages(\"book\")\n"
        "  where p.year == " + y + "\n"
        "  order by (p.rating or 0) desc, p.name asc\n"
        "  select rating_line(p, \"📖\")\n"
        "]]}\n\n"
        "## \U0001f4a5 Comics\n\n"
        "${query[[\n"
        "  from p = index.pages(\"comic\")\n"
        "  where p.year == " + y + "\n"
        "  order by (p.rating or 0) desc, p.name asc\n"
        "  select rating_line(p, \"💥\")\n"
        "]]}\n"
    )


def update_reading_log(year):
    log_file = SPACE_PATH / "reading_logs" / f"reading_log_{year}.md"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    if not log_file.exists():
        log_file.write_text(make_reading_log_template(year))
        print(f"[INFO] Created reading log for {year}", flush=True)


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"bootstrapped": False, "series": {}, "finished": []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def handle_series_start(s):
    sid = s["id"]
    title = series_display_title(s)
    series_slug = slug_from_series(s)
    date_started = datetime.now().strftime("%Y-%m-%d")
    year = datetime.now().strftime("%Y")

    cover_rel = download_cover(sid, series_slug)
    create_series_note(sid, series_slug, title, cover_rel, date_started)
    update_reading_log(year)

    # Log any volumes already finished at start time
    books = get_series_books(sid)
    completed = [b for b in books if b.get("readProgress") and b["readProgress"].get("completed")]
    tracked = []
    for b in completed:
        meta = b.get("metadata", {})
        vol_num = meta.get("number", "?")
        vol_title = meta.get("title") or b.get("name", f"Vol. {vol_num}")
        read_date = (b["readProgress"].get("readDate") or date_started)[:10]
        append_volume(series_slug, vol_num, vol_title, read_date)
        tracked.append(b["id"])

    return {
        "slug": series_slug,
        "title": title,
        "date_started": date_started,
        "year": year,
        "volumes_read": s.get("booksReadCount", 0),
        "tracked_volume_ids": tracked,
    }


def handle_new_volumes(s, entry):
    books = get_series_books(s["id"])
    tracked = set(entry.get("tracked_volume_ids", []))
    new_vols = [b for b in books
                if b.get("readProgress") and b["readProgress"].get("completed")
                and b["id"] not in tracked]
    for b in new_vols:
        meta = b.get("metadata", {})
        vol_num = meta.get("number", "?")
        vol_title = meta.get("title") or b.get("name", f"Vol. {vol_num}")
        read_date = (b["readProgress"].get("readDate") or datetime.now().strftime("%Y-%m-%d"))[:10]
        append_volume(entry["slug"], vol_num, vol_title, read_date)
        tracked.add(b["id"])
        print(f"[INFO] New volume finished: {entry['slug']} Vol. {vol_num}", flush=True)
    entry["volumes_read"] = s.get("booksReadCount", 0)
    entry["tracked_volume_ids"] = list(tracked)


def poll():
    state = load_state()
    all_series = get_all_series()
    active = [s for s in all_series
              if s.get("booksReadCount", 0) > 0 or s.get("booksInProgressCount", 0) > 0]

    if not state.get("bootstrapped"):
        print(f"[INFO] Bootstrap: recording {len(active)} active series, no notes written", flush=True)
        for s in active:
            sid = s["id"]
            books_count = s.get("booksCount", 0)
            read_count = s.get("booksReadCount", 0)
            state["series"][sid] = {
                "slug": slug_from_series(s),
                "title": series_display_title(s),
                "bootstrapped": True,
                "volumes_read": read_count,
                "tracked_volume_ids": [],
            }
            if books_count > 0 and read_count >= books_count and sid not in state["finished"]:
                state["finished"].append(sid)
        state["bootstrapped"] = True
        save_state(state)
        return

    changed = False
    for s in active:
        sid = s["id"]
        read_count = s.get("booksReadCount", 0)
        books_count = s.get("booksCount", 0)

        if sid not in state["series"]:
            print(f"[INFO] New series: {series_display_title(s)}", flush=True)
            state["series"][sid] = handle_series_start(s)
            changed = True
        elif read_count > state["series"][sid].get("volumes_read", 0):
            handle_new_volumes(s, state["series"][sid])
            changed = True

        if books_count > 0 and read_count >= books_count and sid not in state["finished"]:
            entry = state["series"][sid]
            date_fin = datetime.now().strftime("%Y-%m-%d")
            mark_series_finished(entry["slug"], date_fin)
            state["finished"].append(sid)
            changed = True
            print(f"[INFO] Series finished: {entry['slug']}", flush=True)

    if changed:
        save_state(state)


if __name__ == "__main__":
    print(f"[INFO] Starting Komga poller — {KOMGA_URL} — every {POLL_INTERVAL}s", flush=True)
    while True:
        try:
            poll()
        except Exception as e:
            print(f"[ERROR] {e}", flush=True)
        time.sleep(POLL_INTERVAL)
