"""Synthetic workbooks: W/X fulfillment cannot replace target or stock data."""
from copy import deepcopy
from datetime import datetime
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook

from .fulfillment_source import BOOK, read_fulfillment_source


class FulfillmentSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / BOOK
        self.snapshot = {"chains": [{"id": "chain"}],
            "products": [{"id": f"p{i}", "chain_id": "chain", "status": "ACTIVE"} for i in (1, 2)],
            "profiles": [{"chain_id": "chain", "mapping_json": {
                "scope": {"level": "COMMERCIAL_UNIT", "actual_bearing": True, "code": "Store::BD", "parent_code": "Store"},
                "products": [{"id": f"p{i}", "identifier_mappings": [[None, f"00000{i}"]]} for i in (1, 2)]}}], "observations": []}

    def book(self, rows, *, header_changes=None):
        book = Workbook()
        sheet = book.active
        sheet.title = "BAASE"
        headers = {"B": "Cadena", "C": "Tipo Compra", "D": "Formato", "H": "UPC", "Q": "Fecha", "R": "Año", "S": "Mes", "W": "Pedido", "X": "Entrega"}
        for column, value in {**headers, **(header_changes or {})}.items():
            sheet[column + "17"] = value
        for rowno, changes in enumerate(rows, 18):
            original = {"B": "Store", "C": "RS", "D": "BD", "H": "000001", "Q": datetime(2026, 10, 15),
                        "R": 2026, "S": "Octubre", "W": 100, "X": 80, "T": 7, "U": 8, "V": 9, "Y": 10}
            for column, value in {**original, **changes}.items():
                sheet[f"{column}{rowno}"] = value
        book.save(self.path)
        book.close()
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def read(self, rows, **kwargs):
        digest = self.book(rows, **kwargs)
        return read_fulfillment_source(self.path, digest, self.snapshot)

    def test_exact_columns_zero_decimal_upc_scope_and_partial_october(self):
        rows, source, errors = self.read([{"W": 0, "X": 0}, {"H": "000002", "Q": datetime(2026, 9, 30), "S": "Septiembre", "W": 12.125, "X": 10.5}])
        self.assertFalse(errors)
        self.assertEqual((rows[0]["upc"], rows[0]["product_id"], rows[0]["order_value"], rows[0]["delivery_value"]), ("000001", "p1", "0", "0"))
        self.assertTrue(rows[0]["partial_period"])
        self.assertFalse(rows[1]["partial_period"])
        self.assertEqual((rows[1]["order_value"], rows[1]["delivery_value"]), ("12.125", "10.5"))
        self.assertEqual(rows[0]["source_reference"], "BAASE!W18:X18")
        self.assertEqual(rows[0]["source_sha256"], source["source_hash"])
        self.assertIn("FILL RATE ONLY", source["reference"])
        self.assertNotIn("metric_code", rows[0])

    def test_missing_is_none_and_a_single_missing_product_does_not_block_others(self):
        rows, _, errors = self.read([{"W": None, "X": 0}, {"H": "000002", "W": 0, "X": None}])
        self.assertFalse(errors)
        self.assertEqual([(r["order_value"], r["delivery_value"]) for r in rows], [(None, "0"), ("0", None)])

    def test_never_uses_other_series_inventory_or_special_purchase(self):
        first, _, _ = self.read([{}, {"C": "CE", "H": "000002", "W": 888, "X": 999}])
        second, _, _ = self.read([{"T": "=1/0", "U": -9999, "V": "poison", "Y": -1}, {"C": "CE", "H": "000002", "W": -3, "X": -4}])
        ignore = {"source_sha256", "source_id"}
        self.assertEqual([{k: v for k, v in r.items() if k not in ignore} for r in first],
                         [{k: v for k, v in r.items() if k not in ignore} for r in second])
        self.assertEqual(len(second), 1)

    def test_ambiguous_identity_dates_and_duplicate_rows_are_isolated(self):
        rows, _, errors = self.read([{}, {}, {"H": "000002", "Q": datetime(2026, 9, 30), "S": "Septiembre"},
            {"H": "missing", "Q": datetime(2026, 8, 1), "S": "Agosto"},
            {"H": "000002", "Q": datetime(2026, 7, 1), "S": "Agosto"}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["period"], "2026-09")
        self.assertEqual(len(errors), 4)
        self.assertTrue(any("FULFILLMENT_DUPLICATE_SCOPE_PRODUCT_MONTH" in e["reasons"] for e in errors))
        self.assertTrue(any("upc_not_in_scope" in e["reasons"] for e in errors))

    def test_formula_and_negative_pair_are_not_accepted_as_cached_actuals(self):
        rows, _, errors = self.read([{"W": "=50+50"}, {"H": "000002", "W": -1}])
        self.assertFalse(rows)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("FORMULA_FULFILLMENT_REVIEW:W" in e["reasons"] for e in errors))

    def test_wrong_hash_and_changed_header_fail_before_returning_any_pair(self):
        self.book([{}])
        with self.assertRaisesRegex(ValueError, "credited_fulfillment_book_changed"):
            read_fulfillment_source(self.path, "a" * 64, self.snapshot)
        sha = self.book([{}], header_changes={"W": "Forecast"})
        with self.assertRaisesRegex(ValueError, "fulfillment_headers_changed"):
            read_fulfillment_source(self.path, sha, self.snapshot)

    def test_cross_chain_upc_collision_is_never_resolved_by_product_name(self):
        other = deepcopy(self.snapshot)
        other["profiles"][0]["mapping_json"]["products"].append({"id": "p2", "identifier_mappings": [[None, "000001"]]})
        sha = self.book([{}, {"H": "000002"}])
        rows, _, errors = read_fulfillment_source(self.path, sha, other)
        self.assertEqual([r["product_id"] for r in rows], ["p2"])
        self.assertEqual(errors[0]["reasons"], ["ambiguous_upc_in_scope"])


if __name__ == "__main__":
    unittest.main()
