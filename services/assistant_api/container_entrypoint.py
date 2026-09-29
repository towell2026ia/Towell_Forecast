"""Prepare the mounted SQLite directory, then permanently drop root privileges."""
from __future__ import annotations

import os
import sys
from pathlib import Path

VOLUME = Path("/var/lib/forecast-towell")


def prepare_volume(state: Path, database: Path, uid: int, gid: int):
    # No recursive ownership change, no deleting/moving data and no path escapes.
    if state != VOLUME or state.resolve() != VOLUME or not database.is_relative_to(state) \
            or not database.resolve().is_relative_to(state) or database.suffix not in {".sqlite3", ".sqlite", ".db"}:
        raise ValueError("invalid_container_volume_path")
    # Build each directory explicitly beneath the verified mount.
    current = state
    parents = [state]
    for part in database.parent.relative_to(state).parts:
        current = current / part
        parents.append(current)
    for directory in parents:
        if directory.is_symlink():
            raise ValueError("invalid_container_volume_path")
        directory.mkdir(exist_ok=True)
        os.chown(directory, uid, gid)
        directory.chmod(0o750)
    for filename in (str(database), str(database) + "-wal", str(database) + "-shm"):
        path = Path(filename)
        if path.is_symlink():
            raise ValueError("invalid_container_volume_path")
        if path.exists():
            os.chown(path, uid, gid)
            path.chmod(0o600)


def main():
    import pwd  # Linux container only; ordinary local serve remains unchanged.
    account = pwd.getpwnam("forecast")
    state = Path(os.environ.get("STATE_DIR", str(VOLUME)))
    database = Path(os.environ.get("SQLITE_PATH", str(state / "historical/historical.sqlite3")))
    prepare_volume(state, database, account.pw_uid, account.pw_gid)
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    os.execv(sys.executable, [sys.executable, "-m", "services.assistant_api.serve"])


if __name__ == "__main__":
    main()
