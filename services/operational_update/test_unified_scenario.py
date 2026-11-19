from copy import deepcopy
import unittest

from services.operational_update.unified_scenario import build_close_runtime, validate_scenario
from services.operational_update.unified_source import CONTRACT, MASTER_SHA256
from services.operational_update.unified_runtime import catalog_origin_batch
from services.operational_update.unified_runtime import accredited_auxiliary_attributes


def fixture(period="2026-09", observed="0", definitive="99", proposal="88", complete=True):
    attributes = {"account": "SYNTHETIC_ACCOUNT", "format": "SYNTHETIC_FORMAT", "upc": "000123",
                  "model": "SYNTHETIC_MODEL", "presentation": "FACIAL", "color": "SYNTHETIC_COLOR", "product_type": "TOALLA", "commercial_code": "S1"}
    identity = {"identity_key": "A|F|000123", "state": "EXISTING_UUID", "chain_id": "SYNTHETIC_CHAIN",
                "product_id": "SYNTHETIC_PRODUCT", "category_id": "SYNTHETIC_CATEGORY", "attributes": attributes,
                "raw": {"tiene_historico": True}}
    row = {"clave": identity["identity_key"], "cuenta": "A", "formato": "F", "upc": "000123",
           "periodo": period, "pedido_real_piezas": observed, "venta_towell_real_piezas": "555",
           "venta_cliente_real_piezas": "666", "definitiva_piezas": definitive, "propuesta_pedidos_piezas": proposal,
           "apto_entrenamiento_pedido": complete, "source_row": 2,
           "lineage": {field: {"cell": "Base_2024_2026!" + column + "2", "raw": value, "formula": None}
                for field, column, value in (("pedido_real_piezas", "L", observed), ("venta_towell_real_piezas", "M", "555"),
                    ("venta_cliente_real_piezas", "N", "666"), ("definitiva_piezas", "R", definitive), ("propuesta_pedidos_piezas", "O", proposal))}}
    document = {"contract": CONTRACT, "source": {"sha256": MASTER_SHA256, "name": "SYNTHETIC.xlsx"},
                "closure": {"last_closed_period": "2026-09"}, "rows": [row]}
    return document, {"identities": [identity]}


class UnifiedScenarioTests(unittest.TestCase):
    def test_observed_zero_precedes_definitive_and_proposal(self):
        runtime = build_close_runtime(*fixture())
        row = runtime["engine_records"][0]
        self.assertEqual((row["basis"], row["value"]), ("OBSERVED", "0"))
        self.assertEqual(len(runtime["observed_records"]), 1)

    def test_partial_october_is_not_real_training_and_definitive_zero_is_used(self):
        runtime = build_close_runtime(*fixture("2026-10", "999", "0", "88", False))
        self.assertEqual((runtime["engine_records"][0]["basis"], runtime["engine_records"][0]["value"]), ("COMMERCIAL_CLOSE", "0"))
        self.assertEqual(runtime["observed_records"], [])
        order = next(row for row in runtime["facts"] if row["metric_code"] == "ORDER")
        self.assertEqual(order["value"], "999")
        self.assertNotEqual(order["id"], runtime["engine_records"][0]["source_id"])

    def test_order_proposal_fills_reference_not_other_sales(self):
        runtime = build_close_runtime(*fixture("2026-11", None, None, "42.50", False))
        row = runtime["engine_records"][0]
        self.assertEqual((row["value"], row["lineage"]["commercial_type"]), ("42.50", "ORDER_PROPOSAL"))
        self.assertEqual(row["source_reference"], "Base_2024_2026!O2")

    def test_missing_reference_remains_absent_not_zero(self):
        runtime = build_close_runtime(*fixture("2026-12", None, None, None, False))
        self.assertEqual(runtime["engine_records"], [])
        self.assertIsNone(runtime["scenario"]["records"][0]["value"])

    def test_non_2026_missing_order_never_uses_commercial_reference(self):
        runtime = build_close_runtime(*fixture("2025-11", None, "9", "8", False))
        self.assertEqual(runtime["engine_records"], [])

    def test_sales_changes_never_change_scenario_or_final_fit_input(self):
        document, bindings = fixture("2026-11", None, None, "42", False)
        baseline = build_close_runtime(document, bindings)
        modified = deepcopy(document)
        modified["rows"][0]["venta_towell_real_piezas"] = "777777777"
        modified["rows"][0]["venta_cliente_real_piezas"] = "888888888"
        result = build_close_runtime(modified, bindings)
        self.assertEqual(result["engine_records"], baseline["engine_records"])
        self.assertEqual(result["scenario"]["scenario_hash"], baseline["scenario"]["scenario_hash"])

    def test_reference_change_affects_scenario_but_not_observed_evaluation(self):
        document, bindings = fixture("2026-11", None, None, "42", False)
        first = build_close_runtime(document, bindings)
        document["rows"][0]["propuesta_pedidos_piezas"] = "43"
        second = build_close_runtime(document, bindings)
        self.assertNotEqual(first["source_hash"], second["source_hash"])
        self.assertNotEqual(first["scenario"]["scenario_hash"], second["scenario"]["scenario_hash"])
        self.assertEqual(first["observed_records"], second["observed_records"])

    def test_reproducible_and_tamper_seal_rejects(self):
        args = fixture()
        first, second = build_close_runtime(*args), build_close_runtime(*args)
        self.assertEqual(first, second)
        validate_scenario(first["scenario"])
        first["scenario"]["records"][0]["value"] = "999"
        with self.assertRaisesRegex(ValueError, "SEAL_MISMATCH"):
            validate_scenario(first["scenario"])

    def test_unaccredited_source_and_duplicate_months_block(self):
        document, bindings = fixture()
        document["source"]["sha256"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "SOURCE_UNACCREDITED"):
            build_close_runtime(document, bindings)
        document, bindings = fixture()
        document["rows"].append(deepcopy(document["rows"][0]))
        with self.assertRaisesRegex(ValueError, "DUPLICATE_PRODUCT_MONTH"):
            build_close_runtime(document, bindings)


