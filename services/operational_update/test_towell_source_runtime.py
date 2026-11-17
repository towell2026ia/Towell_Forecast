"""Synthetic source assembly tests; never import real files or run forecasts."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import Workbook

from .official_forecast import engine_records
from .preparation import REPO
from .towell_sales_source import MASTER_NAME, read_towell_sales_source
from .towell_source_runtime import CONTRACT, POLICY, forecast_source, prepare_packet, read_inventory_source

CHAIN = "11111111-1111-4111-8111-111111111111"
PRODUCTS = [f"22222222-2222-4222-8222-{i:012d}" for i in range(1, 4)]
STOCK_SOURCE = {"id": "33333333-3333-4333-8333-333333333333", "source_hash": "a" * 64,
                "source_name": "synthetic-inventory.xlsx", "reference": "BAASE!Y INVENTORY ONLY"}


class TowellSourceRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "datos_privados").mkdir()
        self.rules = json.loads((REPO / "config/commercial_catalog_rules.json").read_text(encoding="utf-8"))
        self.snapshot = {"chains": [{"id": CHAIN}],
            "products": [{"id": p, "chain_id": CHAIN, "status": "ACTIVE"} for p in PRODUCTS],
            "profiles": [{"chain_id": CHAIN, "mapping_json": {
                "scope": {"level": "COMMERCIAL_UNIT", "actual_bearing": True, "code": "Store::A", "parent_code": "Store"},
                "products": [{"id": p, "identifier_mappings": [[None, f"{i:06d}"]]}
                             for i, p in enumerate(PRODUCTS, 1)]}}],
            "observations": [{"chain_id": CHAIN, "product_id": PRODUCTS[0], "period": "2024-01",
                              "metric_code": "SALES", "value": "999999", "observation_id": "old", "version_no": 1}]}
        self.bindings = [self.binding(i) for i in range(1, 4)]
        self.document = self.document_from_rows([
            {"J": "Septiembre", "M": 12.125}, {"J": "Octubre", "M": 0},
            {"E": "000002", "J": "Septiembre", "M": None}, {"E": "000002", "J": "Octubre", "M": 4.25}])

    @staticmethod
    def binding(index, **changes):
        return {"id": f"synthetic-binding-{index}", "account": "ACCOUNT", "chain": "Store", "format": "A",
                "barcode": f"{index:06d}", "chain_id": CHAIN, "product_id": PRODUCTS[(index - 1) % 3],
                "state": "COINCIDENCIA_VERIFICADA", **changes}

    def document_from_rows(self, rows):
        book = Workbook()
        sheet = book.active
        sheet.title = "Towell"
        for c, v in {"K": "Venta 2024", "L": "Venta 2025", "M": "Venta 2026"}.items():
            sheet[c + "5"] = v
        for n, extra in enumerate(rows, 6):
            original = {"A": "ACCOUNT", "B": "Store", "D": "A", "E": "000001", "F": "TOALLA MB DESIGN BLUE",
                        "J": "Octubre", "K": None, "L": None, "M": 1,
                        "AD": "COMMERCIAL-CODE", "AE": "TOALLA MB DESIGN", "AF": "MB", "AH": "BLUE"}
            for column, value in {**original, **extra}.items():
                sheet[f"{column}{n}"] = value
        book.save(self.root / "datos_privados" / MASTER_NAME)
        book.close()
        return read_towell_sales_source(self.root)

    def stock(self, **changes):
        return {"chain_id": CHAIN, "product_id": PRODUCTS[0], "format": "A", "upc": "000001",
                "purchase_type": "RS", "period": "2026-10", "metric_code": "INVENTORY_AGGREGATE",
                "value": "9.1234567890123456", "source_value": "9.1234567890123456", "source_id": STOCK_SOURCE["id"],
                "source_sheet": "BAASE", "source_reference": "BAASE!Y18", "model": "ORIGINAL STOCK LABEL",
                "presentation": "MB", "color": "BLUE", "product_type": "Toalla", "account": None,
                "commercial_code": None, **changes}

    def prepare(self, document=None, bindings=None, snapshot=None, stock=None):
        return prepare_packet(document or self.document, self.bindings if bindings is None else bindings,
            self.snapshot if snapshot is None else snapshot, [self.stock()] if stock is None else stock,
            STOCK_SOURCE, rules=self.rules)

    def test_four_source_rows_keep_exact_sales_zero_blanks_and_independent_inventory(self):
        originals = deepcopy((self.document, self.bindings, self.snapshot))
        result = self.prepare()
        facts = result["import"]["observations"]
        self.assertEqual(len(facts), 3)
        self.assertEqual({r["metric_code"] for r in facts}, {"SALES"})
        self.assertEqual({r["source_reference"] for r in facts}, {"Towell!M6", "Towell!M7", "Towell!M9"})
        self.assertEqual([r["value"] for r in facts], ["12.125", "0", "4.25"])
        self.assertEqual(len(result["references"]), 9)
        self.assertTrue(all(r["value"] is None for r in result["references"]))
        stock = result["import"]["inventory"][0]
        self.assertEqual(stock["value"], "9.1234567890123456")
        self.assertEqual(stock["source_reference"], "BAASE!Y18")
        self.assertNotIn("store_id", stock)
        self.assertEqual((stock["model"], stock["presentation"]), ("DESIGN", "MEDIO BAÑO"))
        self.assertEqual(stock["source_native_attributes"]["model"], "ORIGINAL STOCK LABEL")
        self.assertEqual(stock["canonical_attribute_source"]["master_sha256"], self.document["source"]["sha256"])
        self.assertEqual((self.document, self.bindings, self.snapshot), originals)

    def test_old_observations_never_enter_new_source_and_inventory_changes_only_provenance(self):
        original = self.prepare()
        older = deepcopy(self.snapshot)
        older["observations"].append({**older["observations"][0], "value": "8888888", "metric_code": "DELIVERY", "observation_id": "old-delivery"})
        self.assertEqual(original, self.prepare(snapshot=older))
        changed = self.prepare(stock=[self.stock(value="77", source_value="77")])
        self.assertNotEqual(original["import"]["source_hash"], changed["import"]["source_hash"])
        self.assertEqual(changed["import"]["inventory"][0]["value"], "77")
        before, _ = engine_records(original["import"]["observations"], "2026-10")
        after, _ = engine_records(changed["import"]["observations"], "2026-10")
        # Record IDs fingerprint both sources; quantities/features must not.
        without_ids = lambda rows: [{k: v for k, v in r.items() if k != "source_id"} for r in rows]
        self.assertEqual(without_ids(before), without_ids(after))
        self.assertEqual({r["objective"] for r in before}, {"Venta"})
        self.assertFalse(any(r["value"] in {"77", "999999", "8888888"} for r in after))

    def test_seventy_one_synthetic_unresolved_identities_do_not_block_one_verified_product(self):
        document = self.document_from_rows([{"E": f"{i:06d}", "M": i} for i in range(1, 73)])
        pending = [self.binding(i, state="REVISION_REQUERIDA", product_id=None) for i in range(2, 73)]
        result = self.prepare(document=document, bindings=[self.binding(1), *pending], stock=[])
        self.assertEqual(len(result["catalog"]), 72)
        self.assertEqual(result["summary"]["binding_states"], {"COINCIDENCIA_VERIFICADA": 1, "REVISION_REQUERIDA": 71})
        self.assertEqual(len(result["import"]["observations"]), 1)
        self.assertTrue(all(r["reason"] == "NO_UNIQUE_ACCREDITED_ACCOUNT_FORMAT_UPC_BINDING"
                            for r in result["catalog"] if r["state"] == "REVISION_REQUERIDA"))
        self.assertEqual({r["product_id"] for r in result["import"]["observations"]}, {PRODUCTS[0]})
        self.assertEqual(len({r["identity_key"] for r in result["references"] if r["binding_state"] == "REVISION_REQUERIDA"}), 71)

    def test_chain_contradiction_and_duplicate_target_are_isolated_not_guessed(self):
        result = self.prepare(bindings=[self.binding(1, chain="Other Chain"), self.binding(2)])
        self.assertEqual({r["product_id"] for r in result["import"]["observations"]}, {PRODUCTS[1]})
        document = self.document_from_rows([{"E": f"{i:06d}"} for i in (1, 2, 3)])
        result = self.prepare(document=document, bindings=[self.binding(1), self.binding(2, product_id=PRODUCTS[0]), self.binding(3)], stock=[])
        self.assertEqual(result["summary"]["binding_states"], {"AMBIGUO": 2, "COINCIDENCIA_VERIFICADA": 1})
        self.assertEqual({r["product_id"] for r in result["import"]["observations"]}, {PRODUCTS[2]})
        self.assertTrue(all(r["reason"] == "PRIVATE_IDENTITIES_SHARE_TARGET" for r in result["catalog"] if r["state"] == "AMBIGUO"))

    def test_decimal_precision_is_exact_or_an_individual_exception_never_rounded(self):
        for literal, accepted in (("0.1234567890123456", True), ("0.12345678901234567", False)):
            document = deepcopy(self.document)
            row = next(r for r in document["records"] if r["source_reference"] == "Towell!M6")
            row.update(value=Decimal(literal), value_text=literal, source_value_literal=literal)
            result = self.prepare(document=document)
            target = [r for r in result["import"]["observations"] if r["source_reference"] == "Towell!M6"]
            self.assertEqual(len(target), int(accepted))
            if accepted:
                self.assertEqual(target[0]["value"], literal)
                self.assertEqual(target[0]["source_raw_value_literal"], literal)
            else:
                self.assertIn({"identity_key": row["identity_key"], "reason": "EXACT_QUANTITY_OUTSIDE_EXISTING_IMPORT_CONTRACT",
                               "source_reference": "Towell!M6", "source_value_literal": literal}, result["exceptions"])
            self.assertEqual(len(result["import"]["observations"]), 2 + int(accepted))

    def test_wrong_sales_or_inventory_lineage_fails_closed_without_adapting_sources(self):
        for changes in ({"sheet": "Cliente"}, {"metric_code": "ORDER"}, {"source_reference": "Towell!R6"},
                        {"available_at": "2026-10-01"}, {"has_formula": True}):
            document = deepcopy(self.document)
            document["records"][2].update(changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "towell_sales_provenance_invalid"):
                self.prepare(document=document)
        for changes in ({"metric_code": "SALES"}, {"source_reference": "BAASE!V18"}, {"purchase_type": "CE"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "inventory_only_credited_baase_y_required"):
                self.prepare(stock=[self.stock(**changes)])

    def test_unmapped_inventory_is_an_exception_while_good_sales_continue(self):
        result = self.prepare(stock=[self.stock(upc="009999")])
        self.assertEqual(len(result["import"]["observations"]), 3)
        self.assertEqual(result["import"]["inventory"], [])
        self.assertIn({"reason": "INVENTORY_IDENTITY_NOT_UNIQUE", "source_reference": "BAASE!Y18"}, result["exceptions"])

    def test_source_tags_are_reproducible_new_and_forbid_prior_results(self):
        first, second = self.prepare(), self.prepare()
        self.assertEqual(first, second)
        self.assertEqual(first["policy"]["contract"], POLICY)
        self.assertTrue(first["policy"]["no_old_forecast_reuse"])
        self.assertEqual(first["forecast_source_context"], {
            "sales_contract": CONTRACT, "sales_file_sha256": self.document["source"]["sha256"], "sheet": "Towell",
            "inventory_source": "ESTADISTICA", "authorized_source_hash": first["import"]["source_hash"]})
        facts = first["import"]["observations"] + first["import"]["inventory"]
        self.assertEqual(len({r["id"] for r in facts}), len(facts))
        self.assertFalse(any(r.get("available_at") for r in facts))

    def test_forecast_wrapper_reuses_existing_entry_with_explicit_source_and_generation(self):
        directory = self.root / "packet"
        directory.mkdir()
        context = self.prepare()["forecast_source_context"]
        (directory / "Manifest.json").write_text(json.dumps({"policy": {"contract": POLICY}, "generation": 2,
            "first_period": "2024-01", "forecast_source_context": context}), encoding="utf-8")
        compute = object()
        with patch("services.operational_update.towell_source_runtime.run_existing_forecast", return_value={"families": {}}) as run:
            forecast_source(directory, "b" * 64, self.root / "results", compute=compute)
        run.assert_called_once_with(directory, "b" * 64, self.root / "results", first_period="2024-01",
            context_generation=2, extra_source_context=context, compute=compute)
        self.assertNotIn("reuse_from", run.call_args.kwargs)

    def test_inventory_reader_ignores_all_statistical_sales_and_forecast_columns(self):
        path = self.root / "Estadistica Todas las Cadenas Cierre de Octubre 2026.xlsx"
        book = Workbook()
        sheet = book.active
        sheet.title = "BAASE"
        for column, label in {"B": "CADENA", "C": "TIPO COMPRA", "D": "FORMATO", "H": "UPC", "Q": "FECHA",
                              "R": "ANO", "S": "MES", "Y": "INVENTARIO"}.items():
            sheet[column + "17"] = label
        for row, purchase, stock in ((18, "RS", 0), (19, "CE", 999999)):
            for column, value in {"B": "Store", "C": purchase, "D": "A", "E": "Toalla", "F": "DESIGN",
                                  "G": None, "H": "000001", "I": "SYNTHETIC", "J": "BLUE", "K": "MB",
                                  "Q": datetime(2026, 10, 31), "R": 2026, "S": "Octubre", "Y": stock,
                                  "T": "=1/0", "U": 888888, "V": 777777, "W": -999, "X": 666666}.items():
                sheet[f"{column}{row}"] = value
        book.save(path)
        first_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        first, source, issues = read_inventory_source(path, first_hash, self.snapshot, cutoff="2026-10")
        self.assertFalse(issues)
        self.assertEqual(len(first), 1)
        self.assertEqual((first[0]["metric_code"], first[0]["value"]), ("INVENTORY_AGGREGATE", "0"))
        self.assertEqual(first[0]["source_reference"], "BAASE!Y18")
        for column in ("T", "U", "V", "W", "X"):
            sheet[column + "18"] = -123456789
        book.save(path)
        book.close()
        second_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        second, next_source, next_issues = read_inventory_source(path, second_hash, self.snapshot, cutoff="2026-10")
        self.assertFalse(next_issues)
        self.assertNotEqual(source["source_hash"], next_source["source_hash"])
        exclude = {"source_id", "source_sha256"}
        self.assertEqual([{k: v for k, v in r.items() if k not in exclude} for r in first],
                         [{k: v for k, v in r.items() if k not in exclude} for r in second])


if __name__ == "__main__":
    unittest.main()
