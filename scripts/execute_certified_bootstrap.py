"""Explicit administrative bootstrap CLI. No credentials in args/files/logs.

Requires freshly authorized CLI, a clean committed planner, exact linked project,
verified private source pins, pre-load replay and a real approved Auth ADMIN.
Never runs in create_app(), startup, Railway or CI. No automatic cleanup.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.certify_global_corpus import read, verify_pins  # noqa: E402
from scripts.close_residual_fact_scope import run as replay  # noqa: E402
from scripts.reconstruct_chain_history import write_new  # noqa: E402
from services.assistant_api.bootstrap_sql import (  # noqa: E402
    chunks, cleanup_sql, inventory_sql, measurement_sql, publish_sql, readback_sql, seal,
    security_probe_sql, stage_chunk_sql, stage_setup_sql,
)
from services.assistant_api.certified_bootstrap import (  # noqa: E402
    validate_remote_gate, verify_remote_facts, verify_remote_scopes,
)
from services.assistant_api.historical_corpus import sha256_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


class LinkedCLI:
    def __init__(self, project, output):
        if (ROOT / "supabase/.temp/project-ref").read_text().strip() != project:
            raise ValueError("linked_project_mismatch")
        self.project, self.output = project, output
        self.calls = 0
        self.prefix = ["cmd.exe", "/c", "npx", "supabase"] if sys.platform == "win32" else ["npx", "supabase"]

    def command(self, *args):
        result = subprocess.run([*self.prefix, *args, "--output-format", "json", "--agent", "no"],
                                cwd=ROOT, text=True, encoding="utf-8", capture_output=True, timeout=240)
        if result.returncode:
            # Never dump credential prompts, SQL payloads or token-bearing debug output.
            raise RuntimeError("supabase_cli_failed_recheck_account_and_remote_state")
        return json.loads(result.stdout)

    def query(self, sql):
        projects = self.command("projects", "list")["projects"]
        if self.project not in {p["ref"] for p in projects}:
            raise ValueError("current_cli_account_has_no_access_STOP")
        self.calls += 1
        file = self.output / "sql" / f"query_{self.calls:04d}.sql"
        file.parent.mkdir(parents=True, exist_ok=True)
        # Generated operational artifacts only, never versioned implementation edits.
        with file.open("x", encoding="utf-8") as stream:
            stream.write(sql)
        return self.command("db", "query", "--linked", "--project-ref", self.project, "--file", str(file))


def private_output(output):
    output = output.resolve()
    if not output.is_relative_to(ROOT / "outputs") or (output.exists() and any(output.iterdir())):
        raise ValueError("fresh_private_execution_output_required")
    output.mkdir(parents=True, exist_ok=True)
    return output


def execute(plan_path, contract_path, contract_sha, *, project, actor, output, apply=False):
    output = private_output(output)
    head = git("rev-parse", "HEAD")
    if git("status", "--porcelain") or git("rev-parse", "origin/main") != head:
        raise ValueError("clean_pushed_execution_revision_required")
    plan = read(plan_path)
    seal(plan)
    if plan["manifest"]["code_commit"] != head:
        raise ValueError("execution_commit_mismatch")
    if sha256_file(contract_path) != contract_sha:
        raise ValueError("private_source_contract_changed")
    contract = read(contract_path)
    verify_pins(contract["files"])
    client = LinkedCLI(project, output)
    before = client.query(inventory_sql())[0]["snapshot"]
    before["project_ref"] = project
    validate_remote_gate(before, expected_project=project)
    if actor not in before["admin_actors"]:
        raise ValueError("approved_auth_admin_missing")
    write_new(output / "remote_before.json", before)
    existing = before["existing_bootstraps"]
    same = [j for j in existing if j["id"] == plan["manifest"]["bootstrap_id"]]
    if len(existing) != len(same) or (any(before["counts"].values()) and not same):
        raise ValueError("nonempty_remote_requires_explicit_corpus_reconciliation")
    if same and same[0]["metadata_json"]["master"]["plan_sha"] != plan["plan_sha"]:
        raise ValueError("remote_bootstrap_plan_changed")
    # All source literals are reverified immediately before the FIRST staging INSERT.
    fresh = replay(contract_path, contract_sha, output / "pre_insert_replay")
    if (fresh["baseline_gate"]["dataset_sha256"] != plan["manifest"]["dataset_sha"] or
            fresh["baseline_gate"]["certification_sha256"] != plan["manifest"]["certification_sha"]):
        raise ValueError("pre_insert_replay_mismatch")
    verify_pins(contract["files"])
    if not apply:
        write_new(output / "RESULT.json", {"status": "PASS_READ_ONLY_PREFLIGHT", "bootstrap_writes": 0})
        return
    try:
        client.query(stage_setup_sql(plan, actor))
        if not same or same[0]["status"] != "COMPLETED":
            for index in range(len(chunks(plan))):
                client.query(stage_chunk_sql(plan, actor, index))
                if index % 5 == 0:
                    print(json.dumps({"stage_chunks": index+1, "total_chunks": len(chunks(plan)),
                                      "published_observations": "NOT_YET_PUBLISHED"}), flush=True)
        client.query(publish_sql(plan, actor))
    except Exception:
        # A timeout never proves rollback. Keep the exact persisted ID and inspect
        # state, then retry this SAME plan. Never make a second timestamp/ID.
        write_new(output / "resume_required.json", {"bootstrap_id": plan["manifest"]["bootstrap_id"],
                  "dataset_sha": plan["manifest"]["dataset_sha"], "status": "INSPECT_REMOTE_BEFORE_RETRY",
                  "rollback_assumed": False, "checked_at": datetime.now(timezone.utc).isoformat()})
        raise
    remote = []
    for offset in range(0, len(plan["observations"]), 1000):
        remote.extend(client.query(readback_sql(plan, offset=offset, limit=1000)))
        print(json.dumps({"readback_rows": len(remote)}), flush=True)
    fingerprint = verify_remote_facts(remote, plan)
    scope_counts = verify_remote_scopes(remote, plan)
    after = client.query(inventory_sql())[0]["snapshot"]
    after["project_ref"] = project
    validate_remote_gate(after, expected_project=project)
    write_new(output / "remote_after.json", after)
    write_new(output / "remote_fingerprint.json", fingerprint)
    write_new(output / "remote_joined_facts.json", remote)
    write_new(output / "remote_scope_counts.json", scope_counts)
    # A complete rerun recognizes the exact committed job and skips chunk upload.
    # Compare independently measured business+lineage checksum, not just counts.
    first_measurement = client.query(measurement_sql(plan))[0]
    client.query(stage_setup_sql(plan, actor))
    client.query(publish_sql(plan, actor))
    second_measurement = client.query(measurement_sql(plan))[0]
    twice = client.query(inventory_sql())[0]["snapshot"]
    if twice["counts"] != after["counts"] or first_measurement != second_measurement:
        raise ValueError("second_run_changed_counts_or_business_lineage")
    write_new(output / "idempotency.json", {"status": "PASS", "counts_first": after["counts"],
                                           "counts_second": twice["counts"], "same_bootstrap_id": True,
                                           "first_business_measurement": first_measurement,
                                           "second_business_measurement": second_measurement,
                                           "frozen_dataset_sha_verified": fingerprint["dataset_sha"]})
    # Real PostgreSQL authenticated role/RLS behavior, with all probe writes
    # rolled back. Never persist extra users, grants, test categories or edits.
    security = client.query(security_probe_sql(plan, actor))
    if len(security) != 10 or len({r["name"] for r in security}) != 10 or not all(r["ok"] for r in security):
        raise ValueError("remote_security_behavior_failed")
    post_security = client.query(measurement_sql(plan))[0]
    final_inventory = client.query(inventory_sql())[0]["snapshot"]
    final_inventory["project_ref"] = project
    validate_remote_gate(final_inventory, expected_project=project)
    if (post_security != second_measurement or final_inventory["counts"] != after["counts"]
            or actor not in final_inventory["admin_actors"]):
        raise ValueError("security_probe_not_rolled_back")
    # Check actual grants and product descriptions too: observation checksums
    # alone cannot demonstrate that the editor/admin probes were rolled back.
    rollback_probe = client.query(f"""begin transaction read only;
