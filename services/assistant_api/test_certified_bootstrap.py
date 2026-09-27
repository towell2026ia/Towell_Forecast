"""Offline synthetic bootstrap gates. No credentials or remote writes."""
from copy import deepcopy
import inspect
import unittest

from services.assistant_api import certified_bootstrap as module
from services.assistant_api import bootstrap_sql as sql
from services.assistant_api.settings import Settings
from services.assistant_api.source_adjudication import fingerprint
from services.assistant_api.test_global_certification import fixture, certify


def expected(cert):
    data = {k: deepcopy(cert["summary"][k]) for k in
            ("selected_observations", "actual_bearing_scopes", "scope_product_identities", "categories",
             "metric_counts", "grain_counts", "blocked_counts")}
    return {**data, "dataset_sha256": cert["dataset_sha256"], "certification_sha256": cert["certification_sha256"]}


def readback(plan):
    result = []
    for reference in plan["observations"]:
        row = deepcopy(reference)
        fact = row["lineage"]["canonical_fact"]
        row.update(scope_code=fact["key"][0].upper(), product_code=fact["key"][1], variant_code=None,
                   evidence_source_hash=fact["source_sha256"], batch_status="IMPORTED", value=row["value"] + ".000")
        result.append(row)
    return result[::-1]


class BootstrapContracts(unittest.TestCase):
    def setUp(self):
        args = fixture()
        self.report, self.cert = args[0], certify(args)
        self.expected = expected(self.cert)
        self.plan = module.build_plan(self.report, self.cert, self.expected,
                                      code_commit="1" * 40, bootstrap_at="2026-09-26T00:00:00Z")

    def make(self, report=None, cert=None, constraints=None, **kw):
        return module.build_plan(report or self.report, cert or self.cert, constraints or self.expected,
                                 code_commit=kw.get("code_commit", "1" * 40),
                                 bootstrap_at=kw.get("bootstrap_at", "2026-09-26T00:00:00Z"))

    def test_dataset_exact(self):
        bad = deepcopy(self.expected)
        bad["dataset_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "dataset_sha"):
            self.make(constraints=bad)

    def test_certificate_content_sealed(self):
        bad = deepcopy(self.cert)
        bad["rows"][0]["sheet"] = "Tampered"
        with self.assertRaisesRegex(ValueError, "content_changed"):
            self.make(cert=bad)

    def test_certificate_exact(self):
        bad = deepcopy(self.expected)
        bad["certification_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "certification_sha"):
            self.make(constraints=bad)

    def test_literal_check_mandatory(self):
        bad = deepcopy(self.cert)
        bad["summary"]["literal_mismatches"] = 1
        bad["certification_sha256"] = fingerprint({k: v for k, v in bad.items() if k != "certification_sha256"})
        constraints = expected(bad)
        with self.assertRaisesRegex(ValueError, "literal_mismatches"):
            self.make(cert=bad, constraints=constraints)

    def test_certified_counts_exact(self):
        bad = deepcopy(self.expected)
        bad["selected_observations"] += 1
        with self.assertRaisesRegex(ValueError, "count_mismatch"):
            self.make(constraints=bad)

    def test_no_unknown_scope_master(self):
        bad = deepcopy(self.report)
        target = self.plan["scopes"][0]["scope_id"]
        next(s for s in bad["fact_scope_catalog"] if s["scope_id"] == target)["scope_type"] = "UNKNOWN_SCOPE"
        with self.assertRaisesRegex(ValueError, "unproven_scope_master|unproven_parent"):
            self.make(report=bad)

    def test_no_invented_parent(self):
        bad = deepcopy(self.report)
        target = self.plan["batches"][0]["scope_id"]
        next(s for s in bad["fact_scope_catalog"] if s["scope_id"] == target)["parent_id"] = "FAKE"
        with self.assertRaisesRegex(ValueError, "unproven_parent"):
            self.make(report=bad)

    def test_parent_catalog_dynamic(self):
        actual = {f["key"][0] for f in self.report["selected"]}
        self.assertEqual({s["scope_id"] for s in self.plan["scopes"] if s["actual_bearing"]}, actual)
        self.assertTrue(all(s["sql_code"] == s["scope_id"].upper() for s in self.plan["scopes"]))

    def test_product_identity_not_description(self):
        for p in self.plan["products"]:
            self.assertEqual(p["id"], module.uid("product", [p["scope_id"], p["product_code"], None]))
            self.assertIsNone(p["variant_code"])
            self.assertTrue(p["identifier_mappings"])

    def test_category_unknown_is_not_master(self):
        self.assertEqual(len(self.plan["categories"]), self.cert["summary"]["categories"])
        self.assertFalse(any(c["name"] == "UNCLASSIFIED" for c in self.plan["categories"]))

    def test_logical_batch_staging_and_physical_batch_contract(self):
        self.assertEqual(self.plan["manifest"]["status"], "STAGING")
        self.assertEqual(self.plan["manifest"]["kind"], module.BOOTSTRAP_KIND)
        self.assertTrue(all(b["status"] == "UPLOADED" for b in self.plan["batches"]))
        self.assertEqual(sum(b["row_count"] for b in self.plan["batches"]), len(self.plan["observations"]))

    def test_unknown_and_null_preserved(self):
        self.assertTrue(all(r["availability_source"] == "UNKNOWN" and r["available_at"] is None
                            for r in self.plan["observations"]))

    def test_lineage_every_fact(self):
        self.assertTrue(all(r["availability_evidence_id"] and r["source_batch_id"]
                            and r["lineage"]["canonical_fact"]["cell"]
                            and r["lineage"]["source_lineage_fingerprints"] for r in self.plan["observations"]))

    def test_bootstrap_id_reproducible(self):
        self.assertEqual(self.plan, self.make())

    def test_bootstrap_id_binds_timestamp(self):
        self.assertNotEqual(self.plan["manifest"]["bootstrap_id"],
                            self.make(bootstrap_at="2026-09-27T00:00:00Z")["manifest"]["bootstrap_id"])

    def test_bootstrap_id_binds_code(self):
        self.assertNotEqual(self.plan["manifest"]["bootstrap_id"], self.make(code_commit="2" * 40)["manifest"]["bootstrap_id"])

    def test_timestamp_cannot_be_naive(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.make(bootstrap_at="2026-09-26T00:00:00")

    def test_commit_full_required(self):
        with self.assertRaisesRegex(ValueError, "full_commit"):
            self.make(code_commit="1234567")

    def test_backlog_pending_only(self):
        cert = deepcopy(self.cert)
        cert["blocked_trace"]["decisions"] = [{"decision_id": "q" * 64, "kind": "FACT_SCOPE", "canonical_key": ["X", "P", "2024-01", "SALES"],
            "reason": "UNSUPPORTED_SCOPE", "sources": [{"source_sha256": "a" * 64, "sheet": "Private", "cell": "A1"}]}]
        cert["summary"]["blocked_counts"] = {"FACT_SCOPE": 1}
        backlog = module.backlog_manifest(cert)
        self.assertEqual(backlog["total"], 1)
        self.assertEqual(backlog["rows"][0]["status"], "PENDING_FUTURE_RECONCILIATION")
        self.assertTrue(backlog["rows"][0]["lineage_fingerprints"])
        self.assertNotIn("observations", backlog)

    def test_remote_fingerprint_business_key_order(self):
        result = module.verify_remote_facts(readback(self.plan), self.plan)
        self.assertEqual(result["dataset_sha"], self.plan["manifest"]["dataset_sha"])
        self.assertEqual(result["fabricated_available_at"], 0)

    def test_remote_value_change_blocked(self):
        rows = readback(self.plan)
        rows[0]["value"] = "99999"
        with self.assertRaisesRegex(ValueError, "value_or_lineage"):
            module.verify_remote_facts(rows, self.plan)

    def test_remote_role_or_scope_change_blocked(self):
        rows = readback(self.plan)
        rows[0]["scope_code"] = "FORGED"
        with self.assertRaisesRegex(ValueError, "business_identity"):
            module.verify_remote_facts(rows, self.plan)

    def test_remote_duplicates_blocked(self):
        rows = readback(self.plan)
        rows[0] = rows[1]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            module.verify_remote_facts(rows, self.plan)

    def test_remote_temporal_change_blocked(self):
        rows = readback(self.plan)
        rows[0]["available_at"] = "2026-09-26"
        with self.assertRaisesRegex(ValueError, "available_at"):
            module.verify_remote_facts(rows, self.plan)

    def test_remote_lineage_must_match(self):
        rows = readback(self.plan)
        rows[0]["lineage"]["canonical_fact"]["cell"] = "A99999"
        with self.assertRaisesRegex(ValueError, "lineage_changed"):
            module.verify_remote_facts(rows, self.plan)

    def test_blocked_source_intersection_explicit(self):
        bad = deepcopy(self.plan)
        fact = bad["observations"][0]["lineage"]["canonical_fact"]
        bad["backlog"]["rows"] = [{"kind": "VALUE", "canonical_key": fact["key"], "sources": [fact]}]
        with self.assertRaisesRegex(ValueError, "blocked_lineage_intersection"):
            module.verify_remote_facts(readback(self.plan), bad)

    def gate(self):
        return {"project_ref": "approved", "migrations": ["20260925000" + str(i) for i in range(1, 8)],
                "tables": [{"name": t, "rls_enabled": True} for t in module.TABLES],
                "views": ["monthly_observations_current"], "buckets": [{"id": b, "public": False} for b in module.BUCKETS]}

    def test_remote_schema_and_security_gate(self):
        self.assertTrue(all(v == "PASS" for v in module.validate_remote_gate(self.gate(), expected_project="approved").values()))

    def test_wrong_project_blocked(self):
        with self.assertRaisesRegex(ValueError, "wrong_supabase_project"):
            module.validate_remote_gate(self.gate(), expected_project="different")

    def test_extra_migration_blocked(self):
        gate = self.gate()
        gate["migrations"].append("202609250008")
        with self.assertRaisesRegex(ValueError, "migration_difference"):
            module.validate_remote_gate(gate, expected_project="approved")

    def test_rls_disable_blocked(self):
        gate = self.gate()
        gate["tables"][0]["rls_enabled"] = False
        with self.assertRaisesRegex(ValueError, "rls_not_enabled"):
            module.validate_remote_gate(gate, expected_project="approved")

    def test_extra_table_blocked(self):
        gate = self.gate()
        gate["tables"].append({"name": "users", "rls_enabled": True})
        with self.assertRaisesRegex(ValueError, "table_difference"):
            module.validate_remote_gate(gate, expected_project="approved")

    def test_public_bucket_blocked(self):
        gate = self.gate()
        gate["buckets"][0]["public"] = True
        with self.assertRaisesRegex(ValueError, "storage_not_private"):
            module.validate_remote_gate(gate, expected_project="approved")

    def test_local_paths_and_secret_fields_rejected(self):
        for value in ({"filename": "C:/private/file.xlsx"}, {"nested": [{"password": "not-a-real-secret"}]}):
            with self.assertRaises(ValueError):
                module.safe_metadata(value)

    def test_runtime_stays_disabled(self):
        config = Settings(app_env="test").validate()
        self.assertFalse(config.supabase_enabled)
        self.assertEqual(config.persistence_provider, "sqlite")
        self.assertEqual(config.data_provider, "normalized")

    def test_offline_module_has_no_database_mutation(self):
        source = inspect.getsource(module)
        for token in ("requests.", "urlopen(", "create_app(", "sqlite3.connect", "import psycopg"):
            self.assertNotIn(token, source)

    def test_future_v2_classifier_never_overwrites_v1(self):
        fact = {"key": ["X", "P", "2024-01", "SALES"], "value": "1"}
        key = tuple(fact["key"])
        old = {key: deepcopy(fact)}
        self.assertEqual(module.classify_future_fact(fact, {}, set()), "NEW")
        self.assertEqual(module.classify_future_fact(fact, old, set()), "SAME")
        changed = {**fact, "value": "2"}
        self.assertEqual(module.classify_future_fact(changed, old, set()), "CONFLICT")
        self.assertEqual(module.classify_future_fact(changed, old, set(), reviewed=True), "REVISION")
        self.assertEqual(module.classify_future_fact(changed, {}, {key}, reviewed=True), "RESOLVES_BACKLOG")
        self.assertEqual(old[key]["value"], "1")

    def test_sql_generated_only_for_sealed_plan(self):
        bad = deepcopy(self.plan)
        bad["observations"][0]["value"] = "99"
        with self.assertRaisesRegex(ValueError, "plan_content_changed"):
            sql.stage_setup_sql(bad, "00000000-0000-4000-8000-000000000101")

    def test_bootstrap_id_binding_checked(self):
        bad = deepcopy(self.plan)
        bad["manifest"]["bootstrap_id"] = "00000000-0000-4000-8000-000000000101"
        bad["plan_sha"] = fingerprint({k: v for k, v in bad.items() if k != "plan_sha"})
        with self.assertRaisesRegex(ValueError, "identity_changed"):
            sql.seal(bad)

    def test_sql_values_escaped_and_actor_not_injectable(self):
        self.assertEqual(sql.literal("a'b"), "'a''b'")
        with self.assertRaises(ValueError):
            sql.actor_guard("not-a-uuid';delete")

    def test_all_publication_builders(self):
        actor = "00000000-0000-4000-8000-000000000101"
        self.assertIn("STAGING", sql.stage_setup_sql(self.plan, actor))
        self.assertIn("staging_chunk_collision", sql.stage_chunk_sql(self.plan, actor, 0))
        self.assertIn("controlled_publication_failure", sql.publish_sql(self.plan, actor, simulate_failure=True))
        self.assertNotIn("disable trigger", sql.publish_sql(self.plan, actor))
        self.assertIn("transaction read only", sql.readback_sql(self.plan))
        self.assertIn("limit 50 offset 10", sql.readback_sql(self.plan, offset=10, limit=50))
        self.assertIn("business_lineage_sha", sql.measurement_sql(self.plan))
        self.assertIn("migration", sql.inventory_sql())
        probe = sql.security_probe_sql(self.plan, actor)
        self.assertTrue(probe.endswith("rollback;"))
        self.assertNotIn("disable row level security", probe)

    def test_invalid_readback_page_rejected(self):
        with self.assertRaises(ValueError):
            sql.readback_sql(self.plan, offset=-1)
        with self.assertRaises(ValueError):
            sql.readback_sql(self.plan, limit="100;delete")

    def test_cleanup_requires_failed_final_certification(self):
        actor = "00000000-0000-4000-8000-000000000101"
        with self.assertRaisesRegex(ValueError, "failed_final"):
            sql.cleanup_sql(self.plan, actor)
        statement = sql.cleanup_sql(self.plan, actor, certification_failed=True)
        self.assertNotIn("disable row level security", statement)
        self.assertNotIn("truncate", statement)
        self.assertNotIn("drop table", statement)
        self.assertIn("access exclusive mode", statement)
        self.assertEqual(statement.count("disable trigger"), statement.count("enable trigger"))

    def test_remote_scopes_measured_independently(self):
        result = module.verify_remote_scopes(readback(self.plan), self.plan)
        self.assertEqual(result["scope_count"], len(self.plan["scope_summary"]))
        self.assertEqual(result["observations"], len(self.plan["observations"]))
        self.assertEqual(result["grain_counts"], self.expected["grain_counts"])

    def test_missing_remote_scope_rejected(self):
        rows = readback(self.plan)
        sid = rows[0]["scope_code"]
        with self.assertRaisesRegex(ValueError, "scope_inventory_changed"):
            module.verify_remote_scopes([r for r in rows if r["scope_code"] != sid], self.plan)

    def test_remote_scope_metric_totals_rejected(self):
        rows = readback(self.plan)
        rows[0]["metric_code"] = "DELIVERY" if rows[0]["metric_code"] != "DELIVERY" else "ORDER"
        with self.assertRaisesRegex(ValueError, "scope_totals_changed"):
            module.verify_remote_scopes(rows, self.plan)

    def test_remote_scope_identity_rejected(self):
        rows = readback(self.plan)
        rows[0]["scope_code"] = "UNSUPPORTED"
        with self.assertRaisesRegex(ValueError, "scope_identity_changed"):
            module.verify_remote_scopes(rows, self.plan)


if __name__ == "__main__":
    unittest.main()
