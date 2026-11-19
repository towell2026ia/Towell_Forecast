"""The same sealed synthetic plan is served to reports and bounded previews."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from services.assistant_api import test_local_champion as fixtures
from services.assistant_api.supabase_access import PreviewError
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
