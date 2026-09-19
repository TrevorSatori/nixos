#!/usr/bin/env python3
"""Archive completed tasks from the SilverBullet inbox into daily notes.

A finished task is moved out of `inbox.md` and appended to the daily note for
the date it was *completed* — not the date this script runs — so a backlog
drains to the right days instead of collapsing into today.

Design constraints, all deliberate:

  * Write-verify-then-delete. The line is appended, the target is re-read to
    confirm it landed, and only then is it removed from the inbox. A crash at
    any point leaves a duplicate, never a hole.
  * Missing target note => skip and leave in the inbox. Creating notes is
    daily_note.py's job; an archiver that silently creates files is how you
    end up with surprise directories.
  * Only `- [x] ... [completed: YYYY-MM-DD]` lines under `## Open` are
    touched. The stamp is written by configs/tasks.md, and only on pages
    tagged `tasks` — so routine chores and vitamins in daily notes can never
    match, and stay invisible as intended.
  * Runs as `media`. Root-written files land 0600 and vanish from the SB UI.

Dry run by default. Pass --apply to actually move anything.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

SPACE = Path("/data/media/silverbullet")
INBOX = SPACE / "inbox.md"

OPEN_HEADING = re.compile(r"^##\s+Open\s*$", re.I)
ANY_HEADING = re.compile(r"^##\s+")
DONE_TASK = re.compile(r"^-\s+\[[xX]\]\s+(.*)$")
COMPLETED_ATTR = re.compile(r"\[completed:\s*(\d{4}-\d{2}-\d{2})\s*\]")
TODAY_HEADING = re.compile(r"^##\s+✅\s+Today\s*$", re.M)


def note_path(d: date) -> Path:
    return SPACE / "journal" / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.isoformat()}.md"


def task_name(line: str) -> str:
    """Line text minus the checkbox and any attributes — used to dedupe."""
    m = DONE_TASK.match(line)
    body = m.group(1) if m else line
    body = COMPLETED_ATTR.sub("", body)
    body = re.sub(r"\[due:\s*[^\]]*\]", "", body)
    return " ".join(body.split())


def parse_inbox(text: str) -> tuple[list[str], int, int]:
    """Return (lines, open_start, open_end) delimiting the ## Open section."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if OPEN_HEADING.match(line):
            start = i + 1
            break
    if start is None:
        return lines, -1, -1

    end = len(lines)
    for i in range(start, len(lines)):
        if ANY_HEADING.match(lines[i]):
            end = i
            break
    return lines, start, end


def insert_into_note(text: str, line: str) -> str | None:
    """Append `line` under the note's '## ✅ Today' heading.

    Returns None when the heading is absent — caller treats that as a skip
    rather than guessing where the task belongs.
    """
    lines = text.splitlines()
    start = None
    for i, l in enumerate(lines):
        if TODAY_HEADING.match(l):
            start = i + 1
            break
    if start is None:
        return None

    end = len(lines)
    for i in range(start, len(lines)):
        if ANY_HEADING.match(lines[i]):
            end = i
            break

    # place after the last existing task, before any trailing blank lines
    insert_at = start
    for i in range(start, end):
        if lines[i].strip():
            insert_at = i + 1

    lines.insert(insert_at, line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually move tasks (default is a dry run)")
    args = ap.parse_args()

    if not INBOX.exists():
        print(f"[ERROR] inbox not found: {INBOX}", file=sys.stderr)
        return 1

    text = INBOX.read_text(encoding="utf-8")
    lines, start, end = parse_inbox(text)
    if start == -1:
        print("[ERROR] no '## Open' section in inbox — nothing to do", file=sys.stderr)
        return 1

    moved: list[tuple[int, str, Path]] = []
    skipped: list[tuple[str, str]] = []

    for i in range(start, end):
        line = lines[i]
        if not DONE_TASK.match(line):
            continue
        m = COMPLETED_ATTR.search(line)
        if not m:
            skipped.append((task_name(line), "done but no [completed:] stamp"))
            continue

        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            skipped.append((task_name(line), f"unparseable date {m.group(1)!r}"))
            continue

        target = note_path(d)
        if not target.exists():
            skipped.append((task_name(line), f"no daily note for {d}"))
            continue

        target_text = target.read_text(encoding="utf-8")
        name = task_name(line)
        if any(task_name(l) == name for l in target_text.splitlines()
               if DONE_TASK.match(l) or l.strip().startswith("- [ ]")):
            skipped.append((name, f"already present in {target.name}"))
            continue

        if not TODAY_HEADING.search(target_text):
            skipped.append((name, f"no '## ✅ Today' heading in {target.name}"))
            continue

        moved.append((i, line, target))

    if not args.apply:
        print(f"DRY RUN — {len(moved)} task(s) would be archived, "
              f"{len(skipped)} skipped\n")
        for _, line, target in moved:
            rel = target.relative_to(SPACE)
            print(f"  MOVE  {task_name(line)}")
            print(f"        -> {rel}")
        if skipped:
            print()
            for name, why in skipped:
                print(f"  SKIP  {name}")
                print(f"        {why}")
        print("\nRe-run with --apply to perform the move.")
        return 0

    # ── apply ────────────────────────────────────────────────────────────
    archived_idx: set[int] = set()
    for idx, line, target in moved:
        name = task_name(line)
        target_text = target.read_text(encoding="utf-8")
        updated = insert_into_note(target_text, line)
        if updated is None:
            print(f"[SKIP] {name}: '## ✅ Today' vanished from {target.name}")
            continue

        target.write_text(updated, encoding="utf-8")

        # verify before deleting anything from the inbox
        verify = target.read_text(encoding="utf-8")
        if not any(task_name(l) == name for l in verify.splitlines()
                   if DONE_TASK.match(l)):
            print(f"[ERROR] {name}: write to {target.name} did not land — "
                  f"leaving in inbox", file=sys.stderr)
            continue

        archived_idx.add(idx)
        print(f"[OK] {name} -> {target.relative_to(SPACE)}")

    if archived_idx:
        remaining = [l for i, l in enumerate(lines) if i not in archived_idx]
        INBOX.write_text("\n".join(remaining) + "\n", encoding="utf-8")
        print(f"\nremoved {len(archived_idx)} line(s) from inbox")
    else:
        print("\nnothing archived; inbox untouched")

    for name, why in skipped:
        print(f"[SKIP] {name}: {why}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
