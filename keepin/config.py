from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        raise ImportError("Python < 3.11 requires tomli: pip install tomli")

CONFIG_FILENAME = "keepin.toml"

DEFAULT_CONFIG = """\
[pinterest]
# Grab from browser: DevTools (F12) > Application > Cookies > pinterest.com > _pinterest_sess
session_cookie = "PASTE_YOUR_SESSION_COOKIE_HERE"
username       = "your_pinterest_username"

[storage]
# Folder where pins will be saved (relative to this config file, or absolute)
output_dir  = "pins"
# "board" = one subfolder per board | "flat" = everything in output_dir
organize_by = "board"

[sync]
# Seconds between syncs when running in daemon mode (default: 1 hour)
interval = 3600
# Parallel download workers
workers  = 4
"""


@dataclass
class PinterestConfig:
    session_cookie: str
    username: str


@dataclass
class StorageConfig:
    output_dir: Path
    organize_by: str = "board"


@dataclass
class SyncConfig:
    interval: int = 3600
    workers: int = 4


@dataclass
class Config:
    pinterest: PinterestConfig
    storage: StorageConfig
    sync: SyncConfig

    @classmethod
    def load(cls, path: Path) -> "Config":
        with open(path, "rb") as f:
            raw = tomllib.load(f)

        p = raw["pinterest"]
        s = raw.get("storage", {})
        sy = raw.get("sync", {})

        cookie = p["session_cookie"].strip()
        # Strip prefix if user pasted the full "name=value" form
        if cookie.startswith("_pinterest_sess="):
            cookie = cookie[len("_pinterest_sess="):]

        return cls(
            pinterest=PinterestConfig(
                session_cookie=cookie,
                username=p["username"].strip().lstrip("@"),
            ),
            storage=StorageConfig(
                output_dir=_resolve_output(s.get("output_dir", "pins"), path.parent),
                organize_by=s.get("organize_by", "board"),
            ),
            sync=SyncConfig(
                interval=int(sy.get("interval", 3600)),
                workers=int(sy.get("workers", 4)),
            ),
        )


def _resolve_output(raw: str, config_dir: Path) -> Path:
    """Resolve output_dir: absolute or ~/... stays as-is; relative resolves from config file dir."""
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return (config_dir / p).resolve()


def find_config() -> Path:
    """Search: cwd -> ~/.keepin/ -> return cwd path as default."""
    local = Path.cwd() / CONFIG_FILENAME
    if local.exists():
        return local
    user = Path.home() / ".keepin" / CONFIG_FILENAME
    if user.exists():
        return user
    return local


def create_default_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG, encoding="utf-8")