class AuxiliaryAttributesRegressionTests(unittest.TestCase):
    def fixture(self):
        row = {"chain_id": "CHAIN", "product_id": "PRODUCT", "upc": "000123", "format": " Suburbia "}
        registered = {**row, "format": "SUBURBIA", "model": "SOURCE_MODEL"}
        return row, {"PRODUCT": registered}, {("CHAIN", "PRODUCT"): registered}

    def test_case_trim_equivalent_format_uses_accredited_literal(self):
        args = self.fixture()
        result = accredited_auxiliary_attributes(*args)
        self.assertEqual(result["format"], "SUBURBIA")
        self.assertEqual(args[0]["format"], " Suburbia ")

    def test_format_scope_or_upc_contradiction_never_normalizes_into_identity(self):
        for field, value in (("upc", "123"), ("chain_id", "OTHER"), ("product_id", "OTHER"), ("format", "BODEGA")):
            row, metadata, catalog = self.fixture()
            metadata = deepcopy(metadata)
            metadata["PRODUCT"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "contradiction"):
                accredited_auxiliary_attributes(row, metadata, catalog)


class CatalogOriginRegressionTests(unittest.TestCase):
    class Connection:
        def __init__(self, previous):
            self.previous, self.calls = previous, []
        def execute(self, statement, parameters):
            self.calls.append((statement, parameters))
            return self
        def fetchone(self):
            return self.previous

    def call(self, connection):
        return catalog_origin_batch(connection, chain_id="CHAIN", file_sha256="a" * 64,
            candidate_id="NEW_G6_ID", profile_version_id="NEW_G6_PROFILE", actor="ADMIN")

    def test_same_master_chain_reuses_original_g5_batch_without_mutating_it(self):
        c = self.Connection({"id": "ORIGINAL_G5_ID", "filename": "unified-catalog-local-bootstrap"})
        self.assertEqual(self.call(c), "ORIGINAL_G5_ID")
        self.assertEqual(len(c.calls), 1)
        self.assertTrue(c.calls[0][0].strip().startswith("select"))
        self.assertEqual(c.calls[0][1], ("CHAIN", "a" * 64))

    def test_unrelated_original_batch_is_a_contradiction_not_an_implicit_rebind(self):
        c = self.Connection({"id": "UNRELATED", "filename": "OTHER_UPLOAD.xlsx"})
        with self.assertRaisesRegex(ValueError, "existing_origin_not_accredited"):
            self.call(c)
        self.assertEqual(len(c.calls), 1)

    def test_first_import_creates_one_batch_with_original_file_provenance(self):
        c = self.Connection(None)
        self.assertEqual(self.call(c), "NEW_G6_ID")
        self.assertEqual(len(c.calls), 2)
        self.assertIn("insert into public.import_batches", c.calls[1][0])
        self.assertEqual(c.calls[1][1], ("NEW_G6_ID", "CHAIN", "NEW_G6_PROFILE", "a" * 64, "ADMIN"))


if __name__ == "__main__":
    unittest.main()
