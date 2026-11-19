"""The same sealed synthetic plan is served to reports and bounded previews."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from services.assistant_api import test_local_champion as fixtures
from services.assistant_api.supabase_access import PreviewError
from services.assistant_api.operational_preview import OperationalMultiChainForecastRunner
from services.assistant_api.persistence import LocalPersistenceProvider
from services.assistant_api.unified_plan_reader import annual_preview, pinned_run, report_records


class PlanReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        run = fixtures.run()
        run.update(evaluation_mode="RETROSPECTIVE_TRAINING", engine_version="synthetic-1",
                   training_fingerprint="e" * 64)
        for product in run["products"]:
            product["input_snapshot"] = {"source_context": run["source_context"], "bridge_horizons": []}
            for row in product["candidates"]["ENSEMBLE"]["horizons"]:
                row.update(ensemble_ml_model="Synthetic ML", ensemble_ml_component="20.20")
        self.ref = fixtures.save_run(self.temp.name, run)
        self.run = pinned_run(self.ref, fixtures.context())
        self.allowed = {(fixtures.PRODUCT, fixtures.CHAIN)}

    def test_calendar_and_product_authorization_are_shared_by_readers(self):
        headers = report_records(self.run, "forecast_calculations", self.allowed)
        rows = report_records(self.run, "forecast_calculation_horizons", self.allowed)
        self.assertEqual([r["product_id"] for r in headers], [fixtures.PRODUCT])
        self.assertEqual({r["calculation_id"] for r in rows}, {headers[0]["id"]})
        self.assertEqual([r["target_period"] for r in rows], [f"2027-{m:02}-01" for m in range(1, 13)])
        scope, = annual_preview(self.run, fixtures.catalogue()[:1])
        self.assertEqual([r["forecast_towell"] for r in scope["products"][0]["horizons"]],
                         [r["ensemble_value"] for r in rows])
        self.assertEqual(scope["planning_run_id"], self.run["run_id"])
        self.assertFalse(report_records(self.run, "forecast_calculations", set()))

    def test_changed_source_file_or_version_fails_closed(self):
        other = {**fixtures.context(), "generation": 6}
        with self.assertRaises(PreviewError):
            pinned_run(self.ref, other)
        Path(self.ref["path"]).write_bytes(b"changed")
        with self.assertRaises(PreviewError):
            pinned_run(self.ref, fixtures.context())

    def test_sku_missing_a_forecast_cannot_borrow_it(self):
        run = deepcopy(self.run)
        for candidate in run["products"][0]["candidates"].values():
            for row in candidate["horizons"]:
                row["value"] = None
        self.assertFalse(report_records(run, "forecast_calculation_horizons", self.allowed))
        scope, = annual_preview(run, fixtures.catalogue()[:1])
        self.assertEqual(scope["products"][0]["forecast_status"], "INSUFFICIENT")
        self.assertEqual(scope["products"][0]["horizons"], [])

    def test_bounded_submit_reuses_frozen_plan_without_engine_or_provider_history(self):
        storage = LocalPersistenceProvider(Path(self.temp.name) / "bounded.sqlite")
        context = fixtures.context()
        catalog = fixtures.catalogue()
        provider = SimpleNamespace(source_context=lambda: deepcopy(context),
            refresh_source_context=lambda: deepcopy(context),
            client=SimpleNamespace(catalog_rows=lambda: deepcopy(catalog)),
            records=Mock(side_effect=AssertionError("frozen_plan_must_not_read_training_rows")))
        compute = Mock(side_effect=AssertionError("frozen_plan_must_not_execute_engine"))
        runner = OperationalMultiChainForecastRunner(storage, require_authorized_sales=True,
            planning_reference=self.ref, compute=compute)
        self.addCleanup(runner.close)
        arguments = {"chain_ids": [fixtures.CHAIN], "product_ids": [fixtures.PRODUCT, fixtures.SECOND],
                     "objective": "Pedido", "issue_period": "2026-09"}
        job = runner.submit(provider, fixtures.actor(), **arguments)
        self.assertEqual(job["status"], "READY_PREVIEW", job)
        self.assertEqual(job["planning_digest"], self.run["content_digest"])
        repeated = runner.submit(provider, fixtures.actor(), **{**arguments, "product_ids": list(reversed(arguments["product_ids"]))})
        self.assertEqual(repeated["job_id"], job["job_id"])
        self.assertEqual(len(storage.list("forecast_jobs")), 1)
        result = runner.result(job["job_id"], fixtures.actor(), source_context=context)
        scope, = result["scopes"]
        self.assertEqual({p["product_id"] for p in scope["products"]}, {fixtures.PRODUCT, fixtures.SECOND})
        for product in scope["products"]:
            self.assertEqual([r["target_period"] for r in product["horizons"]], [f"2027-{m:02}" for m in range(1, 13)])
            self.assertEqual([(int(r["target_period"][:4])-2026)*12 + int(r["target_period"][5:7])-9
                              for r in product["horizons"]], list(range(4, 16)))
        for changes in ({"issue_period": "2026-10"}, {"mode": "POINT_IN_TIME"},
                        {"product_ids": [fixtures.PRODUCT, "00000000-0000-4000-8000-000000000099"]}):
            with self.subTest(changes=changes), self.assertRaises(PreviewError):
                runner.submit(provider, fixtures.actor(), **{**arguments, **changes})
        original = catalog[0]["chain_id"]
        catalog[0]["chain_id"] = "00000000-0000-4000-8000-000000000098"
        with self.assertRaisesRegex(PreviewError, "SCOPE_FORBIDDEN"):
            runner.submit(provider, fixtures.actor(), **arguments)
        catalog[0]["chain_id"] = original
        self.assertEqual(len(storage.list("forecast_jobs")), 1)
        provider.records.assert_not_called()
        compute.assert_not_called()
