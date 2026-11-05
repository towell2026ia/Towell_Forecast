"""Reader boundary tests; synthetic cells, no commercial files or workbook writes."""
import importlib.util
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("unit_import", Path(__file__).with_name("import-unit-catalog.py"))
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


class Sheet:
    def __init__(self, header, row):
        self.header, self.row = header, row

    def iter_rows(self, min_row, **_):
        return iter([self.header] if min_row == 5 else [self.row, [SimpleNamespace(value=None) for _ in self.row]])


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.values = ["Towell", "TEST", "Synthetic", "Test", "00123", "Model", "MB", "VARIOS",
                       None, None, "piezas", None, None, None, "Pendiente", None, None, "Sí", "12 / 24", "2026-10", "TEST|00123"]
        self.header = [SimpleNamespace(value=v, data_type="s") for v in reader.HEADERS]
        self.cells = [SimpleNamespace(value=v, data_type="s") for v in self.values]
        self.guide = {"B7": SimpleNamespace(value="V1"), "B8": SimpleNamespace(value="hash")}
        self.bundle = {"versions": [{"id": "V1", "hash": "hash"}]}

    def read(self):
        book = {"Equivalencias": Sheet(self.header, self.cells), "Guia": self.guide}

        class Book(dict):
            closed = False

            def close(self):
                self.closed = True

        obj = Book(book)
        with patch.object(reader, "load_workbook", return_value=obj) as loader:
            try:
                result = reader.read_catalog("synthetic.xlsx", self.bundle)
            finally:
                self.assertTrue(obj.closed)
                loader.assert_called_once_with("synthetic.xlsx", read_only=True, data_only=False, keep_links=False)
        return result

    def test_identity_blank_fields_and_reference_not_used(self):
        result = self.read()
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual(row["barcode"], "00123")
        self.assertIsNone(row["factor"])
        self.assertIsNone(row["origin_unit"])
        self.assertIsNone(row["evidence"])
        self.assertEqual(row["status"], "Pendiente")

    def test_excel_dates_are_iso_and_decimals_unchanged(self):
        self.cells[11].value = datetime(2026, 1, 1)
        self.cells[9].value = 1.125
        row = self.read()["rows"][0]
        self.assertEqual(row["valid_from"], "2026-01-01")
        self.assertEqual(row["factor"], 1.125)

    def test_numeric_barcode_is_rejected(self):
        self.cells[4].value = 123
        with self.assertRaisesRegex(ValueError, "barras como texto"):
            self.read()

    def test_formula_and_excel_error_are_rejected(self):
        for kind in ("f", "e"):
            self.cells[9].data_type = kind
            with self.assertRaisesRegex(ValueError, "fórmulas/errores"):
                self.read()

    def test_changed_headers_or_base_are_rejected(self):
        self.header[0].value = "Wrong"
        with self.assertRaisesRegex(ValueError, "Encabezados"):
            self.read()
        self.header[0].value = "Fuente"
        self.guide["B8"].value = "other"
        with self.assertRaisesRegex(ValueError, "otra versión"):
            self.read()


if __name__ == "__main__":
    unittest.main()
