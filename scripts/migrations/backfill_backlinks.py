#!/usr/bin/env python3
"""One-shot: add reading_log/watch_log/journal frontmatter backlinks so the
SilverBullet graph stops treating query-surfaced notes as orphans. Idempotent.

Run:
    sudo nix-shell -p python3 --run "python3 /etc/nixos/scripts/migrations/backfill_backlinks.py"
"""
import re
from pathlib import Path

SPACE = Path("/data/media/silverbullet")

# Reading logs that cover a *span* of years — books whose year falls inside
# the span link to the span file, not a non-existent single-year file.
SPAN_LOGS = {
    2020: "reading_logs/reading_log_2020-2022",
    2021: "reading_logs/reading_log_2020-2022",
    2022: "reading_logs/reading_log_2020-2022",
}


def frontmatter_body(text):
    """Return the frontmatter body (between the `---` fences) or None."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    return text[4:end]


def has_field(fm, key):
    return re.search(rf"^{re.escape(key)}\s*:", fm, re.MULTILINE) is not None


def read_year(fm, key):
    """Extract YYYY from `year: 2023` or `watched: 2026-09-14`."""
    m = re.search(rf"^{re.escape(key)}\s*:\s*[\"']?(\d{{4}})", fm, re.MULTILINE)
    return int(m.group(1)) if m else None


def inject_after(text, anchor_key, new_line):
    """Insert `new_line` on the line right after the frontmatter line with
    key `anchor_key`. Falls back to inserting before the closing `---`."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(anchor_key)}\s*:", line):
            lines.insert(i + 1, new_line)
            return "\n".join(lines)
    for i, line in enumerate(lines[1:], start=1):
        if line == "---":
            lines.insert(i, new_line)
            return "\n".join(lines)
    return text


def process(path, key, target_fn, year_sources):
    text = path.read_text()
    fm = frontmatter_body(text)
    if fm is None:
        return "skip:no-frontmatter"
    if has_field(fm, key):
        return "skip:already-set"
    year = None
    for k in year_sources:
        year = read_year(fm, k)
        if year:
            break
    if not year:
        return "skip:no-year"
    target = target_fn(year)
    new_line = f'{key}: "[[{target}]]"'
    new_text = inject_after(text, year_sources[0], new_line)
    if new_text == text:
        return "skip:no-insert-point"
    path.write_text(new_text)
    return f"ok:{year}"


def reading_log_target(y):
    return SPAN_LOGS.get(y, f"reading_logs/reading_log_{y}")


def watch_log_target(y):
    return f"watch_logs/watch_log_{y}"


def journal_target(y):
    return f"journal/{y}"


def tally_stats():
    stats = {}

    def tally(bucket, result):
        stats.setdefault(bucket, {}).setdefault(result, 0)
        stats[bucket][result] += 1

    return stats, tally


stats, tally = tally_stats()

# Books
for p in (SPACE / "books").rglob("*.md"):
    tally("books", process(p, "reading_log", reading_log_target,
                            ["year", "date_finished", "date_started"]))

# Comics
for p in (SPACE / "comics").rglob("*.md"):
    tally("comics", process(p, "reading_log", reading_log_target,
                             ["year", "date_finished", "date_started"]))

# Movies (skip index page)
for p in (SPACE / "movies").rglob("*.md"):
    if p.name == "movies.md":
        continue
    tally("movies", process(p, "watch_log", watch_log_target, ["watched", "year"]))

# Shows (skip index page)
for p in (SPACE / "shows").rglob("*.md"):
    if p.name == "shows.md":
        continue
    tally("shows", process(p, "watch_log", watch_log_target, ["started", "year"]))

# Daily notes — only files inside dated subdirs, not the yearly index (journal/YYYY.md)
for p in (SPACE / "journal").rglob("*.md"):
    if p.parent == SPACE / "journal":
        continue
    tally("daily", process(p, "journal", journal_target, ["year", "date"]))

# ── Catalog links: book/comic/movie/show → its category index page ────────────

CATALOG_TARGETS = {
    "books":  "books",
    "comics": "comics",
    "movies": "movies/movies",
    "shows":  "shows/shows",
}

# Anchor: insert after reading_log / watch_log if present, else after year
CATALOG_ANCHORS = {
    "books":  ["reading_log", "year"],
    "comics": ["reading_log", "year"],
    "movies": ["watch_log", "year"],
    "shows":  ["watch_log", "year"],
}


def process_catalog(path, subdir):
    text = path.read_text()
    fm = frontmatter_body(text)
    if fm is None:
        return "skip:no-frontmatter"
    if has_field(fm, "catalog"):
        return "skip:already-set"
    target = CATALOG_TARGETS[subdir]
    new_line = f'catalog: "[[{target}]]"'
    for anchor in CATALOG_ANCHORS[subdir]:
        if has_field(fm, anchor):
            new_text = inject_after(text, anchor, new_line)
            if new_text != text:
                path.write_text(new_text)
                return "ok"
    return "skip:no-anchor"


for subdir, index_name in [("books", "books.md"),
                            ("comics", "comics.md"),
                            ("movies", "movies.md"),
                            ("shows", "shows.md")]:
    for p in (SPACE / subdir).rglob("*.md"):
        if p.name == index_name:
            continue
        tally(f"{subdir}:catalog", process_catalog(p, subdir))


for bucket, results in stats.items():
    print(f"[{bucket}] {results}")
