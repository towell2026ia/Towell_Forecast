from datetime import date
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook

from .baase_reported import BOOK, HEADERS, read_reported_source


class ReportedBaaseTests(unittest.TestCase):
    def test_monthly_percent_stock_and_missing_are_independent(self):
        book = Workbook()
        sheet = book.active
        sheet.title = "BAASE"
        for col, label in HEADERS.items():
            sheet[col + "17"] = label
        for month, stock, pct, name in [(1, 0, .875, "Enero"), (2, 7, 0, "Febrero"), (3, 4, "=IFERROR(#REF!/#REF!,0)", "Marzo")]:
            for col, value in {"B": "Cadena", "C": "RS", "D": "Local", "G": "Item", "H": "001", "O": date(2026, month, 1),
                               "P": 2026, "Q": name, "R": stock, "S": pct}.items():
                sheet[f"{col}{17 + month}"] = value
            sheet[f"S{17 + month}"].number_format = "0%"
        catalog = {"observations": [], "products": [{"id": "p", "chain_id": "c", "status": "ACTIVE"}],
                   "profiles": [{"chain_id": "c", "mapping_json": {"scope": {"level": "COMMERCIAL_UNIT", "actual_bearing": True,
                       "code": "Cadena::Local"}, "products": [{"id": "p", "identifier_mappings": [["Item", "001"]]}]}}]}
        with TemporaryDirectory() as root:
            path = Path(root) / BOOK
            book.save(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            stock, rates, _, errors, missing, products = read_reported_source(path, digest, catalog, cutoff="2026-10")
            self.assertEqual([p["upc"] for p in products], ["001"])
            self.assertEqual([r["value"] for r in stock], ["7", "4"])
            self.assertEqual([r["reported_fill_rate_pct"] for r in rates], ["87.500"])
            self.assertEqual(missing, {"R": 1, "S": 1})
            self.assertEqual(errors[0]["source_reference"], "BAASE!S20")
            self.assertNotIn("order_value", rates[0])
            with self.assertRaisesRegex(ValueError, "source_mismatch"):
                read_reported_source(path, "0" * 64, catalog, cutoff="2026-10")
        book.close()
