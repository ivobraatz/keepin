from __future__ import annotations

import json
import logging
import re
import time
from typing import Iterator

import requests
from requests import Session

log = logging.getLogger(__name__)

_BASE = "https://www.pinterest.com"
_API = f"{_BASE}/resource"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    # Avoid br (Brotli) - saves installing brotli package
    "Accept-Encoding": "gzip, deflate",
    "X-Requested-With": "XMLHttpRequest",
    "X-Pinterest-AppState": "active",
}

_PWS_DATA_RE = re.compile(
    r'<script[^>]+id="__PWS_DATA__"[^>]*>(\{.+?\})</script>',
    re.DOTALL,
)


class PinterestError(Exception):
    pass


class AuthError(PinterestError):
    pass


class Pinterest:
    def __init__(self, session_cookie: str, username: str):
        self.username = username
        self._s = Session()
        self._s.headers.update(_HEADERS)
        self._s.cookies.set("_pinterest_sess", session_cookie, domain=".pinterest.com")
        self._app_version: str = ""
        self._ready = False

    # ------------------------------------------------------------------

    def _init(self):
        """
        Visit the profile page once to:
          - collect all required cookies (csrftoken, _auth, _b, _routing_id)
          - extract appVersion and X-Pinterest-PWS-Handler from __PWS_DATA__
        Both are required by Pinterest's resource API.
        """
        if self._ready:
            return

        try:
            resp = self._s.get(
                f"{_BASE}/{self.username}/",
                headers={"Accept": "text/html,application/xhtml+xml,*/*"},
                timeout=20,
            )
            if resp.status_code == 404:
                raise AuthError(
                    f"Pinterest user '{self.username}' not found. "
                    "Check username in keepin.toml."
                )

            m = _PWS_DATA_RE.search(resp.text)
            if m:
                try:
                    pws = json.loads(m.group(1))
                    self._app_version = pws.get("appVersion", "")
                    handler = pws.get("initialHandlerId", "")
                    if handler:
                        self._s.headers["X-Pinterest-PWS-Handler"] = handler
                    log.debug("App version: %s  Handler: %s", self._app_version, handler)
                except Exception as exc:
                    log.debug("Could not parse __PWS_DATA__: %s", exc)
            else:
                log.debug("__PWS_DATA__ not found - API calls may fail.")

        except AuthError:
            raise
        except Exception as exc:
            log.debug("Session init warning: %s", exc)

        csrf = self._s.cookies.get("csrftoken")
        if csrf:
            self._s.headers["X-CSRFToken"] = csrf

        self._s.headers["Referer"] = f"{_BASE}/{self.username}/"
        self._ready = True

    # ------------------------------------------------------------------

    def _get(self, resource: str, options: dict, source_url: str | None = None, retries: int = 4) -> dict:
        self._init()

        params = {
            "source_url": source_url or f"/{self.username}/",
            "data": json.dumps({
                "options": options,
                "context": {"app_version": self._app_version},
            }),
            "_": int(time.time() * 1000),
        }
        url = f"{_API}/{resource}/"

        for attempt in range(retries):
            try:
                resp = self._s.get(url, params=params, timeout=30)
            except requests.RequestException as exc:
                if attempt == retries - 1:
                    raise PinterestError(f"Network error: {exc}") from exc
                time.sleep(2 ** attempt)
                continue

            if resp.status_code in (401, 403):
                try:
                    msg = resp.json().get("resource_response", {}).get("message") or resp.text[:200]
                except Exception:
                    msg = resp.text[:200]
                log.debug("Pinterest %s: %s", resp.status_code, msg)
                raise AuthError(
                    f"HTTP {resp.status_code} - session cookie may be expired.\n"
                    "  Open pinterest.com, copy a fresh _pinterest_sess cookie, "
                    "and update keepin.toml."
                )

            if resp.status_code == 429:
                wait = 60 * (attempt + 1)
                log.warning("Rate-limited. Waiting %ds...", wait)
                time.sleep(wait)
                continue

            if not resp.ok:
                if attempt == retries - 1:
                    raise PinterestError(
                        f"HTTP {resp.status_code} for {resource} - {resp.text[:200]}"
                    )
                time.sleep(2 ** attempt)
                continue

            return resp.json()

        raise PinterestError("Max retries exceeded")

    # ------------------------------------------------------------------

    def boards(self) -> list[dict]:
        """
        Return all boards. Tries BoardsResource first; if empty (e.g. Quick Saves
        only accounts), returns a synthetic board list derived from pins.
        """
        result = self._get(
            "BoardsResource/get",
            {"username": self.username, "field_set_key": "profile_grid_item",
             "filter_stories": False, "include_secret": True},
        )
        data = result.get("resource_response", {}).get("data") or []
        boards = [b for b in data if isinstance(b, dict)]

        if boards:
            return boards

        # Fallback: derive board list from first page of saved pins
        log.debug("BoardsResource returned 0 - deriving boards from pins.")
        seen: dict[str, dict] = {}
        for pin in self._user_pins_page():
            b = pin.get("board")
            if isinstance(b, dict) and b.get("id") and b["id"] not in seen:
                seen[b["id"]] = b
        return list(seen.values())

    def board_pins(self, board_id: str) -> Iterator[dict]:
        yield from self._paginate("BoardFeedResource/get", {"board_id": board_id})

    def sections(self, board_id: str) -> list[dict]:
        result = self._get("BoardSectionsResource/get", {"board_id": board_id})
        data = result.get("resource_response", {}).get("data") or []
        return [s for s in data if isinstance(s, dict)]

    def section_pins(self, section_id: str) -> Iterator[dict]:
        yield from self._paginate("BoardSectionPinsResource/get", {"section_id": section_id})

    def all_saved_pins(self) -> Iterator[dict]:
        """
        Yield every saved pin via UserPinsResource (flat, all boards mixed).
        Used when BoardFeedResource is unavailable per board.
        """
        bookmark: str | None = None
        while True:
            options: dict = {"username": self.username, "page_size": 250}
            if bookmark:
                options["bookmarks"] = [bookmark]

            result = self._get(
                "UserPinsResource/get",
                options,
                source_url=f"/{self.username}/_saved/",
            )
            resp = result.get("resource_response", {})
            data = resp.get("data") or []

            for item in data:
                if isinstance(item, dict) and item.get("type") == "pin":
                    yield item

            bookmark = resp.get("bookmark")
            if not bookmark or bookmark == "-end-" or not data:
                break

            time.sleep(0.4)

    # ------------------------------------------------------------------

    def _user_pins_page(self) -> Iterator[dict]:
        """Single page of saved pins - used for board discovery fallback."""
        options = {"username": self.username, "page_size": 250}
        result = self._get(
            "UserPinsResource/get",
            options,
            source_url=f"/{self.username}/_saved/",
        )
        for item in result.get("resource_response", {}).get("data") or []:
            if isinstance(item, dict) and item.get("type") == "pin":
                yield item

    def _paginate(self, resource: str, base_options: dict) -> Iterator[dict]:
        bookmark: str | None = None
        while True:
            options = {**base_options, "field_set_key": "react_grid_pin",
                       "page_size": 250, "prepend": False}
            if bookmark:
                options["bookmarks"] = [bookmark]

            result = self._get(resource, options)
            resp = result.get("resource_response", {})
            data = resp.get("data") or []

            yielded = 0
            for item in data:
                if isinstance(item, dict) and item.get("type") == "pin":
                    yield item
                    yielded += 1

            bookmark = resp.get("bookmark")
            if not bookmark or bookmark == "-end-" or yielded == 0:
                break

            time.sleep(0.4)


