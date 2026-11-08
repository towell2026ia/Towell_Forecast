"""Prepare/verify a PRIVATE candidate. No activate/apply/forecast commands exist."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.operational_update.preparation import (  # noqa: E402
    REPO, collect_snapshot, file_hash, prepare, read_master, verify_package, write_package,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    candidate = commands.add_parser("prepare")
    candidate.add_argument("--source", type=Path, required=True)
    candidate.add_argument("--output", type=Path, required=True)
    candidate.add_argument("--cutoff", required=True)
    source = candidate.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot", type=Path)
    source.add_argument("--live-read", action="store_true")
    candidate.add_argument("--snapshot-sha256")
    candidate.add_argument("--expected-identities", type=int)
    candidate.add_argument("--expected-groups", type=int)
    candidate.add_argument("--preserve", type=Path, action="append", default=[])
    candidate.add_argument("--preview-model")
    candidate.add_argument("--preview-format")
    verification = commands.add_parser("verify")
    verification.add_argument("--output", type=Path, required=True)
    verification.add_argument("--manifest-hash", required=True)
    args = parser.parse_args(argv)
    if args.command == "verify":
        result = verify_package(args.output, args.manifest_hash)
    else:
        if args.output.resolve().is_relative_to(REPO) or args.output.exists():
            raise ValueError("output_must_be_new_and_outside_repository")
        preserved = {str(p.resolve()): file_hash(p) for p in [args.source, *args.preserve]}
        if args.snapshot:
            if not args.snapshot_sha256 or file_hash(args.snapshot) != args.snapshot_sha256:
                raise ValueError("snapshot_hash_required_or_mismatch")
            snapshot = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
        else:
            from services.assistant_api.supabase_access import SupabaseReadClient
            url = os.environ.get("SUPABASE_URL", "")
            key = os.environ.get("SUPABASE_ANON_KEY", "")
            token = os.environ.get("FORECAST_REVIEW_ACCESS_TOKEN", "")
            if not url.startswith("https://") or not key or not token or key.startswith("sb_secret_"):
                raise ValueError("authenticated_read_configuration_required")
            client = SupabaseReadClient(url, key, token)
            client.identity()  # Verify Auth and scope; no service role or JWT trust.
            snapshot = collect_snapshot(client)
        master = read_master(args.source)
        candidate = prepare(master, snapshot, args.cutoff)
        for field in ("identities", "groups"):
            expected = getattr(args, "expected_" + field)
            if expected is not None and candidate["manifest"]["summary"][field] != expected:
                raise ValueError("source_coverage_changed")
        if any(file_hash(p) != digest for p, digest in preserved.items()):
            raise ValueError("protected_file_changed_before_export")
        manifest = write_package(candidate, snapshot, args.output, args.preview_model, args.preview_format, preserved)
        if any(file_hash(p) != digest for p, digest in preserved.items()):
            raise ValueError("protected_file_changed_after_export")
        result = {"candidate_id": manifest["candidate_id"], "manifest_hash": manifest["manifest_hash"],
                  "summary": manifest["summary"], "protected_files_unchanged": len(preserved), "production_writes": 0}
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        message = str(error)
        print("preparation_failed:" + (message if re.fullmatch(r"[a-z0-9_]+", message) else type(error).__name__), file=sys.stderr)
        raise SystemExit(1) from None
