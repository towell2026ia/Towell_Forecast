"""Explicit local backup/restore and migration-export commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .persistence import LocalPersistenceProvider
from .persistence_migration import export_bundle, validate_bundle
from .settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m services.assistant_api.persistence_admin")
    parser.add_argument("--database", type=Path, help="SQLite path; defaults to Settings.SQLITE_PATH")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("backup", "restore", "export", "validate"):
        command = commands.add_parser(name)
        command.add_argument("path", type=Path)
        if name == "restore":
            command.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    storage = LocalPersistenceProvider(args.database or settings.database_path)
    if args.command == "backup":
        result = storage.backup(args.path)
    elif args.command == "restore":
        result = storage.restore(args.path, confirm=args.confirm)
    elif args.command == "export":
        if args.path.exists():
            raise ValueError("export_target_must_be_new")
        bundle = export_bundle(storage)
        args.path.write_text(json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        result = bundle["manifest"]
    else:
        result = validate_bundle(json.loads(args.path.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
