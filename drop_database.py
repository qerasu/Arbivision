import argparse
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from arbitrage_bot.core.env_loader import load_env_file

ENV_FILE_PATH = Path.home() / ".config" / "arbivision" / ".env"



def _pidfile():
    return Path(tempfile.gettempdir()) / "arbitrage_alert_bot.pid"


def _wait_for_exit(pid, timeout=6):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.2)
        except ProcessLookupError:
            return True
        except OSError:
            return False
    return False


def _kill_process_group(pid, signum):
    try:
        os.killpg(os.getpgid(pid), signum)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        try:
            os.kill(pid, signum)
            return True
        except ProcessLookupError:
            return False


def _stop_tracked_process():
    pid_file = _pidfile()
    if not pid_file.exists():
        print("pid file not found; no tracked process to stop")
        return

    raw_pid = pid_file.read_text().strip()
    try:
        pid = int(raw_pid)
    except ValueError:
        print(f"pid file contains invalid value: {raw_pid!r}")
        pid_file.unlink(missing_ok=True)
        return

    print(f"stopping tracked process {pid}...")
    _kill_process_group(pid, signal.SIGTERM)
    if not _wait_for_exit(pid):
        _kill_process_group(pid, signal.SIGKILL)
    pid_file.unlink(missing_ok=True)


def _run_cmd(args):
    display_cmd = " ".join(args)
    print(f"running: {display_cmd}")
    result = subprocess.run(args)
    if result.returncode != 0:
        print(f"error while running: {display_cmd}")
        raise SystemExit(result.returncode)


def _confirm_drop(force):
    if force:
        return

    print("\n[PIZDEC] this will remove docker containers, network, and volumes for Postgres and Redis.")
    print("[PIZDEC] all current data in these databases will be deleted.\n")
    answer = input("type 'drop' to continue: ").strip().lower()
    if answer != "drop":
        print("operation cancelled")
        raise SystemExit(1)


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip confirmation prompt",
    )
    return parser.parse_args()


def main():
    args = _parse_args()

    load_env_file(str(ENV_FILE_PATH))
    _confirm_drop(args.yes)

    print("\n=== dropping arbitrage alert bot databases ===")
    _stop_tracked_process()
    _run_cmd(["docker", "compose", "down", "-v"])
    print("\npostgres and redis data were removed successfully")


if __name__ == "__main__":
    main()