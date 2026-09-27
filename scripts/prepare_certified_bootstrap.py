"""Read-only source replay and private bootstrap plan. NEVER executes remote SQL."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.certify_global_corpus import read, verify_pins  # noqa: E402
from scripts.close_residual_fact_scope import run  # noqa: E402
from scripts.reconstruct_chain_history import write_new  # noqa: E402
from services.assistant_api.certified_bootstrap import build_plan  # noqa: E402
from services.assistant_api.historical_corpus import sha256_file  # noqa: E402


def prepare(contract_path, contract_sha, output, *, code_commit, bootstrap_at):
    root = Path(__file__).resolve().parents[1]
    output = output.resolve()
    if not output.is_relative_to(root / "outputs") or (output.exists() and any(output.iterdir())):
        raise ValueError("fresh_private_output_required")
    if sha256_file(contract_path) != contract_sha:
        raise ValueError("contract_bytes_changed")
    contract = read(contract_path)
    # This independently rereads every source literal, reverses source order,
    # checks frozen global + legacy goldens, and verifies pins again at the end.
    replay = run(contract_path, contract_sha, output / "pre_bulk_certification")
    paths = {k: Path(v["path"]) for k, v in contract["files"].items()}
    report, cert = read(paths["baseline"]), read(paths["global_certification"])
    expected = {**contract["expected_global"], **{k: cert["summary"][k] for k in
                ("categories", "metric_counts", "grain_counts", "blocked_counts")}}
    plan = build_plan(report, cert, expected, code_commit=code_commit, bootstrap_at=bootstrap_at)
    verify_pins(contract["files"])
    for name, data in (("bootstrap_manifest", plan["manifest"]), ("bootstrap_plan", plan),
                       ("historical_backlog_v1", plan["backlog"]),
                       ("pre_load_validation", {"status": "PASS", "corpus_gate": replay["baseline_gate"],
                                                "regressions": replay["regressions"],
                                                "determinism": replay["determinism"], "supabase_writes": 0,
                                                "execution_authorized_by_this_script": False})):
        write_new(output / (name + ".json"), data)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--contract-sha", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-at", default=None)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    code_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    # The recorded SHA must identify the actual planner, not an uncommitted file.
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip():
        raise SystemExit("clean_committed_planner_required; finish local tests before planning an executable load")
    plan = prepare(args.contract, args.contract_sha, args.output_dir, code_commit=code_commit,
                   bootstrap_at=args.bootstrap_at or datetime.now(timezone.utc).isoformat())
    print("PREPARED_NOT_LOADED", plan["manifest"]["bootstrap_id"], len(plan["observations"]))


if __name__ == "__main__":
    main()
