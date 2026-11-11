"""Standalone-source regression tests using synthetic source rows."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from .official_source import (
    _cached_bindings, _master_bindings, prepare_official_source,
)
from .preparation import file_hash
from .test_unification import make_snapshot, row
from .unification import read_rows


BOOKS = {
    "closing": {"id": "8cb0640b-e035-5358-85d1-32d802644c8c", "source_name": "closing.xlsx", "source_hash": "1" * 64, "reference": "BAASE"},
    "master": {"id": "9fb19750-cd19-56d4-a2ea-d0bc26c14d6c", "source_name": "master.xlsx", "source_hash": "2" * 64, "reference": "Towell/Cliente/Pedidos"},
}


def identities():
    return [{"id": "account|000001", "account": "account", "chain": "Store", "format": "A", "barcode": "000001",
             "model": "Example", "size": "Example", "color": "Blue", "commercial_code": "COM-1",
             "chain_id": "chain", "product_id": "p1", "state": "COINCIDENCIA_VERIFICADA", "reason": "previous_binding"}]


def facts(**overrides):
    return row(color="Blue", linea="Toalla", **overrides)


def prepare(rows, *, refs=None, plans=None, master=None):
    snapshot = make_snapshot()
    from .unification import catalog_index
    scopes, codes = catalog_index(snapshot)
    return prepare_official_source(rows, refs or [], plans or [], master or identities(),
                                   list(read_rows(rows, scopes, codes)), BOOKS)


class OfficialSourceTests(unittest.TestCase):
    def test_source_contains_all_five_quantities_even_when_old_values_were_equal(self):
        prepared = prepare([facts()])
        incoming = prepared["import"]
        values = {row["metric_code"]: row for row in incoming["observations"]}
        self.assertEqual(len(values), 5)
        self.assertEqual(values["ORDER"]["value"], "12")
        self.assertEqual(values["SALES"]["value"], "0")
        self.assertEqual(values["ORDER"]["source_reference"], "BAASE!W1")
        self.assertEqual((values["ORDER"]["product_type"], values["ORDER"]["source_product_type_cell"]), ("Toalla", "E1"))
        self.assertEqual((values["ORDER"]["account"], values["ORDER"]["commercial_code"]), ("account", "COM-1"))
        self.assertEqual(values["FCST_CLIENTE_HISTORY"]["value"], "18.1234567890123456")
        self.assertEqual(incoming["inventory"][0]["value"], "20.0123456789012345")
        self.assertEqual(incoming["inventory"][0]["source_reference"], "BAASE!Y1")
        self.assertEqual(prepared["stats"]["inherited_observations"], 0)
        self.assertFalse(any(row["period"].startswith("2024") for row in incoming["observations"]))
        self.assertFalse(any("version_no" in row for row in incoming["observations"]))
        self.assertTrue(all(row["available_at"] is None and row["availability_source"] == "UNKNOWN"
                            for row in incoming["observations"]))
        self.assertEqual(len(incoming["sources"]), 2)
        self.assertEqual(prepared["coverage"][0]["fill_rate"], "1.25")
        self.assertNotIn("store_id", incoming["inventory"][0])

    def test_actual_decimals_preserved_with_explicit_blanks_and_bad_individual_variable(self):
        prepared = prepare([facts(venta_piezas="1.2345678901234567", pedido_piezas="", entrega_piezas="-")])
        records = {row["metric_code"]: row for row in prepared["import"]["observations"]}
        self.assertEqual(records["SALES"]["value"], "1.2345678901234567")
        self.assertNotIn("ORDER", records)
        self.assertNotIn("DELIVERY", records)
        self.assertEqual(prepared["missing"][0]["metric_code"], "ORDER")
        self.assertEqual(prepared["exceptions"][0]["metric_code"], "DELIVERY")
        self.assertIsNone(prepared["coverage"][0]["fill_rate"])
        outside = prepare([facts(venta_piezas="1.12345678901234567")])
        self.assertNotIn("SALES", {r["metric_code"] for r in outside["import"]["observations"]})

    def test_ce_is_retained_outside_selected_rs_and_shared_destination_is_quarantined(self):
        ce = facts(tipo_compra="CE")
        prepared = prepare([ce])
        self.assertEqual(prepared["import"]["observations"], [])
        self.assertEqual(prepared["import"]["inventory"], [])
        self.assertEqual(len(prepared["ce"]), 6)
        self.assertEqual(prepared["ce"][0]["purchase_type"], "CE")
        collision = prepare([facts(), facts(fila="2", tipo_compra="CE")])
        self.assertEqual(collision["import"]["observations"], [])
        self.assertEqual(collision["stats"]["structural_exception_rows"], {"RS": 1, "CE": 1})

    def test_date_duplicates_and_unmapped_catalog_never_drop_the_valid_other_month(self):
        data = [facts(), facts(fila="2", periodo_fecha_q="2025-08", periodo_etiqueta_rs="2025-09"),
                facts(fila="3", upc="000099", periodo_fecha_q="2025-09", periodo_etiqueta_rs="2025-09")]
        prepared = prepare(data)
        self.assertEqual(len(prepared["import"]["observations"]), 5)
        self.assertEqual(prepared["stats"]["structural_exception_rows"], {"RS": 2})
        self.assertEqual(len(prepared["catalog"]["source_identities"]), 3)
        pending = [r for r in prepared["catalog"]["source_identities"] if r["upc"] == "000099"]
        self.assertEqual(len(pending), 1)
        self.assertIsNone(pending[0]["product_id"])
        self.assertEqual(pending[0]["status"], "EXCEPTION")
        duplicates = prepare([facts(), facts(fila="2")])
        self.assertEqual(duplicates["import"]["observations"], [])

    def test_master_references_and_saved_proposals_keep_semantics_outside_operational_facts(self):
        reference = {"hoja": "Towell", "cuenta": "account", "upc": "000001", "periodo": "2024-01",
                     "valor_lexico": "999", "celda": "Towell!K6"}
        plan = {"cuenta": "account", "upc": "000001", "periodo_objetivo": "2026-12",
                "propuesta_comercial_piezas": "888", "codigo_comercial": "COM-1", "formato": "A"}
        prepared = prepare([facts()], refs=[reference], plans=[plan])
        self.assertEqual(prepared["references"][0]["metric_code"], "FCST_TOWELL_HISTORY")
        self.assertFalse(prepared["references"][0]["operational"])
        self.assertFalse(prepared["plans"][0]["operational"])
        self.assertEqual(prepared["plans"][0]["source_sha256"], BOOKS["master"]["source_hash"])
        self.assertNotIn("999", {r["value"] for r in prepared["import"]["observations"]})
        self.assertNotIn("888", {r["value"] for r in prepared["import"]["observations"]})
        self.assertEqual({r["source_id"] for r in prepared["import"]["observations"]}, {BOOKS["closing"]["id"]})

    def test_reproducibility_and_unique_source_keys(self):
        first, second = prepare([facts()]), prepare([facts()])
        self.assertEqual(first, second)
        records = first["import"]["observations"] + first["import"]["inventory"]
        self.assertEqual(len({r["id"] for r in records}), len(records))
        self.assertEqual(len({(r["chain_id"], r["product_id"], r["period"], r["metric_code"]) for r in records}), len(records))
        changed = prepare([facts(venta_piezas="1")])
        self.assertNotEqual(first["import"]["source_hash"], changed["import"]["source_hash"])

    def test_pinned_master_bindings_require_unchanged_catalog_and_identity_file(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "Identidades.json"
            path.write_text(json.dumps(identities()), encoding="utf-8")
            self.assertEqual(_master_bindings(path, file_hash(path), make_snapshot()), identities())
            with self.assertRaisesRegex(ValueError, "master_identity_file_changed"):
                _master_bindings(path, "0" * 64, make_snapshot())
            snapshot = make_snapshot()
            snapshot["products"][0]["status"] = "INACTIVE"
            with self.assertRaisesRegex(ValueError, "master_identity_catalog_changed"):
                _master_bindings(path, file_hash(path), snapshot)

    def test_cached_reconciliation_is_pinned_and_covers_every_source_row(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "Manifest.json"
            manifest.write_text(json.dumps({"input_hashes": {}, "results_sha256": {}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "reconciliation_manifest_hash_mismatch"):
                _cached_bindings(root, "0" * 64, {}, [facts()])
            manifest.write_text(json.dumps({"input_hashes": {"file": "1"}, "results_sha256": {}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "reconciliation_source_changed"):
                _cached_bindings(root, file_hash(manifest), {}, [facts()])


if __name__ == "__main__":
    unittest.main()
