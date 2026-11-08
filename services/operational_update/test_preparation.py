"""Synthetic acceptance tests; no commercial files, servers or forecasts."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from services.operational_update.preparation import (
    REPO, collect_snapshot, fingerprint, prepare, read_master, reconcile_identities,
    verify_package, write_package,
)


def product(identity="A|001", upc="001", color="Blue"):
    return {"id": identity, "account": identity.split("|")[0], "barcode": upc,
            "chain": "Alpha Legal", "format": "Alpha", "model": "Multi Word Design", "size": "MB",
            "color": color, "commercial_code": "GROUP", "description": "Synthetic variant", "issues": []}


def snapshot():
    return {"chains": [{"id": "chain-a", "name": "Alpha"}, {"id": "chain-b", "name": "Beta"}],
            "products": [{"id": "p1", "chain_id": "chain-a", "product_code": "001", "description": "Synthetic catalog", "status": "ACTIVE"}],
            "profiles": [{"id": "version-a", "profile_id": "profile-a", "version": 1, "chain_id": "chain-a",
                          "mapping_json": {"scope": {"code": "Alpha", "parent_code": "Alpha"},
                                           "products": [{"id": "p1", "chain_id": "chain-a", "identifier_mappings": [["ITEM", "001"]]}]}},
                         {"id": "version-b", "profile_id": "profile-b", "version": 1, "chain_id": "chain-b",
                          "mapping_json": {"scope": {"code": "Beta", "parent_code": "Beta"}, "products": []}}],
            "observations": [{"observation_id": "old-order", "chain_id": "chain-a", "product_id": "p1", "period": "2026-07-01",
                              "metric_code": "ORDER", "value": "10.000", "version_no": 1},
                             {"observation_id": "sale", "chain_id": "chain-a", "product_id": "p1", "period": "2026-07-01",
                              "metric_code": "SALES", "value": "100", "version_no": 1},
                             {"observation_id": "delivery", "chain_id": "chain-a", "product_id": "p1", "period": "2026-07-01",
                              "metric_code": "DELIVERY", "value": "80", "version_no": 1}],
            "protected": {"calculations": [{"id": "C1", "cutoff": "2026-07", "quantity": "9.50", "wape": "18.5"}], "champions": []}}


def record(value="20", period="2026-07", metric="ORDER", identity="A|001"):
    return {"identity": identity, "period": period, "metric": metric, "value": value,
            "value_state": "MISSING" if value is None else "VALUE", "original": value, "cell": "Synthetic!K6", "formula": False}


def master(rows=None, products=None):
    return {"source_file": "synthetic.xlsx", "source_hash": "0" * 64, "products": products or [product()],
            "records": rows if rows is not None else [record()], "inventory": [], "malformed_rows": []}


class IdentityTests(unittest.TestCase):
    def test_exact_upc_scope_does_not_require_complementary_attribute_approval(self):
        p = product()
        p.update(model="Other words not in catalog description", size="Unclassified", color="Commercial label")
        out = reconcile_identities([p], snapshot())[0]
        self.assertEqual(out["state"], "COINCIDENCIA_VERIFICADA")
        self.assertEqual(out["barcode"], "001")

    def test_upc_in_other_chain_and_known_chain_contradiction_are_not_linked(self):
        p = product()
        p["format"] = "Beta"
        self.assertEqual(reconcile_identities([p], snapshot())[0]["state"], "REVISION_REQUERIDA")
        p.update(format="Alpha", chain="Beta")
        self.assertEqual(reconcile_identities([p], snapshot())[0]["reason"], "known_chain_contradiction")

    def test_multiple_active_candidates_and_shared_targets_are_ambiguous(self):
        s = snapshot()
        p2 = {**s["products"][0], "id": "p2"}
        s["products"].append(p2)
        s["profiles"][0]["mapping_json"]["products"].append({"id": "p2", "chain_id": "chain-a", "identifier_mappings": [["OTHER", "001"]]})
        self.assertEqual(reconcile_identities([product()], s)[0]["state"], "AMBIGUO")
        rows = reconcile_identities([product(), product("B|001")], snapshot())
        self.assertTrue(all(r["state"] == "AMBIGUO" and r["product_id"] is None for r in rows))

    def test_one_exception_does_not_stop_other_identities_or_link_by_name(self):
        p = product("A|999", "999")
        rows = reconcile_identities([product(), p], snapshot())
        self.assertEqual([r["state"] for r in rows], ["COINCIDENCIA_VERIFICADA", "SIN_CORRESPONDENCIA"])
        p = product("A|002", "002")
        p["issues"] = ["Duplicado de origen"]
        s = snapshot()
        s["products"].append({**s["products"][0], "id": "p2", "product_code": "002"})
        s["profiles"][0]["mapping_json"]["products"].append({"id": "p2", "chain_id": "chain-a", "identifier_mappings": [["OTHER", "002"]]})
        self.assertEqual(reconcile_identities([product(), p], s)[1]["reason"], "source_identity_or_value_anomaly")


class CandidateTests(unittest.TestCase):
    def test_import_metadata_exception_isolated_without_changing_catalog(self):
        s = snapshot()
        s["products"][0]["description"] = " Original catalog name "
        c = prepare(master(), s, "2026-10")
        self.assertEqual(c["comparisons"][0]["reason"], "import_parser_would_normalize_catalog_description")
        self.assertEqual(c["changes"], [])
        self.assertEqual(s["products"][0]["description"], " Original catalog name ")

    def test_equal_new_changed_missing_and_forecast_measures_are_separate(self):
        rows = [record("10.0"), record("0", "2026-08"), record(None, "2026-09"),
                record("22", "2026-10", "FCST_TOWELL_HISTORY"), record("23", "2026-10", "FCST_CLIENTE_HISTORY")]
        candidate = prepare(master(rows), snapshot(), "2026-10")
        self.assertEqual([r["comparison"] for r in candidate["comparisons"]], ["COINCIDE", "NUEVO", "SIN_DATO", "NUEVO", "NUEVO"])
        self.assertEqual(candidate["comparisons"][0]["activation"], "CONSERVAR_EXISTENTE")
        self.assertEqual(candidate["changes"][0]["value"], "0")
        self.assertEqual(len(candidate["changes"]), 1)
        self.assertEqual(candidate["comparisons"][3]["activation"], "PENDIENTE_CONTRATO")
        changed = prepare(master(), snapshot(), "2026-10")["changes"][0]
        self.assertEqual(changed["comparison"], "DIFERENTE")
        self.assertEqual(changed["next_version"], 2)

    def test_preserves_sources_protected_results_and_does_not_call_any_model(self):
        source, s = master(), snapshot()
        before = fingerprint([source, s])
        with patch("services.statistical_engine.engine.run_series", side_effect=AssertionError("forecast called")):
            c = prepare(source, s, "2026-10")
        self.assertEqual(before, fingerprint([source, s]))
        self.assertEqual(c["manifest"]["protected_hash"], fingerprint(s["protected"]))
        july = {r["metric"]: r["value"] for r in c["color"] if r["period"] == "2026-07"}
        self.assertEqual(july["SALES"], "100")
        self.assertEqual(july["DELIVERY"], "80")
        self.assertEqual(july["ORDER"], "20")

    def test_precision_future_period_and_formula_are_local_exceptions(self):
        rows = [record("1.000000000002"), record("0", "2026-11"), record("4", "2026-08")]
        rows[2]["formula"] = True
        c = prepare(master(rows), snapshot(), "2026-10")
        self.assertFalse(c["changes"])
        self.assertEqual([r["reason"] for r in c["comparisons"]], ["precision_exceeds_numeric_18_3", "period_after_confirmed_closure", "unverified_formula_cache"])
        self.assertEqual(c["comparisons"][0]["value"], "1.000000000002")

    def test_partial_model_sums_exact_variants_and_keeps_scopes_sizes_codes_distinct(self):
        s = snapshot()
        products = [product(), product("A|002", "002", "Red"), product("A|003", "003", "Green")]
        products[2]["size"] = "FACIAL"
        for i in range(2, 4):
            s["products"].append({**s["products"][0], "id": f"p{i}", "product_code": f"00{i}"})
            s["profiles"][0]["mapping_json"]["products"].append({"id": f"p{i}", "chain_id": "chain-a", "identifier_mappings": [["I", f"00{i}"]]})
        c = prepare(master([record("0.1", "2026-08"), record(None, "2026-08", identity="A|002"), record("9", "2026-08", identity="A|003")], products), s, "2026-10")
        models = [r for r in c["model"] if r["metric"] == "ORDER"]
        self.assertEqual(len(models), 2)
        self.assertTrue(any(r["value"] == "0.1" and r["covered"] == 1 and r["expected"] == 2 and r["state"] == "TOTAL_PARCIAL" for r in models))

    def test_reproducible_and_rejects_duplicate_or_cross_scope_snapshots(self):
        self.assertEqual(prepare(master(), snapshot(), "2026-10"), prepare(master(), snapshot(), "2026-10"))
        s = snapshot()
        s["observations"].append(dict(s["observations"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate_snapshot_key"):
            prepare(master(), s, "2026-10")
        s = snapshot()
        s["observations"][0]["chain_id"] = "chain-b"
        with self.assertRaisesRegex(ValueError, "snapshot_product_scope_mismatch"):
            prepare(master(), s, "2026-10")
        with self.assertRaisesRegex(ValueError, "duplicate_source_key"):
            prepare(master([record(), record()]), snapshot(), "2026-10")


class PackageTests(unittest.TestCase):
    def test_catalog_identifier_is_used_without_rewriting_upc_or_product(self):
        s = snapshot()
        s["products"][0].update(product_code="ITEM123", variant_code="SKU1")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "private"
            write_package(prepare(master(), s, "2026-10"), s, output)
            content = (output / "Pedido_chain-a.csv").read_text(encoding="utf-8-sig")
            self.assertIn("ITEM123,SKU1,Synthetic catalog,001", content)

    def test_spreadsheet_tamper_cannot_be_hidden_by_rewriting_index(self):
        from services.operational_update.preparation import file_hash
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "private"
            c = prepare(master(), snapshot(), "2026-10")
            manifest = write_package(c, snapshot(), output)
            target = output / "Vista_por_modelo.csv"
            target.write_text("tampered")
            index = json.loads((output / "Indice_integridad.json").read_text())
            index[target.name] = file_hash(target)
            (output / "Indice_integridad.json").write_text(json.dumps(index))
            with self.assertRaisesRegex(ValueError, "package_index_not_bound"):
                verify_package(output, manifest["manifest_hash"])

    def test_existing_import_contract_manifest_and_tamper_detection(self):
        s = snapshot()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "private"
            c = prepare(master(), s, "2026-10")
            manifest = write_package(c, s, output)
            result = verify_package(output, manifest["manifest_hash"])
            self.assertEqual(result["candidate_id"], manifest["candidate_id"])
            self.assertEqual(json.loads((output / "Validacion.json").read_text())["accepted_rows"], 1)
            with self.assertRaisesRegex(ValueError, "refusing_to_overwrite"):
                write_package(c, s, output)
            (output / "Cambios_ORDER.json").write_text("[]")
            with self.assertRaisesRegex(ValueError, "package_file_hash_mismatch"):
                verify_package(output, manifest["manifest_hash"])
        with self.assertRaisesRegex(ValueError, "outside_repository"):
            write_package(c, s, REPO / "private-output-forbidden")

    def test_foreign_manifest_hash_and_csv_or_html_injection(self):
        p = product()
        p.update(model="<script>alert(1)</script>", description="=WEBSERVICE(1)")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "private"
            c = prepare(master(products=[p]), snapshot(), "2026-10")
            write_package(c, snapshot(), output)
            self.assertIn("&lt;script&gt;", (output / "Vista_previa.html").read_text())
            self.assertNotIn("<script>", (output / "Vista_previa.html").read_text())
            self.assertIn("'=WEBSERVICE", (output / "Identidades.csv").read_text(encoding="utf-8-sig"))
            with self.assertRaisesRegex(ValueError, "manifest_hash_mismatch"):
                verify_package(output, "different-expected-hash")

    def test_get_only_snapshot_and_concurrent_change_detection(self):
        class Client:
            def __init__(self): self.calls = 0
            def rows(self, table, params):
                self.calls += 1
                return [{"id": "stable"}] if table == "chains" else []
        client = Client()
        self.assertEqual(collect_snapshot(client)["chains"], [{"id": "stable"}])
        self.assertEqual(client.calls, 22)
        class Changing(Client):
            def rows(self, table, params):
                self.calls += 1
                return [{"id": str(self.calls)}] if table == "chains" else []
        with self.assertRaisesRegex(ValueError, "snapshot_changed"):
            collect_snapshot(Changing())

    def test_adapter_reads_every_sheet_preserves_zero_blank_decimal_and_upc(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "synthetic.xlsx"
            book = Workbook()
            book.remove(book.active)
            for name in ("Towell", "Cliente", "Pedidos"):
                sheet = book.create_sheet(name)
                sheet.cell(5, 44, "Header")
                row = [None] * 44
                for i, value in {0: "A", 1: "Alpha Legal", 3: "Alpha", 4: "001", 5: "Synthetic", 9: "Enero", 10: 0, 11: None, 12: 1.125, 30: "Multi Word Design", 31: "MB", 33: "Blue"}.items():
                    row[i] = value
                sheet.append(row)
            sheet = book.create_sheet("Propuesta RS Dic Def")
            sheet.cell(5, 42, "Header")
            row = [None] * 42
            for i, value in {0: "A", 1: "Alpha Legal", 3: "Alpha", 4: "001", 5: "Synthetic", 11: "Diciembre", 18: 7, 21: "Multi Word Design", 22: "MB", 24: "Blue", 41: "GROUP"}.items():
                row[i] = value
            sheet.append(row)
            book.create_sheet("Hoja1").append(["GROUP", 7])
            hidden = book.create_sheet("Unrelated hidden summary")
            hidden.sheet_state = "hidden"
            hidden.append(["=1+1"])
            book.save(path)
            before = path.read_bytes()
            source = read_master(path)
            self.assertEqual(source["products"][0]["barcode"], "001")
            self.assertEqual(len(source["inventory"]), 6)
            self.assertEqual([r["value"] for r in source["records"][:3]], ["0", None, "1.125"])
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