select (select count(*) from public.user_chain_access where user_id='{actor}'::uuid) as actor_grants,
 (select count(*) from public.products where description like '% RLS_ROLLBACK_PROBE'
 or description like '% FORBIDDEN_PROBE') as probe_product_edits;
commit;""")[0]
    if rollback_probe != {"actor_grants": 0, "probe_product_edits": 0}:
        raise ValueError("security_probe_state_retained")
    write_new(output / "security_verification.json", {"status": "PASS", "checks": security,
              "rls_enabled": len(final_inventory["tables"]), "private_buckets": final_inventory["buckets"],
              "all_probe_mutations_rolled_back": True, "post_probe_state": rollback_probe})
    verify_pins(contract["files"])
    with (output / "scoped_withdrawal_after_failed_certification.sql").open("x", encoding="utf-8") as stream:
        stream.write(cleanup_sql(plan, actor, certification_failed=True))
    write_new(output / "rollback_plan.json", {"status": "LOCALLY_TESTED_NOT_EXECUTED_REMOTE",
              "bootstrap_id": plan["manifest"]["bootstrap_id"], "automatic_cleanup": False,
              "requires_failed_final_certification": True, "unrelated_deletes": 0,
              "audit_and_staging_retained": True, "FK_RLS_enabled": True})
    write_new(output / "RESULT.json", {"status": "PASS_REMOTE_BOOTSTRAP_GATES", "fingerprint": fingerprint,
              "scopes": scope_counts["scope_count"], "security": "PASS", "source_pins": "PASS",
              "execution_commit": head, "bootstrap_id": plan["manifest"]["bootstrap_id"],
              "final_repository_privacy_and_CI_required": True})
    print(json.dumps({"status": "PASS_REMOTE_BOOTSTRAP_GATES", "observations": len(remote)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--contract-sha", required=True)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply-certified-bootstrap", action="store_true")
    args = parser.parse_args()
    execute(args.plan, args.contract, args.contract_sha, project=args.project_ref, actor=args.actor,
            output=args.output_dir, apply=args.apply_certified_bootstrap)


if __name__ == "__main__":
    main()
