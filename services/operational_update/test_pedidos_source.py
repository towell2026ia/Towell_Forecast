"""Synthetic Pedido capture/closure contracts; no real source, database or forecast."""
from copy import deepcopy
from collections import Counter
from decimal import Decimal
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook, load_workbook

from .pedidos_order_source import read_pedidos_order_source
from .pedido_source_runtime import BLOCK_CODE, prepare_packet
from .preparation import REPO
from .towell_sales_source import MASTER_NAME, TowellSourceError, read_towell_sales_source
from .towell_source_runtime import prepare_packet as prepare_sales
from .unification import json_read

CHAIN = "11111111-1111-4111-8111-111111111111"
PRODUCT = "22222222-2222-4222-8222-222222222222"
STOCK = {"id": "33333333-3333-4333-8333-333333333333", "source_hash": "a" * 64,
         "source_name": "synthetic-stock.xlsx", "reference": "BAASE!Y INVENTORY ONLY"}


class PedidosSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "datos_privados").mkdir()
        self.path = self.root / "datos_privados" / MASTER_NAME
        self.rules = json_read(REPO / "config/commercial_catalog_rules.json")
        self.snapshot = {"chains": [{"id": CHAIN}], "products": [{"id": PRODUCT, "chain_id": CHAIN, "status": "ACTIVE"}],
            "profiles": [{"chain_id": CHAIN, "mapping_json": {"scope": {"code": "Store::A", "parent_code": "Store",
                "level": "COMMERCIAL_UNIT", "actual_bearing": True},
                "products": [{"id": PRODUCT, "identifier_mappings": [[None, "000001"]]}]}}], "observations": []}
        self.bindings = [{"id": "synthetic", "account": "ACCOUNT", "chain": "Store", "format": "A", "barcode": "000001",
            "chain_id": CHAIN, "product_id": PRODUCT, "state": "COINCIDENCIA_VERIFICADA"}]
        self.make_book()

    def make_book(self, order=12.125, sales=3.25, others=999999):
        book = Workbook()
        book.remove(book.active)
        for name in ("Towell", "Pedidos", "Cliente"):
            sheet = book.create_sheet(name)
            for col, text in {"K": "Venta 2024", "L": "Venta 2025", "M": "Venta 2026"}.items():
                sheet[col + "5"] = text
            for n, month, amount in ((6, "Septiembre", order), (7, "Octubre", 0), (8, "Noviembre", None)):
                values = {"A": "ACCOUNT", "B": "Store", "D": "A", "E": "000001", "F": "TOALLA MB DESIGN BLUE",
                    "J": month, "K": None, "L": None, "M": amount if name == "Pedidos" else sales,
                    "AD": "COMMERCIAL", "AE": "TOALLA MB DESIGN", "AF": "MB", "AH": "BLUE",
                    "O": others, "R": others, "U": others, "X": others, "AA": others, "AB": others}
                if name == "Cliente":
                    values["M"] = others
                for col, value in values.items():
                    sheet[f"{col}{n}"] = value
        book.save(self.path)
        book.close()

    def prepared(self, document=None, bindings=None):
        sales = read_towell_sales_source(self.root)
        stock = {"chain_id": CHAIN, "product_id": PRODUCT, "purchase_type": "RS", "period": "2026-10",
            "format": "A", "upc": "000001", "metric_code": "INVENTORY_AGGREGATE", "value": "4.25",
            "source_value": "4.25", "source_id": STOCK["id"], "source_sheet": "BAASE", "source_reference": "BAASE!Y18",
            "model": "STOCK ORIGINAL", "presentation": "MB", "color": "BLUE", "product_type": "TOALLA"}
        g2 = prepare_sales(sales, self.bindings, self.snapshot, [stock], STOCK, rules=self.rules)["import"]
        return prepare_packet(document or read_pedidos_order_source(self.root), bindings or self.bindings,
            self.snapshot, g2, rules=self.rules), g2

    def test_unknown_closure_keeps_captured_quantities_queryable_without_training(self):
        doc = read_pedidos_order_source(self.root)
        self.assertEqual(doc["source"]["sheet"], "Pedidos")
        self.assertEqual(doc["source"]["metric_code"], "ORDER")
        self.assertIsNone(doc["closure"]["last_closed_period"])
        self.assertFalse(doc["closure"]["forecast_available"])
        self.assertEqual(doc["closure"]["forecast_block_code"], BLOCK_CODE)
        rows = {r["source_reference"]: r for r in doc["records"]}
        self.assertEqual(rows["Pedidos!M6"]["value"], Decimal("12.125"))
        self.assertEqual(rows["Pedidos!M7"]["quantity_state"], "EXPLICIT_ZERO")
        self.assertEqual(rows["Pedidos!M8"]["quantity_state"], "EMPTY")
        self.assertEqual(sum(r["capture_eligible"] for r in rows.values()), 2)
        self.assertFalse(any(r["eligible"] for r in rows.values()))
        self.assertEqual(doc["summary"]["last_period_with_quantity"], "2026-10")
        self.assertIsNone(doc["summary"]["last_eligible_period"])

    def test_sales_closure_never_implicitly_closes_orders(self):
        self.assertEqual(read_towell_sales_source(self.root)["closure"]["last_closed_period"], "2026-10")
        with self.assertRaisesRegex(TowellSourceError, "PEDIDOS_SOURCE_SPECIFIC_CLOSURE_REQUIRED"):
            read_pedidos_order_source(self.root, confirmed_closed_period="2026-10", closure_evidence={"sheet": "Towell"})
        with self.assertRaisesRegex(TowellSourceError, "PEDIDOS_CLOSURE_PERIOD_REQUIRED"):
            read_pedidos_order_source(self.root, closure_evidence={"sheet": "Pedidos"})

    def test_explicit_future_source_closure_controls_training_only(self):
        evidence = {"sheet": "Pedidos", "metric_code": "ORDER", "last_closed_period": "2026-09",
            "confirmed_by": "PROJECT_OWNER_USER", "reference": "SYNTHETIC TEST ONLY", "statement": "Synthetic closed September",
            "document_sha256": "f" * 64, "available_at": None}
        doc = read_pedidos_order_source(self.root, confirmed_closed_period="2026-09", closure_evidence=evidence)
        byref = {r["source_reference"]: r for r in doc["records"]}
        self.assertTrue(byref["Pedidos!M6"]["eligible"])
        self.assertFalse(byref["Pedidos!M7"]["eligible"])
        self.assertTrue(byref["Pedidos!M7"]["capture_eligible"])
        self.assertEqual(byref["Pedidos!M7"]["state"], "NOT_CLOSED")
        self.assertIsNone(doc["closure"]["available_at"])

    def test_other_sheets_and_proposals_never_change_order_records(self):
        before = read_pedidos_order_source(self.root)
        book = load_workbook(self.path)
        for name in ("Towell", "Cliente"):
            book[name]["M6"] = 87654321
        for col in ("O", "R", "U", "X", "AA", "AB"):
            book["Pedidos"][col + "6"] = 87654321
        book.save(self.path)
        book.close()
        after = read_pedidos_order_source(self.root)
        normalize = lambda rows: [{k: v for k, v in r.items() if k != "source_sha256"} for r in rows]
        self.assertEqual(normalize(before["records"]), normalize(after["records"]))
        self.assertNotEqual(before["source"]["sha256"], after["source"]["sha256"])

    def test_no_master_no_pedidos_sheet_and_wrong_hash_fail_without_fallback(self):
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        read_pedidos_order_source(self.root, expected_sha256=digest)
        with self.assertRaisesRegex(TowellSourceError, "approved_towell_master_changed"):
            read_pedidos_order_source(self.root, expected_sha256="0" * 64)
        book = load_workbook(self.path)
        del book["Pedidos"]
        book.save(self.path)
        book.close()
        with self.assertRaisesRegex(TowellSourceError, "PEDIDOS_SHEET_MISSING"):
            read_pedidos_order_source(self.root)
        self.path.rename(self.path.with_name("Copia Copia de Pronostico RS Dic Def V1.xlsx"))
        with self.assertRaisesRegex(TowellSourceError, "TOWELL_MASTER_NOT_FOUND"):
            read_pedidos_order_source(self.root)

    def test_packet_separates_order_from_informative_sales_and_inventory(self):
        result, g2 = self.prepared()
        facts = result["import"]["observations"]
        self.assertEqual(Counter(r["metric_code"] for r in facts), {"ORDER": 2, "SALES": 2})
        self.assertEqual([r["value"] for r in facts if r["metric_code"] == "ORDER"], ["12.125", "0"])
        self.assertEqual([r["value"] for r in facts if r["metric_code"] == "SALES"], [r["value"] for r in g2["observations"]])
        self.assertEqual(result["import"]["inventory"][0]["value"], "4.25")
        self.assertEqual(len(result["import"]["sources"]), 2)
        self.assertIsNone(result["summary"]["cutoff"])
        self.assertIsNone(result["forecast_source_context"]["training_cutoff"])
        self.assertFalse(result["forecast_source_context"]["forecast_available"])
        self.assertTrue(result["forecast_source_context"]["target_source_available"])
        self.assertEqual(result["forecast_source_context"]["forecast_contract"], "pedido-towell-v1")
        self.assertEqual(result["forecast_source_context"]["forecast_block_code"], BLOCK_CODE)
        self.assertTrue(all(r["source_reference"].startswith("Pedidos!") for r in facts if r["metric_code"] == "ORDER"))
        self.assertEqual(len({r["id"] for r in facts + result["import"]["inventory"]}), 5)

    def test_bad_precision_is_an_individual_exception_and_old_source_not_a_substitute(self):
        doc = read_pedidos_order_source(self.root)
        row = next(r for r in doc["records"] if r["source_reference"] == "Pedidos!M6")
        row.update(value=Decimal("0.12345678901234567"), value_text="0.12345678901234567",
                   source_value_literal="0.12345678901234567")
        result, _ = self.prepared(document=doc)
        self.assertEqual(result["summary"]["order_observations"], 1)
        self.assertIn("EXACT_QUANTITY_OUTSIDE_EXISTING_IMPORT_CONTRACT", {r["reason"] for r in result["exceptions"]})
        wrong = deepcopy(doc)
        next(r for r in wrong["records"] if r["source_reference"] == "Pedidos!M7")["sheet"] = "Towell"
        with self.assertRaisesRegex(ValueError, "captured_pedidos_provenance_invalid"):
            self.prepared(document=wrong)

    def test_unresolved_identity_does_not_gain_a_link_from_g2_or_name(self):
        doc = read_pedidos_order_source(self.root)
        # One valid identity continues while an extra same-name unlinked one remains a reference.
        original = deepcopy(doc["identities"][0])
        original.update(identity_key="unresolved", upc="000099")
        doc["identities"].append(original)
        row = deepcopy(next(r for r in doc["records"] if r["source_reference"] == "Pedidos!M6"))
        row.update(identity_key="unresolved", source_reference="Pedidos!M99", row=99)
        doc["records"].append(row)
        result, _ = self.prepared(document=doc)
        self.assertEqual(result["summary"]["order_products"], 1)
        self.assertEqual(result["summary"]["binding_states"], {"COINCIDENCIA_VERIFICADA": 1, "REVISION_REQUERIDA": 1})
        self.assertEqual([r["source_reference"] for r in result["references"] if r["identity_key"] == "unresolved"], ["Pedidos!M99"])


if __name__ == "__main__":
    unittest.main()
