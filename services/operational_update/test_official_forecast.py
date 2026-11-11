"""Integrity and engine-entry regression checks for private source forecasts."""
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from services.forecast_engine.engine import ForecastPolicy, forecast_dataset
from services.assistant_api.data_provider import SupabaseDataProvider
from .official_forecast import (
    _scope_emissions, consolidate_models, eligibility, engine_records, source_inputs, variant_forecasts,
)
from .preparation import file_hash
from .unification import write_csv, write_json


def source_row(period="2026-10", value="10"):
    return {
        "id": "source-" + period, "chain_id": "chain", "product_id": "p1",
        "purchase_type": "RS", "account": "account", "format": "Format", "model": "Source Model",
        "presentation": "Facial", "commercial_code": "CODE", "upc": "00001", "color": "Blue",
        "product_type": "Toalla", "metric_code": "SALES", "period": period, "value": value,
        "available_at": "", "availability_source": "UNKNOWN",
    }


class OfficialForecastTests(unittest.TestCase):
    def test_reused_scopes_preserve_actual_original_emission_and_new_scopes_get_their_own(self):
        with TemporaryDirectory() as temporary:
            original, revision = Path(temporary) / "original", Path(temporary) / "revision"
            original.mkdir()
            revision.mkdir()
            write_json(original / "Context.json", {"emitted_at": "2026-11-11T10:00:00+00:00", "reused_from": None})
            for chain in ("one", "two"):
                write_json(original / ("Outcome_" + chain + ".json"), {"chain_id": chain})
            hashes = {path.name: file_hash(path) for path in original.iterdir()}
            write_json(revision / "Context.json", {"emitted_at": "2026-11-11T11:00:00+00:00",
                                                   "reused_from": str(original), "reused_scope_sha256": hashes})
            write_json(revision / "Outcome_one.json", {"chain_id": "one", "reused": True})
            write_json(revision / "Outcome_two.json", {"chain_id": "two", "reused": False})
            self.assertEqual(_scope_emissions(revision), {
                "one": "2026-11-11T10:00:00+00:00", "two": "2026-11-11T11:00:00+00:00"})
            with (original / "Outcome_one.json").open("a", encoding="utf-8") as stream:
                stream.write("\n")
            with self.assertRaisesRegex(ValueError, "official_forecast_reuse_artifact_changed"):
                _scope_emissions(revision)

    def test_private_and_normal_provider_use_same_latest_documented_category(self):
        chain = "00000000-0000-0000-0000-000000000001"
        product = "00000000-0000-0000-0000-000000000002"
        category = "00000000-0000-0000-0000-000000000003"
        facts = [
            {**source_row("2025-01", "1.2345678901234567"), "chain_id": chain, "product_id": product, "product_type": "Trapo"},
            {**source_row("2026-10", "22.0"), "chain_id": chain, "product_id": product, "product_type": "Toalla"},
        ]

        class Client:
            def rows(self, table, params):
                if table == "categories":
                    return [{"id": category, "name": "Catalog category"}]
                if table == "products":
                    return [{"id": product, "chain_id": chain, "category_id": category,
                             "product_code": "existing-code", "description": "existing-description", "variant_code": None}]
                return []

            def catalog_rows(self, *, chain_id, as_of=None):
                return [{"chain_id": chain_id, "product_id": product, "purchase_type": "RS",
                         "product_type": "Toalla", "color": "Blue"}]

            def observation_rows(self, params, **kwargs):
                return [{**row, "period": row["period"] + "-01", "value_text": row["value"],
                         "available_at": None, "observation_id": row["id"], "version_no": 1}
                        for row in facts]

        private, _ = engine_records(facts, "2026-10")
        normal = SupabaseDataProvider(Client()).records(chain_id=chain, issue_period="2026-10")
        fields = ("chain_id", "product_id", "period", "objective", "category", "value",
                  "source_id", "available_at", "availability_source", "evidence_mode")
        self.assertEqual([tuple(row[field] for field in fields) for row in private],
                         [tuple(row[field] for field in fields) for row in normal])
        self.assertTrue(all(row["category_id"] == category for row in normal))
        self.assertEqual({row["category"] for row in private}, {"Toalla"})

    def test_real_engine_entry_keeps_unknown_time_and_own_product_horizons(self):
        facts = [source_row(f"{2025 + i // 12}-{1 + i % 12:02d}", str(10 + i % 3)) for i in range(22)]
        records, metadata = engine_records(facts, "2026-10")
        self.assertTrue(all(row["objective"] == "Venta" and row["available_at"] is None for row in records))
        states, eligible = eligibility(records, "2026-10", ForecastPolicy())
        self.assertEqual(eligible, {"p1"})
        result = forecast_dataset(records, "2026-10", policy=ForecastPolicy(),
                                  evidence_mode="RETROSPECTIVE_TRAINING")["chains"][0]
        variants, coverage = variant_forecasts(result, states, metadata, "2026-10", {"generation": 1})
        statistical = [r for r in variants if r["family"] == "statistical"]
        self.assertEqual(len(statistical), 12)
        self.assertEqual((statistical[0]["period"], statistical[-1]["period"]), ("2026-11", "2027-10"))
        self.assertTrue(all(row["temporal_certification"] is False for row in variants))
        self.assertEqual(sum(row["family"] == "statistical" and row["horizons"] == 12 for row in coverage), 1)
        candidate = next(c for c in result["model_audit"]["product_candidates"] if c["family"] == "statistical")
        self.assertEqual([Decimal(row["pieces"]) for row in statistical], [Decimal(str(h["value"])) for h in candidate["horizons"]])

    def test_no_future_period_or_invented_timestamp_enters_engine(self):
        for change in ({"period": "2026-11"}, {"available_at": "2026-11-01"}, {"availability_source": "SYSTEM_INGESTION"}):
            row = {**source_row(), **change}
            with self.assertRaisesRegex(ValueError, "official_forecast_evidence_mismatch"):
                engine_records([row], "2026-10")
        rows = [source_row(value="0")]
        records, _ = engine_records(rows, "2026-10")
        states, eligible = eligibility(records, "2026-10", ForecastPolicy())
        self.assertFalse(eligible)
        self.assertEqual(states[0]["status"], "PRE-LAUNCH")

    def test_models_are_decimal_sums_with_complete_missing_family_columns(self):
        _, metadata = engine_records([source_row()], "2026-10")
        metadata["chain", "p2"] = {**metadata["chain", "p1"], "product_id": "p2", "color": "Red"}
        variants = [{**details, "family": "statistical", "horizon": 1, "pieces": value}
                    for details, value in zip(metadata.values(), ("0.1", "0.2"))]
        models = consolidate_models(variants, metadata, "2026-10", {"generation": 1})
        self.assertEqual(len(models), 36)
        first = next(row for row in models if row["family"] == "statistical" and row["horizon"] == 1)
        self.assertEqual((first["pieces"], first["covered_variants"], first["expected_variants"], first["status"]), ("0.3", 2, 2, "COMPLETE"))
        self.assertTrue(all(row["pieces"] is None for row in models if row["family"] == "ml"))
        with self.assertRaisesRegex(ValueError, "official_model_variant_double_count"):
            consolidate_models(variants + variants[:1], metadata, "2026-10", {})

    def test_groups_keep_chain_account_presentation_and_unknown_commercial_code(self):
        _, metadata = engine_records([source_row()], "2026-10")
        original = next(iter(metadata.values()))
        for index, changes in enumerate(({"account": "other"}, {"presentation": "Baño"}, {"commercial_code": ""}), 2):
            metadata["chain", "p" + str(index)] = {**original, "product_id": "p" + str(index), **changes}
        models = consolidate_models([], metadata, "2026-10", {})
        self.assertEqual(len({row["group_id"] for row in models}), 4)
        self.assertTrue(any(row["commercial_code_accredited"] is False for row in models))

    def test_source_sha_and_csv_json_parity_are_verified_before_engine_entry(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            row = source_row()
            packet = {"source_hash": "1" * 64, "observations": [row]}
            write_json(directory / "Import.json", packet)
            write_csv(directory / "Facts.csv", [row], list(row))
            manifest = {"schema": "official-source-1", "source_hash": "1" * 64, "cutoff": "2026-10",
                        "outputs_sha256": {name: file_hash(directory / name) for name in ("Facts.csv", "Import.json")}}
            write_json(directory / "Manifest.json", manifest)
            pinned = file_hash(directory / "Manifest.json")
            self.assertEqual(source_inputs(directory, pinned)[1], [row])
            with self.assertRaisesRegex(ValueError, "official_manifest_hash_mismatch"):
                source_inputs(directory, "0" * 64)
            with (directory / "Facts.csv").open("a", encoding="utf-8") as stream:
                stream.write("\n")
            with self.assertRaisesRegex(ValueError, "official_source_artifact_changed"):
                source_inputs(directory, pinned)


if __name__ == "__main__":
    unittest.main()