# ------------------------------------------------------------------

def extract_media(pin: dict) -> tuple[str, str] | tuple[None, None]:
    """Return (url, extension) for best available media, or (None, None)."""
    vlist = (pin.get("videos") or {}).get("video_list") or {}
    for quality in ("V_1080P", "V_720P", "V_480P", "V_360P"):
        v = vlist.get(quality)
        if v and v.get("url"):
            return v["url"], "mp4"

    images = pin.get("images") or {}
    for size in ("orig", "736x", "474x", "236x"):
        img = images.get(size)
        if img and img.get("url"):
            raw_url: str = img["url"]
            ext = raw_url.rsplit(".", 1)[-1].split("?")[0].lower()
            if ext not in {"jpg", "jpeg", "png", "gif", "webp"}:
                ext = "jpg"
            return raw_url, ext

    return None, None


def pin_board_name(pin: dict) -> str:
    """Extract board name from pin data (fallback: 'Unsorted')."""
    board = pin.get("board")
    if isinstance(board, dict):
        return board.get("name") or "Unsorted"
    return "Unsorted"


def pin_board_id(pin: dict) -> str:
    """Extract board id from pin data."""
    board = pin.get("board")
    if isinstance(board, dict):
        return board.get("id") or "unsorted"
    return "unsorted"
