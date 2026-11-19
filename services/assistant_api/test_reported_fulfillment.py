"""Synthetic percentages never become pairs, training rows or aggregate ratios."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from services.assistant_api.local_sales_policy import LocalSalesPolicy, MASTER_NAME
from services.assistant_api.local_postgres import _response
from services.assistant_api.reported_fulfillment import reported_fulfillment
from services.assistant_api.supabase_access import PreviewError, matches_source, require_sales_context
from services.assistant_api.test_order_paths import order_context

C = "11111111-1111-4111-8111-111111111111"
P = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"


class ReportedFulfillmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "datos_privados"
        self.folder.mkdir()
        self.path = self.folder / "reported.json"
        self.context = {**order_context(closed=True), "last_period": "2026-12", "training_cutoff": "2026-09",
            "fulfillment_source": "ESTADISTICA_BAASE_REPORTED",
            "fulfillment_mode": "REPORTED_PERCENTAGE", "fulfillment_file_sha256": "a" * 64,
            "fulfillment_report_sha256": "b" * 64, "fulfillment_available": True}
        self.catalog = [{"chain_id": C, "product_id": P, "format": "SYNTHETIC", "upc": "00001234", "purchase_type": "RS"}]
        self.row = {**self.catalog[0], "period": "2026-10", "source_reference": "BAASE!S100",
            "source_sha256": "a" * 64, "reported_fill_rate_pct": "91.5800000000000001"}
        self.save([self.row])

    def save(self, rows):
        raw = json.dumps(rows).encode()
        self.path.write_bytes(raw)
        self.context["fulfillment_report_sha256"] = hashlib.sha256(raw).hexdigest()

    def read(self, supplied=None, catalog=None):
        parameters = {"p_source_hash": self.context["source_hash"], "p_generation": self.context["generation"], **(supplied or {})}
        return reported_fulfillment({"fulfillment_report_path": str(self.path), "fulfillment_partial_periods": ["2026-10"]}, self.context,
            parameters, self.catalog if catalog is None else catalog)

    def test_literal_percentage_exact_without_invented_denominators(self):
        row, = self.read()
        self.assertEqual(row["reported_fill_rate_pct_text"], "91.5800000000000001")
        self.assertEqual(Decimal(row["reported_fill_rate_pct"]), Decimal(self.row["reported_fill_rate_pct"]))
        self.assertEqual(row["period"], "2026-10-01")
        self.assertIsNone(row["order_text"])
        self.assertIsNone(row["delivery_text"])
        self.assertIsNone(row["order_source_reference"])
        self.assertIsNone(row["delivery_source_reference"])
        self.assertTrue(row["partial_period"])
        self.assertEqual(row["upc"], "00001234")

    def test_only_rls_visible_exact_catalog_members_are_returned(self):
        self.assertEqual(self.read(catalog=[]), [])
        self.assertEqual(self.read({"p_chain_id": OTHER}), [])
        self.assertEqual(self.read({"p_product_ids": [OTHER]}), [])
        self.assertEqual(self.read({"p_product_ids": []}), [])
        self.assertEqual(len(self.read({"p_product_ids": json.dumps([P]), "p_chain_id": C})), 1)
        self.assertEqual(self.read({"p_format": "OTHER"}), [])
        self.assertEqual(self.read({"p_from": "2026-11-01"}), [])
        self.assertEqual(self.read({"p_to": "2026-09-01"}), [])

    def test_zero_is_unreported_missing_stays_missing_and_rows_never_average(self):
        second = {**self.row, "period": "2026-09", "reported_fill_rate_pct": "0"}
        self.save([self.row, second])
        self.assertEqual(len(self.read()), 1)
        self.assertEqual(self.read({"p_to": "2026-09-01"}), [])

    def test_equivalent_format_casing_uses_canonical_label_only_after_exact_identity(self):
        self.save([{**self.row, "format": " synthetic "}])
        row, = self.read({"p_format": "synthetic"})
        self.assertEqual(row["format"], "SYNTHETIC")
        self.save([{**self.row, "upc": "1234", "format": "synthetic"}])
        with self.assertRaisesRegex(PreviewError, "FILL_RATE_REPORT_UNAVAILABLE"):
            self.read()

    def test_postgresql_numeric_response_is_json_number_with_exact_companion(self):
        row, = self.read()
        # jsonb_to_recordset declares the primary column numeric. Psycopg then
        # yields Decimal; the gateway serializes that column as a JSON number.
        row["reported_fill_rate_pct"] = Decimal(row["reported_fill_rate_pct"])
        value, = _response(200, [row]).json()
        self.assertIsInstance(value["reported_fill_rate_pct"], (float, int))
        self.assertEqual(value["reported_fill_rate_pct_text"], "91.5800000000000001")

    def test_baase_partial_status_does_not_inherit_order_observed_cutoff(self):
        self.context.update(training_cutoff="2026-12", observed_cutoff="2026-01")
        self.save([{**self.row, "period": "2026-09"}, self.row])
        rows = self.read()
        self.assertEqual([(r["period"], r["partial_period"]) for r in rows], [("2026-09-01", False), ("2026-10-01", True)])

    def test_old_source_or_generation_cannot_return_report(self):
        for changes in ({"p_source_hash": "f" * 64}, {"p_generation": 2}):
            with self.subTest(changes=changes), self.assertRaisesRegex(PreviewError, "OFFICIAL_SOURCE_CHANGED"):
                self.read(changes)
        for changes in ({"p_purchase_type": "CE"}, {"p_product_ids": "not-a-uuid"}, {"p_from": "invalid"}):
            with self.subTest(changes=changes), self.assertRaises(PreviewError):
                self.read(changes)

    def test_file_is_checked_each_read_and_old_pairs_are_never_a_fallback(self):
        self.assertEqual(len(self.read()), 1)
        self.path.write_bytes(b"[]")
        with self.assertRaisesRegex(PreviewError, "FILL_RATE_REPORT_UNAVAILABLE"):
            self.read()
        self.path.unlink()
        with self.assertRaisesRegex(PreviewError, "FILL_RATE_REPORT_UNAVAILABLE"):
            self.read()

    def test_malformed_or_contradictory_source_fails_closed(self):
        for changes in ({"source_sha256": "f" * 64}, {"format": "OTHER"}, {"upc": "1234"},
                {"period": "2026-13"}, {"reported_fill_rate_pct": "NaN"}, {"reported_fill_rate_pct": "-1"},
                {"reported_fill_rate_pct": 91.58}, {"source_reference": ""}):
            self.save([{**self.row, **changes}])
            with self.subTest(changes=changes), self.assertRaisesRegex(PreviewError, "FILL_RATE_REPORT_UNAVAILABLE"):
                self.read()
        self.save([self.row, self.row])
        with self.assertRaisesRegex(PreviewError, "FILL_RATE_REPORT_UNAVAILABLE"):
            self.read()

    def test_context_pin_includes_report_digest_but_does_not_gate_target_forecast(self):
        require_sales_context(self.context)
        self.assertFalse(matches_source(self.context, {**self.context, "fulfillment_report_sha256": "e" * 64}))
        self.assertFalse(matches_source(self.context, {**self.context, "fulfillment_mode": "AVERAGE"}))
        unavailable = {**self.context, "fulfillment_available": False}
        require_sales_context(unavailable)
        with self.assertRaisesRegex(PreviewError, "FILL_RATE_REPORT_UNAVAILABLE"):
            reported_fulfillment({"fulfillment_report_path": str(self.path)}, unavailable,
                {"p_source_hash": unavailable["source_hash"], "p_generation": unavailable["generation"]}, self.catalog)

    def test_policy_uses_only_private_pinned_sidecar_and_preserves_order_contract(self):
        master = self.folder / MASTER_NAME
        master.write_bytes(b"synthetic-master")
        config = {"contract": "pedido-towell-v1", "master_path": str(master),
            "master_sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
            "source_hash": self.context["source_hash"], "generation": self.context["generation"],
            "sheet": "Pedidos", "unit": "PIEZAS", "last_closed_period": "2026-09",
            **{key: value for key, value in self.context.items() if key.startswith("fulfillment_")},
            "fulfillment_report_path": str(self.path)}
        policy = LocalSalesPolicy(config)
        actual = {key: self.context[key] for key in ("source_hash", "generation", "first_period", "last_period")}
        self.assertEqual(policy.context(actual)["fulfillment_mode"], "REPORTED_PERCENTAGE")
        self.path.unlink()
        self.assertTrue(policy.context(actual)["forecast_available"])
        self.assertEqual(policy.context(actual)["target_sheet"], "Pedidos")
        for change in ({"fulfillment_report_path": "relative.json"}, {"fulfillment_report_sha256": "invalid"}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "local_fulfillment_policy_invalid"):
                LocalSalesPolicy({**deepcopy(config), **change})


if __name__ == "__main__":
    unittest.main()
