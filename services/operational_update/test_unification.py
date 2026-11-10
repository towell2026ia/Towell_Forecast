import json
import tempfile
import unittest
import zipfile
from decimal import Decimal
from pathlib import Path

from .preparation import file_hash
from .unification import FACTS, PLAN, REFERENCE, parse_value, prepare_unification, verify_inputs, verify_logical_reversal


def make_snapshot():
    return {
        "chains": [{"id": "chain"}],
        "products": [
            {"id": "p1", "chain_id": "chain", "status": "ACTIVE"},
            {"id": "untouched", "chain_id": "chain", "status": "ACTIVE"},
        ],
        "profiles": [{"chain_id": "chain", "mapping_json": {
            "scope": {"level": "COMMERCIAL_UNIT", "actual_bearing": True, "code": "Store::A", "parent_code": "Store"},
            "products": [{"id": "p1", "identifier_mappings": [["004", "000001"]]}],
        }}],
        "observations": [
            {"chain_id": "chain", "product_id": "p1", "period": "2024-03", "metric_code": "SALES",
             "observation_id": "old-year", "value": "12", "version_no": 1, "availability_source": "UNKNOWN",
             "available_at": None},
            {"chain_id": "chain", "product_id": "p1", "period": "2025-07", "metric_code": "ORDER",
             "observation_id": "same-order", "value": "12.0", "version_no": 3, "availability_source": "UNKNOWN",
             "available_at": None},
            {"chain_id": "chain", "product_id": "untouched", "period": "2025-07", "metric_code": "SALES",
             "observation_id": "outside-file", "value": "7", "version_no": 1, "availability_source": "UNKNOWN",
             "available_at": None},
        ],
    }


def row(**overrides):
    return {
        "fila": "1", "cadena": "Store", "tipo_compra": "RS", "formato": "A", "item": "004", "upc": "000001",
        "periodo_fecha_q": "2025-07", "periodo_etiqueta_rs": "2025-07", "referencia_origen": "BAASE!1",
        "modelo": "Example", "tamano": "Example", "venta_piezas": "0", "pedido_piezas": "12",
        "entrega_piezas": "15", "fcst_cliente_piezas": "18.1234567890123456", "fcst_towell_piezas": "19",
        "inventario_reportado_piezas": "20.0123456789012345", **overrides,
    }


class UnificationTests(unittest.TestCase):
    def test_separate_five_series_inventory_and_unchanged_baseline(self):
        candidate = prepare_unification([row()], [], make_snapshot())
        self.assertEqual(len(candidate["a"]), 3)
        self.assertEqual(len(candidate["b"]), 7)
        self.assertTrue(all(row_a in candidate["b"] for row_a in candidate["a"]))
        self.assertEqual(candidate["stats"]["actuals_2024_preserved"], 1)
        self.assertEqual(candidate["stats"]["unchanged_prior_keys"], 3)
        self.assertEqual(candidate["inventory"][0]["value"], "20.0123456789012345")
        self.assertEqual(candidate["totals"][0]["fill_rate"], "1.25")
        self.assertNotIn("store_id", candidate["inventory"][0])
        changes = {r["metric_code"]: r for r in candidate["diff"]}
        self.assertEqual(changes["SALES"]["value"], "0")
        self.assertEqual(changes["ORDER"]["observation_id"], "same-order")
        self.assertEqual(changes["FCST_CLIENTE_HISTORY"]["value"], "18.1234567890123456")
        self.assertNotEqual(changes["FCST_CLIENTE_HISTORY"]["observation_id"], changes["FCST_TOWELL_HISTORY"]["observation_id"])
        self.assertTrue(all(r["available_at"] is None for r in candidate["b"]))
        reversal = verify_logical_reversal(candidate["a"], candidate["b"])
        self.assertEqual(reversal["a_hash"], reversal["restored_hash"])
        self.assertNotEqual(reversal["a_hash"], reversal["b_hash"])

    def test_conflicts_quarantine_and_a_valid_row_still_reconciles(self):
        conflict = row(fila="2", periodo_etiqueta_rs="2025-08", periodo_fecha_q="2025-09")
        wrong_scope = row(fila="3", cadena="Other")
        wrong_item = row(fila="4", item="999", periodo_fecha_q="2025-10", periodo_etiqueta_rs="2025-10")
        raw = [row(), conflict, wrong_scope, wrong_item]
        c = prepare_unification(raw, [], make_snapshot())
        self.assertEqual(len(c["inventory"]), 1)
        self.assertEqual({r["reason"] for r in c["issues"]},
                         {"period_q_rs_conflict", "scope_not_unique_or_missing", "contradictory_item"})
        self.assertEqual(c, prepare_unification(raw, [], make_snapshot()))

    def test_duplicate_and_cross_type_destination_collisions_are_isolated(self):
        raw = [row(), row(fila="2"), row(fila="3", tipo_compra="CE")]
        c = prepare_unification(raw, [], make_snapshot())
        self.assertEqual(c["a"], c["b"])
        self.assertEqual(c["inventory"], [])
        self.assertEqual(c["ce"], [])
        self.assertEqual(c["stats"]["issues"], {"shared_destination_scope_product_month": 3, "source_duplicate": 2})

    def test_ce_separate_and_partial_metrics_never_become_zero(self):
        raw = [row(tipo_compra="CE", venta_piezas="-", entrega_piezas="")]
        c = prepare_unification(raw, [], make_snapshot())
        self.assertEqual(c["a"], c["b"])
        self.assertEqual({r["metric_code"] for r in c["ce"]},
                         {"ORDER", "FCST_CLIENTE_HISTORY", "FCST_TOWELL_HISTORY", "INVENTORY_AGGREGATE"})
        self.assertEqual(c["stats"]["issues"], {"non_numeric": 1})
        self.assertIsNone(c["totals"][0]["fill_rate"])

    def test_decimal_contract_does_not_round_actuals(self):
        self.assertEqual(parse_value("1.234000", "ORDER"), (Decimal("1.234000"), None))
        self.assertEqual(parse_value("1.23401", "ORDER"), (None, "actual_outside_numeric_18_3"))
        self.assertEqual(parse_value("NaN", "INVENTORY_AGGREGATE"), (None, "non_finite_or_negative"))

    def test_zip_and_extracted_hashes_checked_independently(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            names = [FACTS, REFERENCE, PLAN, "README_PARA_CODEX.md"]
            for name in names + ["source.xlsx"]:
                (package / name).write_bytes(name.encode())
            def entry(name):
                return {"name": name, "bytes": (package / name).stat().st_size, "sha256": file_hash(package / name)}
            (package / "manifest.json").write_text(json.dumps({
                "status": "CANDIDATA_PRIVADA_NO_ACTIVADA", "sources": [entry("source.xlsx")],
                "files": [entry(name) for name in names],
            }), encoding="utf-8")
            archive = package / "pkg.zip"
            with zipfile.ZipFile(archive, "w") as z:
                for name in names + ["manifest.json"]:
                    z.write(package / name, arcname=f"root/{name}")
            self.assertIn("source.xlsx", verify_inputs(package, archive))
            (package / "source.xlsx").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "input_hash_or_size_mismatch"):
                verify_inputs(package, archive)
