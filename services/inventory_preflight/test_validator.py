import copy
import unittest
import tempfile
import json
import hashlib
from pathlib import Path
from unittest.mock import patch
from .validator import COLUMNS, csv_rows, preflight


def fixture():
    approval = {"status": "APPROVED", "evidence": "SYNTHETIC-TEST-ONLY", "approved_by": "Test", "approved_at": "2026-11-05T10:00:00Z"}
    manifest = {**approval, "account": "A", "chain_id": "C", "cutoff": "2026-10-31", "version": "1", "unit": "PIEZAS", "source_sha256": "a"*64, "stores": ["S1", "S2"], "identities": ["A|001", "A|002"]}
    mappings = [{**approval, "identity": "A|00"+str(i), "account": "A", "chain_id": "C", "product_id": str(i), "barcode": "00"+str(i), "format": "F", "model": "M", "presentation": "MB", "commercial_code": "CODE", "color": str(i)} for i in (1, 2)]
    catalog = [{"id": m["product_id"], "chain_id": "C", "barcodes": [m["barcode"]], "status": "ACTIVE"} for m in mappings]
    row = {"account": "A", "chain_id": "C", "barcode": "001", "product_id": "1", "store": "S1", "cutoff": "2026-10-31", "version": "1", "pieces": "0.125"}
    return [row], manifest, mappings, catalog


class InventoryTests(unittest.TestCase):
    def test_decimal_partial_and_no_mutation(self):
        args = fixture(); original = copy.deepcopy(args)
        r = preflight(*args)
        self.assertEqual(r["status"], "READY_FOR_OFFLINE_REVIEW")
        self.assertFalse(r["production_load"])
        self.assertEqual(r["models"][0]["pieces"], "0.125")
        self.assertEqual(r["models"][0]["pairs_expected"], 4)
        self.assertEqual(r["models"][0]["status"], "PARTIAL")
        self.assertEqual(args, original)
        args[0][0]["pieces"] = "0.0000001"
        self.assertEqual(preflight(*args)["models"][0]["pieces"], "1E-7")

    def test_complete_sum_zero_and_other_model_no_data(self):
        rows, m, mappings, catalog = fixture()
        rows = [{**rows[0], "barcode": "00"+str(i), "product_id": str(i), "store": s, "pieces": "0" if i == 1 else "0.1"} for i in (1, 2) for s in m["stores"]]
        r = preflight(rows, m, mappings, catalog)
        self.assertEqual(r["models"][0]["pieces"], "0.2")
        self.assertEqual(r["models"][0]["status"], "COMPLETE")
        mappings[1]["presentation"] = "FACIAL"
        r = preflight(rows[:2], m, mappings, catalog)
        self.assertEqual(len(r["models"]), 2)
        self.assertIsNone(r["models"][1]["pieces"])

    def test_invalid_rows_block_whole_batch(self):
        for change in ({"pieces": ""}, {"pieces": "-1"}, {"pieces": "NaN"}, {"pieces": "1e3"}, {"pieces": 1}, {"cutoff": "2026-09-30"}, {"store": "UNKNOWN"}, {"version": "2"}, {"account": "B"}, {"chain_id": "D"}, {"barcode": "1"}, {"product_id": "2"}):
            with self.subTest(change=change):
                rows, *rest = fixture()
                r = preflight(rows + [{**rows[0], **change}], *rest)
                self.assertEqual(r["status"], "BLOCKED")
                self.assertEqual(r["rows"], [])

    def test_duplicates_and_missing_approvals(self):
        for kind in ("duplicate_row", "ambiguous_mapping", "unapproved", "no_evidence", "wrong_catalog", "wrong_upc", "invalid_date", "duplicate_stores", "no_rows", "existing"):
            with self.subTest(kind=kind):
                rows, m, mappings, catalog = fixture(); existing = []
                if kind == "duplicate_row": rows += copy.deepcopy(rows)
                if kind == "ambiguous_mapping": mappings += copy.deepcopy(mappings)
                if kind == "unapproved": mappings[1]["status"] = "PENDING_CLASSIFICATION"
                if kind == "no_evidence": m["evidence"] = ""
                if kind == "wrong_catalog": catalog[0]["chain_id"] = "D"
                if kind == "wrong_upc": catalog[0]["barcodes"] = ["1"]
                if kind == "invalid_date": m["cutoff"] = "2026-02-30"
                if kind == "duplicate_stores": m["stores"] *= 2
                if kind == "no_rows": rows = []
                if kind == "existing": existing = [copy.deepcopy(m)]
                self.assertEqual(preflight(rows, m, mappings, catalog, existing)["status"], "BLOCKED")

    def test_csv_strict_and_leading_zero(self):
        rows, *_ = fixture()
        text = ",".join(COLUMNS) + "\n" + ",".join(rows[0][k] for k in COLUMNS)
        self.assertEqual(csv_rows(text), rows)
        for invalid in (text + ",extra", text.replace("account,", "account,account,"), "account\nA"):
            with self.assertRaises(ValueError): csv_rows(invalid)

    def test_cli_hash_and_no_overwrite(self):
        from .__main__ import main
        rows, manifest, mappings, catalog = fixture()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = (",".join(COLUMNS)+"\n"+",".join(rows[0][k] for k in COLUMNS)).encode()
            (root/"capture.csv").write_bytes(raw)
            manifest["source_sha256"] = hashlib.sha256(raw).hexdigest()
            for name, value in (("manifest", manifest), ("mappings", mappings), ("catalog", catalog), ("existing", [])):
                (root/(name+".json")).write_text(json.dumps(value), encoding="utf-8")
            args = ["preflight", "--csv", str(root/"capture.csv"), "--output", str(root/"preview.json")]
            for name in ("manifest", "mappings", "catalog", "existing"):
                args += ["--"+name, str(root/(name+".json"))]
            with patch("sys.argv", args):
                self.assertEqual(main(), 0)
                before = (root/"preview.json").read_bytes()
                with self.assertRaises(FileExistsError): main()
                self.assertEqual((root/"preview.json").read_bytes(), before)
                (root/"capture.csv").write_bytes(raw+b"\n")
                with self.assertRaisesRegex(SystemExit, "HASH_FUENTE"): main()


if __name__ == "__main__":
    unittest.main()
