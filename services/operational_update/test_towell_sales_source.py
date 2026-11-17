"""Synthetic read-only parser tests; no commercial fixture is committed."""
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
import zipfile

from openpyxl import Workbook, load_workbook

from .towell_sales_source import MASTER_NAME, TowellSourceError, _read_windows_shared, read_master_bytes, read_towell_sales_source, serializable


class TowellSalesSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        self.private = self.workspace / "datos_privados"
        self.private.mkdir()
        self.path = self.private / MASTER_NAME

    def write(self, rows=None, *, sheet_name="Towell", headers=None):
        book = Workbook()
        sheet = book.active
        sheet.title = sheet_name
        for column, value in (headers or {"K": "Venta 2024", "L": "Venta 2025", "M": "Venta 2026"}).items():
            sheet[column + "5"] = value
        for index, override in enumerate(rows or [{}], 6):
            values = {"A": "001", "B": "SYNTHETIC CHAIN", "D": "FORMAT", "E": "0000000000017",
                "F": "SYNTHETIC PRODUCT", "J": "Octubre", "K": 0, "L": 12.5, "M": 7.75,
                "AD": "SOURCE-ARTICLE", "AE": "SYNTHETIC MODEL", "AF": "PRESENTATION", "AH": "COLOR"}
            values.update(override)
            for column, value in values.items():
                sheet[f"{column}{index}"] = value
        for other in ("Cliente", "Pedidos", "Propuesta RS Dic Def"):
            book.create_sheet(other)["M6"] = 999999
        book.save(self.path)
        book.close()
        return self.path

    def read(self):
        return read_towell_sales_source(self.workspace)

    def test_only_exact_workbook_and_no_fallback(self):
        fallback = self.write().rename(self.private / ("Copia " + MASTER_NAME))
        # Size, recency or a familiar filename must never select a fallback.
        with fallback.open("ab") as stream:
            stream.write(b"untrusted alternate contents" * 10000)
        self.assertGreater(fallback.stat().st_size, 200000)
        with self.assertRaisesRegex(TowellSourceError, "TOWELL_MASTER_NOT_FOUND"):
            self.read()
        with self.assertRaisesRegex(TowellSourceError, "TOWELL_MASTER_PATH_INVALID"):
            read_master_bytes(self.private / ("Copia " + MASTER_NAME))

    @staticmethod
    def quantities(document):
        """Input hash is provenance; it must not turn another sheet into sales."""
        return [{k: value for k, value in row.items() if k != "source_sha256"}
                for row in document["records"]]

    def change_unrelated_sources(self):
        book = load_workbook(self.path, keep_links=False)
        try:
            for column in ("O", "R", "U", "X", "AA", "AB", "AN"):
                book["Towell"][column + "6"] = 987654321
            for name in ("Cliente", "Pedidos", "Propuesta RS Dic Def", "Estadistica"):
                sheet = book[name] if name in book else book.create_sheet(name)
                sheet["K6"], sheet["M6"], sheet["Y6"] = -888888, "=1/0", 123456789
            book.save(self.path)
        finally:
            book.close()
        # Old operational results and inventory are not training input here.
        (self.private / "previous_operational.json").write_text(
            '{"SALES":999999999,"ORDER":123,"DELIVERY":456}', encoding="utf-8")
        (self.private / "inventory.csv").write_text("upc,stock\n0000000000017,999999999\n", encoding="utf-8")

    def test_unrelated_sheets_proposals_old_results_and_inventory_cannot_change_sales(self):
        self.write([{"J": "Septiembre"}, {"J": "Octubre", "M": 8.125}])
        original = self.read()
        self.change_unrelated_sources()
        changed = self.read()
        self.assertNotEqual(original["source"]["sha256"], changed["source"]["sha256"])
        self.assertEqual(original["identities"], changed["identities"])
        self.assertEqual(original["summary"], changed["summary"])
        self.assertEqual(self.quantities(original), self.quantities(changed))
        self.assertEqual(original["exceptions"], changed["exceptions"])

    def test_unchanged_sales_train_identical_forecasts_in_one_isolated_engine_call(self):
        from services.forecast_engine.engine import ForecastPolicy, forecast_dataset

        months = "Enero Febrero Marzo Abril Mayo Junio Julio Agosto Septiembre Octubre Noviembre Diciembre".split()
        self.write([{"J": month, "K": 20 + i % 3, "L": 24 + i % 3,
                     "M": 28 + i % 3 if i < 10 else None} for i, month in enumerate(months)])
        original = self.read()
        self.change_unrelated_sources()
        changed = self.read()
        self.assertEqual(self.quantities(original), self.quantities(changed))

        def engine_input(document):
            return [{"chain_id": "SYNTHETIC", "product_id": "SYNTHETIC-PRODUCT",
                     "product_code": "SYNTHETIC-CODE", "category": "SYNTHETIC-TYPE",
                     "variant": "SYNTHETIC-COLOR", "objective": "Venta", "period": row["period"],
                     "value": float(row["value"]), "available_at": None,
                     "source_id": row["source_reference"], "evidence_mode": "RETROSPECTIVE_TRAINING"}
                    for row in document["records"] if row["eligible"]]

        before, after = engine_input(original), engine_input(changed)
        self.assertEqual(before, after)
        self.assertEqual(len(before), 34)
        # Two isolated scopes in one real engine invocation, never a commercial
        # forecast. Each scope receives the same confirmed Towell-only values.
        records = [{**row, "chain_id": scope} for scope, rows in (("SYNTHETIC-A", before), ("SYNTHETIC-B", after))
                   for row in rows]
        result = forecast_dataset(records, "2026-10", policy=ForecastPolicy(), evidence_mode="RETROSPECTIVE_TRAINING")
        self.assertEqual(len(result["chains"]), 2)
        fields = ("horizon", "target_period", "statistical", "ml", "forecast_towell", "statistical_model", "ml_model")
        quantities = [[tuple(row[field] for field in fields) for row in chain["forecast_towell"]]
                      for chain in result["chains"]]
        self.assertEqual(quantities[0], quantities[1])
        self.assertEqual(len(quantities[0]), 12)
        self.assertTrue(all(chain["certification_status"] == "PROVISIONAL_TEMPORAL_UNKNOWN" for chain in result["chains"]))

    @unittest.skipUnless(os.name == "nt", "Windows shared-handle reader is platform-specific")
    def test_windows_shared_handle_reads_the_same_source_without_modification(self):
        self.write()
        before = self.path.read_bytes()
        self.assertEqual(_read_windows_shared(self.path), before)
        self.assertEqual(self.path.read_bytes(), before)

    def test_requires_towell_sheet_and_the_literal_historical_headers(self):
        self.write(sheet_name="NotTowell")
        with self.assertRaisesRegex(TowellSourceError, "TOWELL_SHEET_MISSING"):
            self.read()
        self.write(headers={"K": "Forecast 2024", "L": "Venta 2025", "M": "Venta 2026"})
        with self.assertRaisesRegex(TowellSourceError, "TOWELL_HISTORY_HEADERS_MISMATCH"):
            self.read()

    def test_only_historical_columns_produce_sales_and_original_source_is_unchanged(self):
        self.write([{"O": 123456, "R": 234567, "U": 345678, "X": 456789, "AA": 567890, "AN": 99}])
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        result = self.read()
        self.assertEqual({r["source_reference"] for r in result["records"]}, {"Towell!K6", "Towell!L6", "Towell!M6"})
        self.assertEqual({r["metric_code"] for r in result["records"]}, {"SALES"})
        self.assertEqual([r["value"] for r in result["records"]], [Decimal(0), Decimal("12.5"), Decimal("7.75")])
        self.assertEqual(before, result["source"]["sha256"])
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertTrue(all(r["eligible"] for r in result["records"]))
        self.assertFalse(any("pack" in k or "factor" in k for r in result["records"] for k in r))

    def test_preserves_decimal_xml_literal_text_upc_zero_and_empty(self):
        self.write([{"K": 0, "L": None, "M": 1}])
        with zipfile.ZipFile(self.path) as archive:
            files = [(item, archive.read(item.filename)) for item in archive.infolist()]
        with zipfile.ZipFile(self.path, "w") as archive:
            for item, content in files:
                if item.filename == "xl/worksheets/sheet1.xml":
                    text = content.decode()
                    text, count = re.subn(r'(<c r="M6"[^>]*><v>)[^<]*(</v>)', r'\g<1>16.1234567890123456\2', text)
                    self.assertEqual(count, 1)
                    content = text.encode()
                archive.writestr(item, content)
        result = self.read()
        self.assertEqual(result["identities"][0]["account"], "001")
        self.assertEqual(result["identities"][0]["upc"], "0000000000017")
        self.assertEqual(result["records"][0]["value"], Decimal(0))
        self.assertEqual(result["records"][1]["state"], "EMPTY")
        self.assertIsNone(result["records"][1]["value"])
        self.assertFalse(result["records"][1]["eligible"])
        self.assertEqual(result["records"][2]["value"], Decimal("16.1234567890123456"))
        self.assertEqual(result["records"][2]["value_text"], "16.1234567890123456")
        roundtrip = json.loads(json.dumps(serializable(result), allow_nan=False))
        self.assertEqual(roundtrip["records"][2]["value"], "16.1234567890123456")
        self.assertIsNone(roundtrip["records"][1]["value"])

    def test_month_closure_has_no_invented_confirmation_timestamp(self):
        self.write([{"J": "Noviembre", "M": 99}])
        result = self.read()
        self.assertEqual(result["closure"]["last_closed_period"], "2026-10")
        self.assertEqual(result["closure"]["explicitly_confirmed_periods"], ["2026-09", "2026-10"])
        self.assertIsNone(result["closure"]["confirmed_at"])
        self.assertFalse(result["closure"]["point_in_time_certified"])
        record = next(r for r in result["records"] if r["column"] == "M")
        self.assertEqual(record["value"], Decimal(99))
        self.assertEqual(record["state"], "NOT_CLOSED")
        self.assertFalse(record["eligible"])
        self.assertEqual(result["summary"]["last_period_with_quantity"], "2026-11")
        self.assertNotEqual(result["summary"]["last_period_with_quantity"], result["closure"]["last_closed_period"])

    def test_captured_october_is_partial_when_only_september_is_explicitly_closed(self):
        self.write()
        result = read_towell_sales_source(self.workspace, confirmed_closed_period="2026-09")
        october = next(r for r in result["records"] if r["period"] == "2026-10")
        self.assertEqual(october["value"], Decimal("7.75"))
        self.assertEqual(october["state"], "NOT_CLOSED")
        self.assertFalse(october["eligible"])
        self.assertEqual(result["summary"]["last_period_with_quantity"], "2026-10")
        self.assertEqual(result["closure"]["last_closed_period"], "2026-09")
        self.assertIsNone(result["closure"]["confirmed_at"])
        self.assertEqual(result["closure"]["confirmed_by"], "EXPLICIT_CALLER_CONFIGURATION")

    def test_zero_gap_before_first_positive_and_unknown_activity_are_distinct(self):
        self.write([{"J": "Enero", "K": None, "L": None, "M": None},
                    {"J": "Febrero", "K": None, "L": None, "M": 0},
                    {"J": "Marzo", "K": None, "L": None, "M": 5},
                    {"J": "Abril", "K": None, "L": None, "M": None},
                    {"J": "Mayo", "K": None, "L": None, "M": 7},
                    {"J": "Junio", "K": None, "L": None, "M": None},
                    {"J": "Noviembre", "K": None, "L": None, "M": None},
                    {"E": "0000000000024", "K": None, "L": None, "M": None}])
        result = self.read()
        rows = {r["period"]: r for r in result["records"] if r["identity_key"] == result["identities"][0]["identity_key"]}
        self.assertEqual(rows["2026-01"]["activity_evidence"], "BEFORE_FIRST_POSITIVE_OBSERVED")
        self.assertEqual(rows["2026-02"]["quantity_state"], "EXPLICIT_ZERO")
        self.assertEqual(rows["2026-02"]["activity_evidence"], "OBSERVED_QUANTITY_ONLY")
        self.assertTrue(rows["2026-02"]["eligible"])
        self.assertEqual(rows["2026-04"]["quantity_state"], "EMPTY")
        self.assertEqual(rows["2026-04"]["activity_evidence"], "CAPTURE_GAP")
        self.assertEqual(rows["2026-06"]["activity_evidence"], "UNKNOWN_ACTIVITY")
        self.assertEqual(rows["2026-11"]["activity_evidence"], "FUTURE_NO_REAL")
        unknown = next(r for r in result["records"] if r["row"] == 13 and r["column"] == "M")
        self.assertEqual(unknown["activity_evidence"], "UNKNOWN_ACTIVITY")
        self.assertFalse(unknown["eligible"])
        self.assertEqual(result["source"]["product_opening_dates"], "NOT_ACCREDITED")
        self.assertFalse(any("PRE-LAUNCH" in r["activity_evidence"] for r in result["records"]))

    def test_invalid_closure_month_is_rejected_without_guessing(self):
        self.write()
        with self.assertRaisesRegex(TowellSourceError, "TOWELL_CLOSURE_PERIOD_INVALID"):
            read_towell_sales_source(self.workspace, confirmed_closed_period="2026-13")

    def test_an_individual_invalid_amount_does_not_block_the_other_product(self):
        self.write([{"M": -1}, {"E": "0000000000024", "M": 4.25}])
        result = self.read()
        self.assertEqual(len(result["identities"]), 2)
        self.assertEqual(len(result["records"]), 6)
        self.assertEqual(result["records"][2]["state"], "INVALID_NUMERIC_LITERAL_REVIEW")
        self.assertEqual(result["records"][5]["value"], Decimal("4.25"))
        self.assertTrue(result["records"][5]["eligible"])

    def test_unknown_month_missing_identity_and_text_quantities_are_exceptions_not_substitutes(self):
        self.write([{"J": "Unknown"}, {"E": None}, {"E": "0000000000024", "M": "7.75"}])
        result = self.read()
        self.assertEqual(len(result["records"]), 9)
        self.assertIsNone(result["records"][0]["period"])
        self.assertEqual(result["records"][3]["state"], "ACCOUNT_FORMAT_UPC_MISSING")
        self.assertEqual(result["records"][8]["state"], "NON_NUMERIC_HISTORY_REVIEW")
        self.assertIsNone(result["records"][8]["value"])

    def test_duplicate_scope_month_blocks_both_rows_without_selecting_one_or_double_counting(self):
        self.write([{"M": 3}, {"M": 9}])
        result = self.read()
        self.assertEqual(result["summary"]["eligible_cells"], 0)
        self.assertEqual(result["summary"]["states"], {"DUPLICATE_SOURCE_KEY": 6})
        self.assertEqual([r["value"] for r in result["records"] if r["column"] == "M"], [Decimal(3), Decimal(9)])

    def test_same_upc_in_two_formats_is_not_merged(self):
        self.write([{}, {"D": "OTHER FORMAT"}])
        result = self.read()
        self.assertEqual(result["summary"]["identities"], 2)
        self.assertEqual(result["summary"]["eligible_cells"], 6)

    def test_unaccredited_metadata_change_blocks_only_that_identity(self):
        self.write([{"J": "Enero"}, {"J": "Febrero", "AH": "DIFFERENT COLOR"}, {"E": "0000000000024"}])
        result = self.read()
        self.assertEqual(result["summary"]["states"]["IDENTITY_ATTRIBUTES_CONFLICT"], 6)
        self.assertEqual(result["summary"]["eligible_cells"], 3)

    def test_formula_in_a_historical_cell_is_not_silently_treated_as_an_empty_observation(self):
        self.write([{"M": "=O6+R6"}])
        result = self.read()
        record = result["records"][2]
        self.assertEqual(record["state"], "FORMULA_HISTORY_REVIEW")
        self.assertTrue(record["has_formula"])
        self.assertFalse(record["eligible"])
        self.assertIsNone(record["value"])


if __name__ == "__main__":
    unittest.main()
