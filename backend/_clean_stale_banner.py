"""One-off: fix pages stored under instructions the prompt no longer gives.

Two kinds of stale text were written *into* page JSON, so correcting the prompt leaves the old text on
pages already in the database:

1. The "Not verified" banner. Every turn added it, so grading and retry pages -- answered from the stored
   exercise and the learner's own code, with no repository to ground themselves in -- carry a warning
   about GitHub they were never checked against. Only those pages lose it; a roadmap page keeps it, since
   there it may well be true.

2. Retry titles. The prompt used to offer a separate "retry the exercise with extra hints" action, so the
   model titled the exercises it produced that way -- "4. LLM tool schemas (retry)", "re-opened with the
   reference shape". That action is gone (a hint belongs in the feedback details), and no amount of
   prompt fixing rewrites what is already on disk. Only the title and subtitle are touched, and only when
   they say "retry": renaming a page the learner is reading is worth less than leaving correct content
   alone, so nothing else about the page is changed.

Button labels need no cleanup: the frontend derives the text from the action id and ignores any label the
agent sent (see frontend/src/components/actionLabel.ts), so a stored "Retry exercise 6 with the worked
shape" already renders as "Retry exercise 6".

Run from backend/: uv run python _clean_stale_banner.py [--apply]
Without --apply it only reports, so the changes can be read before anything is written."""

from __future__ import annotations

import re
import sys

from app import db

BANNER = "**Not verified.**"

# The words that only ever came from the old retry-with-hints instruction. "Retry" alone would also match
# a legitimate "retry the next exercise" title, so the old wording is matched as a whole phrase.
RETRY_TITLE = re.compile(r"\s*[\(\[]\s*retry[a-z ]*\s*[\)\]]", re.IGNORECASE)
RETRY_SUBTITLE = re.compile(r"re-?open(s|ed)?\b|\bretry\b|\bwith hints\b", re.IGNORECASE)


def banner_is_stale(payload: dict) -> bool:
    """Whether this page's leading "Not verified" banner was added by a turn that was never asked to
    ground itself -- a feedback page, which grades the learner's own code."""
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return False
    first = blocks[0]
    if not (isinstance(first, dict) and first.get("type") == "markdown"
            and BANNER in (first.get("content") or "")):
        return False
    return any(b.get("type") == "feedback" for b in blocks if isinstance(b, dict))


def without_banner(payload: dict) -> dict:
    updated = dict(payload)
    updated["blocks"] = payload["blocks"][1:]
    return updated


def retry_subtitle_is_stale(payload: dict) -> bool:
    subtitle = payload.get("subtitle")
    return bool(isinstance(subtitle, str) and subtitle and RETRY_SUBTITLE.search(subtitle))


def retry_title_is_stale(payload: dict) -> bool:
    title = payload.get("title")
    return bool(isinstance(title, str) and RETRY_TITLE.search(title)) or retry_subtitle_is_stale(payload)


def without_retry_wording(payload: dict) -> dict:
    updated = dict(payload)
    updated["title"] = RETRY_TITLE.sub("", payload["title"]).strip()
    # Only a subtitle that is itself the old retry wording goes ("re-opened with the reference shape").
    # A subtitle saying something real about the exercise is kept -- it is content, not a label for the
    # retry, and dropping it would quietly lose something the learner needs.
    if retry_subtitle_is_stale(payload):
        updated.pop("subtitle", None)
    return updated


def main() -> int:
    apply = "--apply" in sys.argv
    banners = titles = 0
    with db.session_scope() as s:
        for row in s.query(db.Page).all():
            payload = row.payload
            if not isinstance(payload, dict):
                continue
            updated = payload
            if banner_is_stale(payload):
                updated = without_banner(updated)
                banners += 1
                print(f"{row.session_id} seq={row.seq}: dropping the stale banner")
            if retry_title_is_stale(updated):
                print(f"{row.session_id} seq={row.seq}: {updated.get('title')!r} -> ", end="")
                updated = without_retry_wording(updated)
                titles += 1
                print(f"{updated.get('title')!r}")
            if apply and updated is not payload:
                row.payload = updated
        if apply:
            s.commit()
    verb = "cleaned" if apply else "would clean"
    print(f"{verb}: {banners} banner(s), {titles} retry title(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
