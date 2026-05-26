from __future__ import annotations

import logging
import time
from pathlib import Path

from .config import Config
from .downloader import Downloader
from .pinterest import (
    AuthError, Pinterest, PinterestError,
    extract_media, pin_board_id, pin_board_name,
)
from .state import State

log = logging.getLogger(__name__)


def _db_path(output_dir: Path) -> Path:
    return output_dir / ".keepin" / "state.db"


# ------------------------------------------------------------------

def run_sync(config: Config) -> dict:
    output_dir = config.storage.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    client = Pinterest(config.pinterest.session_cookie, config.pinterest.username)
    state = State(_db_path(output_dir))
    dl = Downloader(output_dir, config.storage.organize_by, config.sync.workers)

    totals = {"boards": 0, "pins_seen": 0, "downloaded": 0, "skipped": 0, "errors": 0}

    try:
        log.info("Fetching board list for @%s ...", config.pinterest.username)
        boards = client.boards()
        totals["boards"] = len(boards)
        log.info("Found %d board(s)", len(boards))

        if boards and _boards_have_feed(client, boards[0]):
            # Happy path: fetch per-board via BoardFeedResource
            _sync_by_boards(client, boards, state, dl, totals)
        else:
            # Fallback: fetch all saved pins via UserPinsResource
            log.info("Using flat saved-pins feed (BoardFeedResource unavailable).")
            _sync_flat(client, state, dl, totals)

    finally:
        dl.shutdown()
        state.close()

    return totals


def _boards_have_feed(client: Pinterest, board: dict) -> bool:
    """Check if BoardFeedResource works for this account."""
    try:
        first = next(iter(client.board_pins(board["id"])), None)
        return first is not None or True   # 200 with empty = still OK
    except (PinterestError, StopIteration):
        return False


def _sync_by_boards(client, boards, state, dl, totals):
    for board in boards:
        board_id: str = board["id"]
        board_name: str = board["name"]
        log.info("  Board: %s", board_name)
        known = state.known_ids(board_id)
        pending = []

        for pin in client.board_pins(board_id):
            _collect(pin, board_id, board_name, known, state, dl, pending, totals)

        _flush(pending, state, dl, totals)

        # Sections
        try:
            for section in client.sections(board_id):
                sec_id: str = section["id"]
                sec_name = f"{board_name}/{section.get('title', sec_id)}"
                known_s = state.known_ids(board_id)
                sec_pending: list = []
                for pin in client.section_pins(sec_id):
                    _collect(pin, board_id, sec_name, known_s, state, dl, sec_pending, totals)
                _flush(sec_pending, state, dl, totals)
        except PinterestError as exc:
            log.debug("Section error (%s): %s", board_name, exc)


def _sync_flat(client, state, dl, totals):
    """Sync all saved pins via UserPinsResource; group by embedded board info."""
    pending: list = []
    seen_pin_ids: set[str] = set()

    for pin in client.all_saved_pins():
        pin_id: str = pin["id"]
        if pin_id in seen_pin_ids:
            continue
        seen_pin_ids.add(pin_id)

        board_id = pin_board_id(pin)
        board_name = pin_board_name(pin)
        known = state.known_ids(board_id)

        _collect(pin, board_id, board_name, known, state, dl, pending, totals)

        # Flush in batches of 100 to start downloading early
        if len(pending) >= 100:
            _flush(pending, state, dl, totals)
            pending.clear()

    _flush(pending, state, dl, totals)


def _collect(pin, board_id, board_name, known, state, dl, pending, totals):
    pin_id: str = pin["id"]
    totals["pins_seen"] += 1

    if pin_id in known:
        totals["skipped"] += 1
        return

    url, ext = extract_media(pin)
    if not url:
        log.debug("No media for pin %s", pin_id)
        return

    title = pin.get("title") or pin.get("description") or ""
    dest = dl.path_for(pin_id, board_name, title, ext)
    pending.append((pin_id, board_id, board_name, url, dest))


def _flush(pending, state, dl, totals):
    if not pending:
        return
    log.info("  Downloading %d pin(s) ...", len(pending))
    results = dl.download_batch([(url, dest) for _, _, _, url, dest in pending])
    for pin_id, board_id, board_name, url, dest in pending:
        if results.get(dest):
            state.mark_ok(pin_id, board_id, board_name, dest, url)
            totals["downloaded"] += 1
        else:
            state.mark_error(pin_id, board_id, board_name, url, "download_failed")
            totals["errors"] += 1


# ------------------------------------------------------------------

def run_daemon(config: Config):
    log.info("Daemon started. Interval: %ds. Press Ctrl+C to stop.", config.sync.interval)
    while True:
        try:
            stats = run_sync(config)
            log.info(
                "Sync done - boards:%d  downloaded:%d  skipped:%d  errors:%d",
                stats["boards"], stats["downloaded"], stats["skipped"], stats["errors"],
            )
        except AuthError as exc:
            log.error("Authentication error: %s", exc)
            log.error("Update session_cookie in keepin.toml and restart.")
            return
        except PinterestError as exc:
            log.error("Pinterest error: %s", exc)
        except Exception as exc:
            log.exception("Unexpected error: %s", exc)

        log.info("Next sync in %ds ...", config.sync.interval)
        time.sleep(config.sync.interval)
