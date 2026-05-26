from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_UNSAFE = re.compile(r'[^\w\-. ]')


def _slugify(text: str, max_len: int = 60) -> str:
    return (_UNSAFE.sub("_", text or "").strip("_. ") or "pin")[:max_len]


def _download_one(url: str, dest: Path, retries: int = 3) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        return True

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")

    for attempt in range(retries):
        try:
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(65_536):
                        f.write(chunk)
            tmp.rename(dest)
            return True
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                log.debug("Download failed (%s): %s", dest.name, exc)
    return False


class Downloader:
    def __init__(self, output_dir: Path, organize_by: str, workers: int = 4):
        self._output = output_dir
        self._organize = organize_by
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="keepin")

    def path_for(self, pin_id: str, board_name: str, title: str, ext: str) -> Path:
        fname = f"{pin_id}_{_slugify(title)}.{ext}"
        if self._organize == "board":
            return self._output / _slugify(board_name) / fname
        return self._output / fname

    def download_batch(
        self,
        tasks: list[tuple[str, Path]],   # [(url, dest), ...]
    ) -> dict[Path, bool]:
        futures = {
            self._pool.submit(_download_one, url, dest): dest
            for url, dest in tasks
        }
        results: dict[Path, bool] = {}
        for fut in as_completed(futures):
            dest = futures[fut]
            try:
                results[dest] = fut.result()
            except Exception as exc:
                log.debug("Worker error %s: %s", dest.name, exc)
                results[dest] = False
        return results

    def shutdown(self):
        self._pool.shutdown(wait=True)
