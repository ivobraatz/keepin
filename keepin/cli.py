from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import CONFIG_FILENAME, Config, create_default_config, find_config
from .state import State
from .sync import _db_path, run_daemon, run_sync


def _setup_logging(verbose: bool):
    logging.basicConfig(
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
        level=logging.DEBUG if verbose else logging.INFO,
    )
    # Silence noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def _load_config(args) -> Config:
    path = Path(args.config) if getattr(args, "config", None) else find_config()
    if not path.exists():
        print(f"Config not found: {path}")
        print("Run:  keepin init")
        sys.exit(1)
    return Config.load(path)


# ------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------

def cmd_init(args):
    if args.global_:
        dest = Path.home() / ".keepin" / CONFIG_FILENAME
    else:
        dest = Path.cwd() / CONFIG_FILENAME

    if dest.exists() and not args.force:
        print(f"Config already exists: {dest}")
        print("Use --force to overwrite.")
        return

    create_default_config(dest)
    print(f"Config created: {dest}")
    print()
    print("Quick-start:")
    print("  1. Open pinterest.com in your browser and log in.")
    print("  2. Open DevTools (F12) -> Application -> Cookies -> pinterest.com")
    print("  3. Copy the value of  _pinterest_sess")
    print(f"  4. Paste it into  session_cookie  in {dest}")
    print("  5. Set  username  to your Pinterest handle.")
    print("  6. Run:  keepin sync")


def cmd_sync(args):
    config = _load_config(args)
    _check_cookie(config)
    print(f"Syncing @{config.pinterest.username}  ->  {config.storage.output_dir}")
    stats = run_sync(config)
    print(
        f"\nDone - {stats['downloaded']} downloaded, "
        f"{stats['skipped']} already saved, "
        f"{stats['errors']} errors"
    )


def cmd_daemon(args):
    config = _load_config(args)
    _check_cookie(config)
    print(f"Daemon mode - syncing every {config.sync.interval}s.  Ctrl+C to stop.")
    try:
        run_daemon(config)
    except KeyboardInterrupt:
        print("\nStopped.")


def cmd_status(args):
    config = _load_config(args)
    db = _db_path(config.storage.output_dir)
    if not db.exists():
        print("No sync history yet. Run:  keepin sync")
        return

    state = State(db)
    g = state.stats()
    boards = state.board_stats()
    state.close()

    print(f"Output dir : {config.storage.output_dir}")
    print(f"Database   : {db}")
    print(f"Total      : {g['total']}  ({g['downloaded']} ok, {g['errors']} errors)")
    if boards:
        print()
        print(f"{'Board':<40}  {'Pins':>6}")
        print("-" * 48)
        for b in boards:
            print(f"{b['board_name']:<40}  {b['downloaded']:>6}")


# ------------------------------------------------------------------
# Service helpers
# ------------------------------------------------------------------

def cmd_install_service(args):
    import platform
    config_path = find_config()

    if platform.system() == "Linux":
        _install_systemd(config_path)
    elif platform.system() == "Windows":
        _install_windows_task(config_path)
    else:
        print(f"Auto-install not supported on {platform.system()}.")
        print("Run  keepin daemon  manually or add it to your startup scripts.")


def _install_systemd(config_path: Path):
    import shutil
    keepin_bin = shutil.which("keepin") or "keepin"
    unit = f"""[Unit]
Description=keepin – Pinterest pin sync
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={keepin_bin} daemon -c {config_path}
Restart=on-failure
RestartSec=60

[Install]
WantedBy=default.target
"""
    unit_path = Path.home() / ".config" / "systemd" / "user" / "keepin.service"
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit)
    print(f"Service file written: {unit_path}")
    print()
    print("Enable and start:")
    print("  systemctl --user daemon-reload")
    print("  systemctl --user enable --now keepin")
    print("  systemctl --user status keepin")


def _install_windows_task(config_path: Path):
    import shutil
    import subprocess
    keepin_bin = shutil.which("keepin") or "keepin"
    # XML for schtasks - runs at login, repeats every hour
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT1H</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </LogonTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>{keepin_bin}</Command>
      <Arguments>daemon -c "{config_path}"</Arguments>
    </Exec>
  </Actions>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
  </Settings>
</Task>"""
    xml_path = Path.home() / ".keepin" / "keepin-task.xml"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_path.write_text(xml, encoding="utf-16")
    result = subprocess.run(
        ["schtasks", "/Create", "/TN", "keepin", "/XML", str(xml_path), "/F"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("Windows Task Scheduler task created: keepin")
        print("keepin will start at next login and sync every hour.")
    else:
        print("schtasks failed:", result.stderr.strip())
        print(f"Task XML saved to {xml_path} - import it manually.")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _check_cookie(config: Config):
    if "PASTE_YOUR_SESSION_COOKIE" in config.pinterest.session_cookie:
        print("session_cookie not set in keepin.toml.")
        print("Run:  keepin init  for instructions.")
        sys.exit(1)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="keepin",
        description="Pinterest pin backup and sync - keeps your pins safe forever.",
    )
    parser.add_argument("--version", action="version", version=f"keepin {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug output")
    parser.add_argument("-c", "--config", metavar="FILE", help="Config file (default: auto-detect)")

    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p = sub.add_parser("init", help="Create a default keepin.toml")
    p.add_argument("-g", "--global", dest="global_", action="store_true",
                   help="Write to ~/.keepin/keepin.toml instead of current dir")
    p.add_argument("--force", action="store_true", help="Overwrite existing config")
    p.set_defaults(func=cmd_init)

    # sync
    p = sub.add_parser("sync", help="Download all new pins (one-shot)")
    p.set_defaults(func=cmd_sync)

    # daemon
    p = sub.add_parser("daemon", help="Run continuous background sync")
    p.set_defaults(func=cmd_daemon)

    # status
    p = sub.add_parser("status", help="Show sync statistics")
    p.set_defaults(func=cmd_status)

    # install-service
    p = sub.add_parser("install-service", help="Register keepin as a system service")
    p.set_defaults(func=cmd_install_service)

    args = parser.parse_args()
    _setup_logging(args.verbose)
    args.func(args)


if __name__ == "__main__":
    main()
