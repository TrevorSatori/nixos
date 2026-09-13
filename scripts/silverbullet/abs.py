#!/usr/bin/env python3
import json
import os
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

SPACE_PATH = Path(os.environ.get("SILVERBULLET_SPACE", "/data/media/silverbullet"))
ABS_URL = os.environ.get("ABS_URL", "http://127.0.0.1:13378").rstrip("/")
ABS_API_KEY = os.environ.get("ABS_API_KEY", "")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "300"))
FINISH_THRESHOLD = 0.98

STATE_FILE = SPACE_PATH / ".abs_state.json"
BOOKS_DIR = SPACE_PATH / "books"
COVERS_DIR = BOOKS_DIR / "covers"


def slug(text):
    text = re.sub(r"\s*\([^)]*\)", "", text)
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()


def make_book_slug(title, author):
    return f"{slug(title)}.{slug(author)}"


def abs_get(path):
    url = f"{ABS_URL}{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {ABS_API_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[ERROR] GET {path}: {e}", flush=True)
        return None


def download_cover(item_id, book_slug):
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    out = COVERS_DIR / f"{book_slug}.jpg"
    if out.exists():
        return f"covers/{book_slug}.jpg"
    req = urllib.request.Request(
        f"{ABS_URL}/api/items/{item_id}/cover",
        headers={"Authorization": f"Bearer {ABS_API_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            out.write_bytes(resp.read())
        return f"covers/{book_slug}.jpg"
    except Exception as e:
        print(f"[WARN] cover download failed: {e}", flush=True)
        return None


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"bootstrapped": False, "started": {}, "finished": []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def get_sessions():
    data = abs_get("/api/me/listening-sessions?desc=1&itemsPerPage=100")
    if not data:
        return {}
    items = {}
    for s in data.get("sessions", []):
        iid = s.get("libraryItemId")
        if not iid:
            continue
        dur = s.get("duration") or 0
        cur = s.get("currentTime") or 0
        prog = (cur / dur) if dur > 0 else (s.get("progress") or 0)
        if iid not in items or prog > items[iid]["progress"]:
            items[iid] = {
                "progress": prog,
                "displayTitle": s.get("displayTitle") or "",
                "displayAuthor": s.get("displayAuthor") or "",
            }
    return items


def get_metadata(item_id):
    data = abs_get(f"/api/items/{item_id}?expanded=1")
    if not data:
        return {}
    meta = data.get("media", {}).get("metadata", {})
    duration = data.get("media", {}).get("duration") or 0
    return {
        "title": meta.get("title") or "",
        "author": meta.get("authorName") or meta.get("author") or "",
        "narrator": meta.get("narratorName") or meta.get("narrator") or "",
        "duration_hours": duration / 3600 if duration else 0,
    }


def write_book_note(book_slug, title, author, narrator, duration_hours, cover_rel, year, date_started):
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    book_file = BOOKS_DIR / f"{book_slug}.md"
    if book_file.exists():
        return
    narrator_meta = f'\nnarrator: "{narrator}"' if narrator else ""
    duration_meta = f"\nduration_hours: {duration_hours:.1f}" if duration_hours else ""
    cover_block = f"![cover]({cover_rel})\n\n" if cover_rel else ""
    narrator_line = f"**Narrator:** {narrator}\n" if narrator else ""
    duration_line = f"**Duration:** {duration_hours:.1f} hrs\n" if duration_hours else ""
    content = (
        f"---\n"
        f"tags: book\n"
        f'title: "{title}"\n'
        f'author: "{author}"{narrator_meta}\n'
        f'slug: "{book_slug}"\n'
        f"year: {year}\n"
        f'status: "🎧 listening"\n'
        f"date_started: {date_started}{duration_meta}\n"
        f"---\n"
        f"# {title}\n\n"
        f"{cover_block}"
        f"**Author:** {author}\n"
        f"{narrator_line}"
        f"{duration_line}\n"
        f"## thoughts\n- \n"
    )
    book_file.write_text(content)
    print(f"[INFO] Created: books/{book_slug}.md", flush=True)


def mark_book_finished(book_slug, date_finished):
    book_file = BOOKS_DIR / f"{book_slug}.md"
    if not book_file.exists():
        return
    content = book_file.read_text()
    content = content.replace(
        'status: "🎧 listening"',
        f'status: "🌟 finished"\ndate_finished: {date_finished}',
    )
    book_file.write_text(content)
    print(f"[INFO] Marked finished: books/{book_slug}.md", flush=True)


def make_reading_log_template(year):
    y = str(year)
    return (
        "---\ntags: reading_log\n---\n\n"
        "# Reading Log " + y + "\n\n"
        "## \U0001f4da Books\n\n"
        "${query[[\n"
        "  from p = index.pages(\"book\")\n"
        "  where p.year == " + y + "\n"
        "  order by p.name asc\n"
        "  select \"- \" .. (p.status or \"\U0001f4d6\") .. \" [[\" .. p.name .. \"|\" .. (p.title or p.name) .. \"]]\"\n"
        "]]}\n\n"
        "## \U0001f4a5 Comics\n\n"
        "${query[[\n"
        "  from p = index.pages(\"comic\")\n"
        "  where p.year == " + y + "\n"
        "  order by p.name asc\n"
        "  select \"- \" .. (p.status or \"\U0001f4a5\") .. \" [[\" .. p.name .. \"|\" .. (p.title or p.name) .. \"]]\"\n"
        "]]}\n"
    )


def update_reading_log(year):
    log_file = SPACE_PATH / "reading_logs" / f"reading_log_{year}.md"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    if not log_file.exists():
        log_file.write_text(make_reading_log_template(year))
        print(f"[INFO] Created reading log for {year}", flush=True)


def handle_start(item_id, session_info):
    now = datetime.now()
    date_started = now.strftime("%Y-%m-%d")
    year = now.strftime("%Y")

    meta = get_metadata(item_id)
    title = meta.get("title") or session_info["displayTitle"] or "Unknown"
    author = meta.get("author") or session_info.get("displayAuthor") or "Unknown"
    narrator = meta.get("narrator") or ""
    duration_hours = meta.get("duration_hours") or 0

    book_slug = make_book_slug(title, author)
    cover_rel = download_cover(item_id, book_slug)
    write_book_note(book_slug, title, author, narrator, duration_hours, cover_rel, year, date_started)
    update_reading_log(year)

    return {"slug": book_slug, "title": title, "date_started": date_started, "year": year}


def handle_finish(item_id, entry):
    date_finished = datetime.now().strftime("%Y-%m-%d")
    mark_book_finished(entry.get("slug", ""), date_finished)


def poll():
    state = load_state()
    sessions = get_sessions()
    if not sessions:
        return

    if not state.get("bootstrapped"):
        print(f"[INFO] Bootstrap: recording {len(sessions)} existing sessions, no notes written", flush=True)
        for item_id, info in sessions.items():
            if item_id not in state["started"]:
                state["started"][item_id] = {"slug": slug(info["displayTitle"]), "bootstrapped": True}
            if info["progress"] >= FINISH_THRESHOLD and item_id not in state["finished"]:
                state["finished"].append(item_id)
        state["bootstrapped"] = True
        save_state(state)
        return

    changed = False
    for item_id, info in sessions.items():
        prog = info["progress"]
        if item_id not in state["started"]:
            print(f"[INFO] Book started: {info['displayTitle']}", flush=True)
            entry = handle_start(item_id, info)
            state["started"][item_id] = entry
            changed = True
        if prog >= FINISH_THRESHOLD and item_id not in state["finished"]:
            print(f"[INFO] Book finished: {info['displayTitle']}", flush=True)
            handle_finish(item_id, state["started"].get(item_id, {}))
            state["finished"].append(item_id)
            changed = True

    if changed:
        save_state(state)


if __name__ == "__main__":
    print(f"[INFO] Starting ABS poller — {ABS_URL} — every {POLL_INTERVAL}s", flush=True)
    while True:
        try:
            poll()
        except Exception as e:
            print(f"[ERROR] {e}", flush=True)
        time.sleep(POLL_INTERVAL)
