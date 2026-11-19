"""Synthetic December scenario readers: observed actuals and G5 stay intact."""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import httpx

from services.assistant_api import test_local_champion as base
from services.assistant_api.data_provider import SupabaseDataProvider
from services.assistant_api.local_champion import LocalChampionService
from services.assistant_api.local_postgres import LocalPostgresGateway
from services.assistant_api.local_sales_policy import LocalSalesPolicy
from services.assistant_api.operational_preview import OperationalMultiChainForecastRunner
from services.assistant_api.persistence import LocalPersistenceProvider, content_hash
from services.assistant_api.supabase_access import (
    COMMERCIAL_CLOSE_BASIS, PreviewError, SupabaseReadClient, UNIFIED_MASTER_SHA256,
    matches_source, observed_cutoff, require_sales_context,
)
from services.assistant_api.unified_plan_reader import annual_preview, pinned_run, report_records


def context():
    return {**base.context(), "source_hash": "b" * 64, "authorized_source_hash": "b" * 64,
        "generation": 6, "last_period": "2026-12", "training_cutoff": "2026-12",
        "observed_cutoff": "2026-09", "planning_basis": COMMERCIAL_CLOSE_BASIS, "scenario_hash": "f" * 64}


def run():
    result = base.run()
    current = context()
    result.update(source_context=current, run_id=base.SECOND, cutoff="2026-12", model_cutoff="2026-12",
        observed_cutoff="2026-09", planning_basis=COMMERCIAL_CLOSE_BASIS,
        evaluation_mode="RETROSPECTIVE_TRAINING", engine_version="synthetic-scenario-1",
        emitted_at="2026-10-01T00:00:00+00:00", training_fingerprint="e" * 64)
    for product in result["products"]:
        product["bridge_horizons"] = []
        product["input_snapshot"] = {"source_context": current, "bridge_horizons": [],
            "commercial_history": [{"period": "2026-10", "basis": "COMMERCIAL_CLOSE", "value": "12.50"}]}
        for candidate in product["candidates"].values():
            candidate.update(evaluation_target_basis="OBSERVED_ONLY", eligible=False,
                             status="PROVISIONAL", reason="INSUFFICIENT_RETROSPECTIVE_WINDOWS")
            for row in candidate["horizons"]:
                row.update(forecast_distance=row["horizon"], ensemble_ml_model="Synthetic ML", ensemble_ml_component="20.20")
    return result


class CommercialCloseContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.storage = LocalPersistenceProvider(self.folder / "synthetic.sqlite")
        self.ref = base.save_run(self.folder, run())

    def test_real_dto_shape_projects_explicit_sealed_empty_bridge_without_mutating_origin(self):
        payload = run()
        for product in payload["products"]:
            product["input_snapshot"].pop("bridge_horizons")
        payload["content_digest"] = content_hash(payload)
        before = deepcopy(payload)
        header, = report_records(payload, "forecast_calculations", {(base.PRODUCT, base.CHAIN)})
        self.assertEqual(header["input_snapshot"]["bridge_horizons"], [])
        self.assertEqual(payload, before)
        scope, = annual_preview(payload, base.catalogue()[:1])
        self.assertEqual(scope["issue_period"], "2026-12")
        self.assertEqual(scope["latest_actual_period"], "2026-09")

    def test_missing_or_conflicting_bridge_origin_cannot_be_filled_or_hidden(self):
        for mode in ("missing_both", "wrong_geometry", "conflicting_snapshot"):
            payload = run()
            product = payload["products"][0]
            if mode == "missing_both":
                product.pop("bridge_horizons")
                product["input_snapshot"].pop("bridge_horizons")
            elif mode == "wrong_geometry":
                product["bridge_horizons"] = [{"target_period": "2026-12"}]
            else:
                product["input_snapshot"]["bridge_horizons"] = None
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "planning_bridge_lineage_mismatch"):
                report_records(payload, "forecast_calculations", {(base.PRODUCT, base.CHAIN)})

    def test_explicit_scenario_pin_does_not_relabel_the_observed_cutoff(self):
        current = context()
        require_sales_context(current, required=True)
        self.assertEqual(observed_cutoff(current), "2026-09")
        self.assertEqual(current["training_cutoff"], "2026-12")
        self.assertFalse(matches_source(base.context(), current))
        for change in ({"scenario_hash": "e" * 64}, {"observed_cutoff": "2026-08"}, {"planning_basis": "OBSERVED_ONLY"}):
            self.assertFalse(matches_source({**current, **change}, current))
        for change in ({"scenario_hash": "invalid"}, {"training_cutoff": "2026-09"}, {"generation": 5}, {"observed_cutoff": "2026-12"}):
            with self.subTest(change=change), self.assertRaises(PreviewError):
                require_sales_context({**current, **change})
        require_sales_context(base.context())

    def test_january_is_h1_at_one_month_from_december_and_q4_is_not_forecast(self):
        frozen = pinned_run(self.ref, context())
        rows = report_records(frozen, "forecast_calculation_horizons", {(base.PRODUCT, base.CHAIN)})
        self.assertEqual([row["target_period"] for row in rows], [f"2027-{m:02}-01" for m in range(1, 13)])
        self.assertEqual([row["horizon"] for row in rows], list(range(1, 13)))
        scope, = annual_preview(frozen, base.catalogue()[:1])
        self.assertEqual(scope["issue_period"], "2026-12")
        self.assertEqual(scope["products"][0]["horizons"][0]["target_period"], "2027-01")
        header, = report_records(frozen, "forecast_calculations", {(base.PRODUCT, base.CHAIN)})
        self.assertEqual(header["input_snapshot"]["bridge_horizons"], [])
        self.assertEqual(header["input_snapshot"]["commercial_history"][0]["basis"], "COMMERCIAL_CLOSE")

    def test_wrong_distance_input_source_or_evaluation_basis_is_never_served(self):
        for mutation in ("distance", "input_source", "evaluation_basis"):
            frozen = run()
            product = frozen["products"][0]
            if mutation == "distance":
                product["candidates"]["ML"]["horizons"][0]["forecast_distance"] = 4
            elif mutation == "input_source":
                product["input_snapshot"]["source_context"] = base.context()
            else:
                product["candidates"]["ML"]["evaluation_target_basis"] = "COMMERCIAL_CLOSE"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                report_records(frozen, "forecast_calculation_horizons", {(base.PRODUCT, base.CHAIN)})

    def test_read_rehashes_the_run_even_if_size_and_mtime_are_unchanged(self):
        pinned_run(self.ref, context())
        path = Path(self.ref["path"])
        stat = path.stat()
        raw = path.read_bytes()
        changed = raw.replace(b'10.10', b'10.11', 1)
        self.assertNotEqual(changed, raw)
        self.assertEqual(len(changed), len(raw))
        path.write_bytes(changed)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with self.assertRaisesRegex(PreviewError, "PLANNING_RUN_UNAVAILABLE"):
            pinned_run(self.ref, context())

    def test_observed_order_reader_never_returns_commercial_q4_as_evaluation_actuals(self):
        calls = []
        def transport(request):
            name = request.url.path.rsplit("/", 1)[-1]
            if name == "operational_read_context":
                rows = [context()]
            elif name == "products":
                rows = [{"id": base.PRODUCT, "chain_id": base.CHAIN, "product_code": "Synthetic",
                         "description": "Synthetic", "category_id": None}]
            elif name == "categories":
                rows = []
            elif name == "operational_read_catalog":
                rows = [{"chain_id": base.CHAIN, "product_id": base.PRODUCT, "purchase_type": "RS", "product_type": "TOALLA", "color": "SYNTHETIC"}]
            elif name == "operational_read_observations":
                calls.append(dict(request.url.params))
                rows = [{"chain_id": base.CHAIN, "product_id": base.PRODUCT, "period": "2026-09-01",
                    "metric_code": "ORDER", "value": 10, "value_text": "10.000", "id": base.SECOND,
                    "available_at": None, "availability_source": "UNKNOWN", "source_reference": "SYNTHETIC_OBSERVED"}]
            else:
                raise AssertionError("no stock, fill rate or scenario may replace actual ORDER")
            return httpx.Response(200, json=rows)
        client = SupabaseReadClient("http://127.0.0.1:8000", "synthetic", "synthetic", transport=httpx.MockTransport(transport))
        rows = SupabaseDataProvider(client).records(chain_id=base.CHAIN, objective="Pedido", issue_period="2026-12")
        self.assertEqual(rows[0]["period"], "2026-09")
        self.assertEqual(rows[0]["value"], "10.000")
        self.assertIsNone(rows[0]["available_at"])
        self.assertEqual(calls[0]["period"], "lte.2026-09-01")
        self.assertEqual(calls[0]["metric_code"], "eq.ORDER")

    def test_g5_human_champion_is_retained_and_never_inherited_by_g6(self):
        old_folder = self.folder / "G5"
        old_folder.mkdir()
        old_ref = base.save_run(old_folder, base.run())
        old = LocalChampionService(self.storage, old_ref)
        event = old.publish(base.request(), base.actor(), base.context(), base.catalogue(), idempotency_key="g5-explicit-choice")
        before = content_hash(self.storage.get("champion_history", event["id"]))
        registry = deepcopy(self.storage.items("champion_registry"))
        new = LocalChampionService(self.storage, self.ref)
        result = new.current(base.actor(), context(), base.catalogue(), planning_year=2027, chain_ids=[base.CHAIN], product_ids=[base.PRODUCT, base.SECOND])
        self.assertEqual(result["covered"], 0)
        self.assertEqual(registry, self.storage.items("champion_registry"))
        self.assertEqual(before, content_hash(self.storage.get("champion_history", event["id"])))
        with self.assertRaises(PreviewError):
            new.publish(base.request(), base.actor(), context(), base.catalogue(), idempotency_key="cannot-reuse-g5")
        request = {**base.request(), "source_context": context(), "run_id": base.SECOND}
        new_event = new.publish(request, base.actor(), context(), base.catalogue(), idempotency_key="g6-human-only")
        self.assertFalse(new_event["official_publication"])
        self.assertFalse(new_event["products"][0]["technical_eligible"])
        self.assertEqual(new_event["products"][0]["horizons"][0]["forecast_distance"], 1)
        self.assertEqual(before, content_hash(self.storage.get("champion_history", event["id"])))

    def test_cached_jobs_cannot_substitute_a_different_source_or_frozen_run(self):
        current = context()
        provider = SimpleNamespace(source_context=lambda: deepcopy(current), refresh_source_context=lambda: deepcopy(current),
            client=SimpleNamespace(catalog_rows=lambda: base.catalogue()), records=Mock(side_effect=AssertionError("no new fit")))
        compute = Mock(side_effect=AssertionError("frozen plan only"))
        runner = OperationalMultiChainForecastRunner(self.storage, require_authorized_sales=True, planning_reference=self.ref, compute=compute)
        self.addCleanup(runner.close)
        job = runner.submit(provider, base.actor(), chain_ids=[base.CHAIN], product_ids=[base.PRODUCT], objective="Pedido", issue_period="2026-12")
        self.assertEqual(job["scopes"][0]["issue_period"], "2026-12")
        with self.assertRaises(PreviewError):
            runner.get_job(job["job_id"], base.actor(), source_context=base.context())
        replacement = run()
        replacement["run_id"] = base.PRODUCT
        runner.planning_reference = base.save_run(self.folder, replacement)
        with self.assertRaisesRegex(PreviewError, "PREVIEW_NOT_FOUND"):
            runner.get_job(job["job_id"], base.actor(), source_context=current)
        self.assertEqual(runner.provisional_forecasts(base.actor(), source_context=current)["rows"], [])
        compute.assert_not_called()
        provider.records.assert_not_called()

    def test_startup_requires_installed_metadata_and_the_exact_frozen_run(self):
        gateway = LocalPostgresGateway.__new__(LocalPostgresGateway)
        gateway.config = {"local_champion_planning_run": self.ref}
        gateway.sales_policy = SimpleNamespace(config={"planning_basis": COMMERCIAL_CLOSE_BASIS}, context=lambda actual: {**context(), **actual})
        calls = []
        class Connection:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            @contextmanager
            def transaction(self):
                yield self
            def execute(self, query):
                calls.append(query)
                if query.startswith("set transaction read only"):
                    return None
                if "from public.official_source_current" not in query:
                    raise AssertionError("startup cannot read private facts or write")
                return SimpleNamespace(fetchall=lambda: [{key: context()[key] for key in ("source_hash", "generation", "first_period", "last_period")}])
        gateway.connect = lambda: Connection()
        proof = gateway.validate_planning()
        self.assertEqual(proof["planning_run_id"], base.SECOND)
        self.assertEqual(len(calls), 2)
        Path(self.ref["path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(PreviewError, "PLANNING_RUN_UNAVAILABLE"):
            gateway.validate_planning()


class CommercialCloseFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name) / "datos_privados"
        folder.mkdir()
        master = folder / "Synthetic_Unified.xlsx"
        master.write_bytes(b"synthetic-master")
        records, observed = folder / "EngineRecords_G6.json", folder / "ObservedRecords.json"
        records.write_text('[{"basis":"COMMERCIAL_CLOSE"}]')
        observed.write_text('[{"basis":"OBSERVED"}]')
        self.config = {"contract": "pedido-unificado-v1", "sheet": "Base_2024_2026", "unit": "PIEZAS",
            "master_path": str(master), "master_sha256": UNIFIED_MASTER_SHA256, "source_hash": context()["source_hash"], "generation": 6,
            "last_closed_period": "2026-12", "observed_cutoff": "2026-09", "planning_basis": COMMERCIAL_CLOSE_BASIS,
            "scenario_hash": context()["scenario_hash"], "scenario_records_path": str(records), "observed_records_path": str(observed),
            "scenario_records_sha256": hashlib.sha256(records.read_bytes()).hexdigest(), "observed_records_sha256": hashlib.sha256(observed.read_bytes()).hexdigest()}
        scenario = {"schema": "unified-commercial-close-scenario-1", "scenario_hash": self.config["scenario_hash"], "master_sha256": UNIFIED_MASTER_SHA256,
            "source_hash": self.config["source_hash"], "generation": 6, "observed_cutoff": "2026-09", "training_cutoff": "2026-12",
            "planning_basis": COMMERCIAL_CLOSE_BASIS, "planning_start": "2027-01", "planning_end": "2027-12",
            "engine_records_sha256": self.config["scenario_records_sha256"], "observed_records_sha256": self.config["observed_records_sha256"]}
        path = folder / "Scenario.json"
        path.write_text(json.dumps(scenario))
        self.config.update(scenario_path=str(path), scenario_file_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.policy = LocalSalesPolicy(self.config)
        # The existing master-hash tests cover XLSX authorization. These tests
        # isolate the three real synthetic evidence files without a real book.
        self.policy.master_available = lambda: True
        self.native = {key: context()[key] for key in ("source_hash", "generation", "first_period", "last_period")}

    def test_scenario_files_are_checked_on_every_operation_and_actuals_remain_separate(self):
        current = self.policy.context(self.native)
        require_sales_context(current)
        self.assertTrue(current["forecast_available"])
        self.assertEqual(current["observed_cutoff"], "2026-09")
        for resource in ("rpc/operational_read_inventory", "rpc/operational_read_observations", "forecast_calculations"):
            self.policy.require(resource, self.native)
        Path(self.config["scenario_records_path"]).write_text("[]")
        unavailable = self.policy.require("rpc/operational_read_observations", self.native)
        self.assertTrue(unavailable["target_source_available"])
        self.assertFalse(unavailable["forecast_available"])
        self.assertEqual(unavailable["forecast_block_code"], "PLANNING_SOURCE_UNAVAILABLE")
        require_sales_context(unavailable, for_training=False)
        with self.assertRaisesRegex(PreviewError, "PLANNING_SOURCE_UNAVAILABLE"):
            require_sales_context(unavailable)
        with self.assertRaisesRegex(PreviewError, "PLANNING_SOURCE_UNAVAILABLE"):
            self.policy.require("forecast_calculations", self.native)
        self.policy.require("rpc/operational_read_inventory", self.native)

    def test_no_evidence_alias_or_missing_undocumented_file_is_accepted(self):
        for field in ("scenario_path", "scenario_records_sha256", "observed_records_path"):
            bad = dict(self.config)
            bad.pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                LocalSalesPolicy(bad)
        Path(self.config["observed_records_path"]).unlink()
        self.assertFalse(self.policy.scenario_available())
        self.assertFalse(self.policy.available())

    def test_an_observed_or_scenario_hash_change_cannot_reuse_an_old_pin(self):
        first = self.policy.context(self.native)
        changed = {**first, "scenario_hash": "e" * 64}
        self.assertFalse(matches_source(first, changed))
        Path(self.config["scenario_path"]).write_text("{}")
        self.assertFalse(self.policy.context(self.native)["forecast_available"])


if __name__ == "__main__":
    unittest.main()
