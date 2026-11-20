"""Synthetic isolated journal, exact selection, rolling calendar and admission."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
import httpx

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.assistant_api import test_local_champion as base
from services.assistant_api.test_commercial_close_context import context
from services.assistant_api.persistence import LocalPersistenceProvider
from services.assistant_api.rolling_api import mount_rolling_routes
from services.assistant_api.rolling_planning import RollingPlanningService
from services.assistant_api.rolling_store import RollingPlanningStore
from services.assistant_api.rolling_inputs import HostedRollingInputLoader
from services.assistant_api.rolling_report import scope_evaluation, sanitized
from services.assistant_api.runtime import SlidingWindowRateLimiter
from services.assistant_api.settings import Settings
from services.assistant_api.supabase_access import PreviewError
from services.forecast_engine.engine import add_month


class Provider:
    def __init__(self, ctx=None):
        self.context = deepcopy(ctx or context())
        self.client = SimpleNamespace(catalog_rows=lambda: base.catalogue())

    def source_context(self):
        return deepcopy(self.context)

    refresh_source_context = source_context


def inputs(_provider, _context, catalogue, chains):
    return {"rows": [], "observed_records": [], "metadata": {p["product_id"]: deepcopy(p) for p in catalogue if p["chain_id"] in chains}}


def compute(_rows, metadata, ctx, *, requested_product_ids, model_cutoff, observed_cutoff, run_id, emitted_at, **_kwargs):
    products = []
    for pid in requested_product_ids:
        candidates = {}
        for family in ("STATISTICAL", "ML", "ENSEMBLE"):
            candidates[family] = {"family": family, "model": "Synthetic " + family, "origin": "PYTHON_EXISTING_ENGINE",
                "calculable": True, "eligible": False, "reason": "SYNTHETIC_TECHNICAL_RESTRICTION", "metrics": {},
                "horizons": [{"horizon": h, "planning_horizon": h, "forecast_distance": h,
                    "target_period": add_month(model_cutoff, h), "value": "0.00" if h == 1 else "12.3456"} for h in range(1, 13)]}
        products.append({**metadata[pid], "product_id": pid, "candidates": candidates,
                         "input_snapshot": {"source_context": ctx, "bridge_horizons": []}})
    return {"schema": "unified-planning-run-1", "run_id": run_id, "source_context": ctx, "objective": "Pedido",
            "cutoff": model_cutoff, "model_cutoff": model_cutoff, "observed_cutoff": observed_cutoff,
            "emitted_at": emitted_at, "engine_version": "SYNTHETIC", "evaluation_mode": "RETROSPECTIVE_TRAINING", "products": products}


def restart_app(folder):
    """Only imported by the isolated synthetic subprocess regression below."""
    service = RollingPlanningService(LocalPersistenceProvider(Path(folder) / "isolated.sqlite"), inputs, compute=compute)
    app = FastAPI()
    @app.exception_handler(PreviewError)
    async def error(_request, exception):
        return JSONResponse(status_code=exception.status, content={"error_code": exception.code})
    mount_rolling_routes(app, SimpleNamespace(rolling_planning_enabled=True, forecast_rate_per_minute=50),
        base.Identity(), SimpleNamespace(bind=lambda _token: Provider()), service, SlidingWindowRateLimiter())
    return app


class RollingPlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "isolated.sqlite"
        self.persistence = LocalPersistenceProvider(self.path)
        self.service = RollingPlanningService(self.persistence, inputs, compute=compute, concurrency=2)
        self.addCleanup(self.service.close)
        self.provider = Provider()

    def request(self, **changes):
        return {"source_context": self.provider.source_context(), "chain_ids": [base.CHAIN],
                "product_ids": [base.PRODUCT], "objective": "Pedido", "issue_period": None, **changes}

    def ready(self, key="synthetic-1", request=None):
        summary = self.service.create(request or self.request(), base.actor(), self.provider, idempotency_key=key)
        for _ in range(200):
            summary = self.service.summary(summary["run_id"], base.actor(), self.provider)
            if summary["status"] in {"READY", "FAILED"}:
                break
            time.sleep(.005)
        self.assertEqual(summary["status"], "READY", summary)
        return summary

    def query(self, summary, **changes):
        return {"source_context": self.provider.source_context(), "chain_ids": [base.CHAIN], "product_ids": [base.PRODUCT],
                "window_start": summary["window_start"], "window_end": summary["window_end"], **changes}

    def test_new_run_does_not_replace_g6_and_is_recoverable_in_another_process(self):
        self.persistence.put("forecast_previews", "g6-preserved", {"literal": "original"})
        before = self.persistence.items("forecast_previews")
        summary = self.ready()
        self.assertEqual((summary["window_start"], summary["window_end"]), ("2027-01", "2027-12"))
        self.assertEqual(self.persistence.items("forecast_previews"), before)
        report = self.service.report(summary["run_id"], base.actor("reader"), self.provider, self.query(summary))
        self.assertEqual(len(report["horizons"]), 12)
        self.assertEqual(report["horizons"][0]["statistical_value_text"], "0.00")
        self.assertEqual(report["horizons"][1]["statistical_value_text"], "12.3456")
        self.assertEqual(self.service.current_champion(summary["run_id"], base.actor(), self.provider, self.query(summary))["covered"], 0)
        code = "from pathlib import Path;from services.assistant_api.persistence import LocalPersistenceProvider;from services.assistant_api.rolling_store import RollingPlanningStore;import json,sys;s=RollingPlanningStore(LocalPersistenceProvider(Path(sys.argv[1])));print(json.dumps(s.run(sys.argv[2])))"
        process = subprocess.run([sys.executable, "-c", code, str(self.path), summary["run_id"]], capture_output=True, text=True, check=True)
        restored = json.loads(process.stdout)
        self.assertEqual(restored, self.service.store.run(summary["run_id"]))

    def test_february_revision_window_is_march_through_next_february(self):
        self.provider.context = {**base.context(), "forecast_contract": "pedido-towell-v1", "target_sheet": "Pedidos",
            "generation": 7, "last_period": "2027-02", "training_cutoff": "2027-02"}
        summary = self.ready()
        self.assertEqual((summary["window_start"], summary["window_end"]), ("2027-03", "2028-02"))
        rows = self.service.report(summary["run_id"], base.actor(), self.provider, self.query(summary))["horizons"]
        self.assertEqual(rows[0]["target_period"], "2027-03-01")
        self.assertEqual(rows[-1]["target_period"], "2028-02-01")

    def test_parallel_idempotency_same_actor_and_key_creates_one_run(self):
        with ThreadPoolExecutor(2) as workers:
            results = list(workers.map(lambda _: self.service.create(self.request(), base.actor(), self.provider, idempotency_key="double-click"), range(2)))
        self.assertEqual(results[0]["run_id"], results[1]["run_id"])
        self.assertEqual(len(self.service.store.entries()), 1)
        with self.assertRaises(PreviewError) as error:
            self.service.create(self.request(product_ids=[base.SECOND]), base.actor(), self.provider, idempotency_key="double-click")
        self.assertEqual(error.exception.code, "PLANNING_IDEMPOTENCY_CONFLICT")

    def test_roles_and_scopes_cannot_execute_or_read_foreign_data(self):
        for principal in (base.actor("reader"), base.actor(scope=False)):
            with self.subTest(principal=principal.role), self.assertRaises(PreviewError):
                self.service.create(self.request(), principal, self.provider, idempotency_key="not-authorized")
        summary = self.ready()
        with self.assertRaises(PreviewError):
            self.service.report(summary["run_id"], base.actor(scope=False), self.provider, self.query(summary))

    def test_report_intersects_partial_run_without_g6_fallback(self):
        summary = self.ready()
        report = self.service.report(summary["run_id"], base.actor("reader"), self.provider,
                                     self.query(summary, product_ids=[base.PRODUCT, base.SECOND]))
        self.assertEqual(report["product_ids"], [base.PRODUCT])
        self.assertEqual({row["product_id"] for row in report["horizons"]}, {base.PRODUCT})
        empty = self.service.report(summary["run_id"], base.actor("reader"), self.provider, self.query(summary, product_ids=[base.SECOND]))
        self.assertEqual(empty["calculations"], [])

    def test_read_operations_use_one_current_catalogue_and_no_product_status_requests(self):
        from unittest.mock import Mock
        self.provider.client.catalog_rows = Mock(return_value=base.catalogue())
        self.provider.client.rows = Mock(return_value=[{"id": pid, "chain_id": base.CHAIN, "status": "ACTIVE"} for pid in (base.PRODUCT, base.SECOND)])
        first = self.ready("first")
        self.ready("second", self.request(product_ids=[base.SECOND]))
        operations = [lambda: self.service.summary(first["run_id"], base.actor("reader"), self.provider),
            lambda: self.service.list(base.actor("reader"), self.provider),
            lambda: self.service.report(first["run_id"], base.actor("reader"), self.provider, self.query(first)),
            lambda: self.service.current_champion(first["run_id"], base.actor("reader"), self.provider, self.query(first))]
        for operation in operations:
            self.provider.client.catalog_rows.reset_mock()
            self.provider.client.rows.reset_mock()
            operation()
            self.provider.client.catalog_rows.assert_called_once_with()
            self.provider.client.rows.assert_not_called()
        # No global cache can retain revoked membership on a later request.
        self.provider.client.catalog_rows.return_value = [base.catalogue()[1]]
        with self.assertRaises(PreviewError) as error:
            self.service.report(first["run_id"], base.actor("reader"), self.provider, self.query(first))
        self.assertEqual(error.exception.code, "SCOPE_FORBIDDEN")
        visible = self.service.list(base.actor("reader"), self.provider)
        self.assertEqual(len(visible["runs"]), 1)
        self.assertEqual(visible["runs"][0]["product_ids"], [base.SECOND])

    def test_execution_enriches_only_requested_authorized_chains_and_freezes_real_status(self):
        from unittest.mock import Mock
        foreign = {"product_id": base.RUN, "chain_id": base.SECOND, "purchase_type": "RS"}
        self.provider.client.catalog_rows = Mock(return_value=[*base.catalogue(), foreign])
        self.provider.client.rows = Mock(return_value=[{"id": base.PRODUCT, "chain_id": base.CHAIN, "status": "ACTIVE"},
                                                       {"id": base.SECOND, "chain_id": base.CHAIN, "status": "INACTIVE"}])
        result = self.service.create(self.request(), base.actor(), self.provider, idempotency_key="only-one-chain")
        self.service.close()
        self.provider.client.rows.assert_called_once_with("products", {"select": "id,chain_id,status", "chain_id": f"eq.{base.CHAIN}", "order": "id.asc"})
        frozen = self.service.store.request(result["run_id"])
        self.assertEqual(frozen["inputs"]["metadata"][base.PRODUCT]["status"], "ACTIVE")
        self.assertEqual(frozen["inputs"]["metadata"][base.SECOND]["status"], "INACTIVE")
        self.assertNotIn(base.RUN, frozen["inputs"]["metadata"])

    def test_explicit_champion_persists_recovers_and_does_not_change_ft_or_old_pointers(self):
        summary = self.ready()
        body = {**self.query(summary), "candidate_family": "STATISTICAL", "confirm": True, "comment": "Synthetic manual selection"}
        before = self.persistence.items("champion_registry")
        event = self.service.promote(summary["run_id"], base.actor(), self.provider, body, idempotency_key="select-1")
        self.assertFalse(event["official_publication"])
        self.assertFalse(event["ft_decision_changed"])
        self.assertFalse(event["products"][0]["technical_eligible"])
        self.assertEqual(self.persistence.items("champion_registry"), before)
        self.assertEqual(event["id"], self.service.promote(summary["run_id"], base.actor(), self.provider, body, idempotency_key="select-1")["id"])
        self.assertEqual(event["run_file_sha256"], self.service.store.artifact(summary["run_id"])["sha256"])
        self.assertEqual(event["run_artifact"]["storage"], "SQLITE_ROLLING_RUN")
        second = self.service.promote(summary["run_id"], base.actor(), self.provider, {**body, "candidate_family": "ML"}, idempotency_key="select-2")
        self.service.promote(summary["run_id"], base.actor(), self.provider, body, idempotency_key="select-1")
        reloaded = RollingPlanningService(LocalPersistenceProvider(self.path), inputs, compute=compute)
        try:
            saved = reloaded.current_champion(summary["run_id"], base.actor("reader"), self.provider, self.query(summary))
            self.assertEqual(saved["products"][0]["id"], second["id"])
            self.assertEqual(saved["products"][0]["product"]["horizons"][0]["value"], "0.00")
        finally:
            reloaded.close()

    def test_champion_rejects_roles_window_source_incomplete_curve_and_no_confirm(self):
        summary = self.ready()
        body = {**self.query(summary), "candidate_family": "STATISTICAL", "confirm": True, "comment": "Synthetic manual selection"}
        for who, change in ((base.actor("editor"), {}), (base.actor(), {"window_end": "2028-01"}),
                            (base.actor(), {"confirm": False}), (base.actor(), {"source_context": base.context()})):
            with self.subTest(change=change), self.assertRaises(PreviewError):
                self.service.promote(summary["run_id"], who, self.provider, {**body, **change}, idempotency_key="blocked")
        self.assertEqual(self.service.current_champion(summary["run_id"], base.actor(), self.provider, self.query(summary))["covered"], 0)

    def test_inputs_results_and_events_are_immutable_and_tampering_fails(self):
        summary = self.ready()
        with self.persistence._connect() as db:
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("UPDATE rolling_runs SET payload='{}'")
        self.assertEqual(self.service.store.run(summary["run_id"])["run_id"], summary["run_id"])

    def test_interrupted_jobs_fail_after_restart_without_reusing_credentials(self):
        store = RollingPlanningStore(self.persistence)
        frozen, _ = store.reserve(base.ACTOR, "interrupt", "e" * 64,
            {"source_context": context(), "product_ids": [base.PRODUCT], "window_start": "2027-01", "window_end": "2027-12"})
        store.mark_running(frozen["run_id"])
        reloaded = RollingPlanningService(LocalPersistenceProvider(self.path), inputs, compute=compute)
        try:
            self.assertEqual(reloaded.store.status(frozen["run_id"]), {"status": "FAILED", "error_code": "PLANNING_INTERRUPTED"})
            self.assertIsNone(reloaded.store.run(frozen["run_id"]))
        finally:
            reloaded.close()

    def test_failure_does_not_publish_or_log_secret_exception_text(self):
        def broken(*args, **kwargs):
            raise RuntimeError("never-persist-synthetic-sensitive-detail")
        self.service.compute = broken
        result = self.service.create(self.request(), base.actor(), self.provider, idempotency_key="failure")
        self.service.close()
        self.assertEqual(self.service.store.status(result["run_id"])["error_code"], "PLANNING_EXECUTION_FAILED")
        self.assertNotIn(b"never-persist", self.path.read_bytes())

    def test_flag_is_off_and_hosted_unprepared_configuration_rejected(self):
        self.assertFalse(Settings().rolling_planning_enabled)
        with self.assertRaisesRegex(ValueError, "rolling_planning_local_or_durable_hosted_required"):
            replace(Settings(), rolling_planning_enabled=True, app_env="production").validate()

    def test_cold_start_individual_acceptance_is_persistent_but_not_consolidable(self):
        def cold(*args, **kwargs):
            result = compute(*args, **kwargs)
            item = result["products"][0]
            value = deepcopy(item["candidates"]["STATISTICAL"])
            value.update(family="COLD_START", model="peer_recent_level", origin="INTERNAL_PEER_ORDER",
                admission={"status": "REQUIRES_EXPLICIT_ACCEPTANCE", "manual_selection_allowed": True,
                    "required_acknowledgements": ["COLD_START_METHOD", "NO_TARGET_WAPE", "UNDETERMINED_INCREMENTAL_EFFECT"]},
                incremental_effect={"status": "UNDETERMINED", "incremental_pieces": None})
            item["candidates"] = {"COLD_START": value}
            return result
        self.service.compute = cold
        summary = self.ready()
        query = self.query(summary)
        report = self.service.report(summary["run_id"], base.actor("reader"), self.provider, query)
        self.assertEqual(report["calculations"], [])
        self.assertEqual(len(report["provisionals"][0]["horizons"]), 12)
        self.assertEqual(report["provisionals"][0]["created_at"], self.service.store.run(summary["run_id"])["emitted_at"])
        body = {**query, "candidate_family": "COLD_START", "confirm": True,
                "comment": "Synthetic explicit cold start acceptance", "accept_cold_start": True}
        with self.assertRaises(PreviewError):
            self.service.promote(summary["run_id"], base.actor(), self.provider, body, idempotency_key="cold-choice")
        body["acknowledgements"] = ["COLD_START_METHOD", "NO_TARGET_WAPE", "UNDETERMINED_INCREMENTAL_EFFECT"]
        event = self.service.promote(summary["run_id"], base.actor(), self.provider, body, idempotency_key="cold-choice")
        self.assertFalse(event["products"][0]["consolidation_allowed"])
        self.assertEqual(event["products"][0]["admission_status"], "ACCEPTED_INDIVIDUAL_ONLY")
        saved = self.service.current_champion(summary["run_id"], base.actor("reader"), self.provider, query)
        self.assertEqual(saved["products"][0]["product"]["horizons"], event["products"][0]["horizons"])
        self.assertFalse(saved["products"][0]["product"]["consolidation_allowed"])

    def test_scope_metrics_use_identical_observed_pairs_and_not_mean_percentages(self):
        run = compute([], {p["product_id"]: p for p in base.catalogue()}, context(), requested_product_ids=[base.PRODUCT, base.SECOND],
            model_cutoff="2026-12", observed_cutoff="2026-09", run_id=base.RUN, emitted_at="2026-11-20T00:00:00Z")
        run["paired_evidence"] = [
            {"product_id": pid, "origin": "2026-07", "target_period": "2026-08", "horizon": 1, "actual": actual,
             "selected_statistical_model": "s", "selected_ml_model": "m", "ensemble_ml_model": "m",
             "ensemble_statistical_weight": .5, "statistical": {"s": stat}, "ml": {"m": ml}}
            for pid, actual, stat, ml in ((base.PRODUCT, 900, 810, 990), (base.SECOND, 100, 10, 90))]
        evaluation = scope_evaluation(run, run["products"])
        self.assertEqual(evaluation["families"]["STATISTICAL"]["wape"], 18)
        self.assertEqual(evaluation["families"]["STATISTICAL"]["bias"], 18)
        self.assertEqual(evaluation["families"]["ML"]["wape"], 10)
        self.assertEqual(evaluation["families"]["ML"]["bias"], -8)
        self.assertEqual((evaluation["observations"], evaluation["windows"]), (2, 1))
        self.assertEqual(evaluation["scope_leader"]["family"], "ENSEMBLE")
        run["paired_evidence"][1]["ml"] = {}
        incomplete = scope_evaluation(run, run["products"])
        self.assertIsNone(incomplete["scope_leader"])
        self.assertIsNone(incomplete["families"]["ML"]["wape"])
        self.assertEqual(scope_evaluation(run, run["products"][:1])["covered_variants"], 1)

    def test_peer_ids_from_other_reader_scopes_are_removed_from_report(self):
        value = {"target_product_ids": [base.PRODUCT, base.SECOND], "comparables": [
            {"product_id": base.PRODUCT, "chain_id": base.CHAIN}, {"product_id": base.SECOND, "chain_id": base.RUN}]}
        result = sanitized(value, {base.PRODUCT}, {base.CHAIN})
        self.assertEqual(result["target_product_ids"], [base.PRODUCT])
        self.assertEqual(result["comparables"], [{"product_id": base.PRODUCT, "chain_id": base.CHAIN}])

    def test_hosted_inputs_reuse_rls_actuals_and_scoped_sealed_scenario_only(self):
        from unittest.mock import patch
        provider = Provider()
        observed = {"chain_id": base.CHAIN, "product_id": base.PRODUCT, "period": "2026-09", "objective": "Pedido",
                    "value": "10.25", "source_reference": "synthetic!K1"}
        provider.records = lambda **_kwargs: [deepcopy(observed)]
        product = {"product_id": base.PRODUCT, "chain_id": base.CHAIN, "input_snapshot": {"source_context": context(),
            "commercial_history": [{"period": "2026-09", "value": "10.25", "basis": "OBSERVED", "source_reference": "synthetic!K1"},
                {"period": "2026-12", "value": "15.75", "basis": "COMMERCIAL_CLOSE", "source_reference": "synthetic!T1"}]}}
        archive = {"products": [product], "file_sha256": "c" * 64, "content_digest": "d" * 64}
        with patch("services.assistant_api.rolling_inputs.hosted_run", return_value=archive):
            result = HostedRollingInputLoader()(provider, context(), base.catalogue(), [base.CHAIN])
            self.assertEqual(len(result["rows"]), 2)
            self.assertEqual(len(result["observed_records"]), 1)
            self.assertEqual(result["rows"][1]["basis"], "COMMERCIAL_CLOSE")
            self.assertEqual(result["observed_records"][0]["basis"], "OBSERVED")
            product["input_snapshot"]["commercial_history"][0]["value"] = "99"
            with self.assertRaises(PreviewError):
                HostedRollingInputLoader()(provider, context(), base.catalogue(), [base.CHAIN])

    def test_hosted_same_volume_rejects_second_writer_before_recovery(self):
        writer = RollingPlanningService(self.persistence, inputs, compute=compute, hosted=True)
        try:
            with self.assertRaisesRegex(ValueError, "rolling_single_writer_required"):
                RollingPlanningService(LocalPersistenceProvider(self.path), inputs, compute=compute, hosted=True)
        finally:
            writer.close()
        reopened = RollingPlanningService(LocalPersistenceProvider(self.path), inputs, compute=compute, hosted=True)
        reopened.close()

    def test_future_hosted_commercial_scenario_uses_new_pinned_rpc_not_g6(self):
        from unittest.mock import Mock, patch
        provider = Provider()
        future = {**context(), "generation": 8, "training_cutoff": "2027-04", "observed_cutoff": "2027-02",
                  "last_period": "2027-04", "planning_start": "2027-05", "planning_end": "2028-04"}
        provider.records = lambda **kwargs: [{"chain_id": base.CHAIN, "product_id": base.PRODUCT,
            "period": "2027-02", "objective": "Pedido", "value": "12", "source_reference": "synthetic!K1"}]
        response = {"transport": "SUPABASE_SEALED_ROLLING_SCENARIO", "source_context": future,
            "content_digest": "a" * 64, "file_sha256": "b" * 64, "products": [
                {"product_id": base.PRODUCT, "chain_id": base.CHAIN, "input_snapshot": {"source_context": future,
                 "commercial_history": [{"period": "2027-03", "basis": "COMMERCIAL_CLOSE", "value": "17", "source_reference": "synthetic!T1"}]}}]}
        provider.client.read_rpc = Mock(return_value=response)
        with patch("services.assistant_api.rolling_inputs.hosted_run", side_effect=AssertionError("old G6 path forbidden")):
            loaded = HostedRollingInputLoader()(provider, future, base.catalogue(), [base.CHAIN])
        self.assertEqual(len(loaded["rows"]), 2)
        self.assertEqual(provider.client.read_rpc.call_args.args[0], "operational_read_rolling_scenario")
        response["source_context"] = context()
        with self.assertRaises(PreviewError):
            HostedRollingInputLoader()(provider, future, base.catalogue(), [base.CHAIN])
        provider.client.read_rpc.reset_mock()
        future["observed_cutoff"] = future["training_cutoff"]
        HostedRollingInputLoader()(provider, future, base.catalogue(), [base.CHAIN])
        provider.client.read_rpc.assert_not_called()

    def test_actual_http_process_restart_reads_same_rolling_run_and_champion(self):
        summary = self.ready()
        body = {**self.query(summary), "candidate_family": "ENSEMBLE", "confirm": True, "comment": "Synthetic confirmed ensemble"}
        saved = self.service.promote(summary["run_id"], base.actor(), self.provider, body, idempotency_key="http-restart")
        observed = []
        for _ in range(2):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            code = "import sys,uvicorn;from services.assistant_api.test_rolling_planning import restart_app;uvicorn.run(restart_app(sys.argv[1]),host='127.0.0.1',port=int(sys.argv[2]),log_level='error')"
            process = subprocess.Popen([sys.executable, "-c", code, self.temp.name, str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                url = f"http://127.0.0.1:{port}/api/forecast/planning-runs/{summary['run_id']}"
                headers = {"Authorization": "Bearer reader"}
                for attempt in range(100):
                    try:
                        response = httpx.get(url, headers=headers, timeout=1)
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.05)
                else:
                    self.fail("isolated_http_restart_not_ready")
                selected = httpx.post(url + "/champion", headers=headers, json=self.query(summary), timeout=2)
                self.assertEqual(selected.status_code, 200)
                observed.append(selected.json())
            finally:
                process.terminate()
                process.wait(timeout=10)
        self.assertEqual(observed[0], observed[1])
        self.assertEqual(observed[1]["products"][0]["id"], saved["id"])

    def test_api_bearer_only_authorization_and_read_only_report(self):
        app = FastAPI()
        @app.exception_handler(PreviewError)
        async def error(_request, exception):
            return JSONResponse(status_code=exception.status, content={"error_code": exception.code})
        data = SimpleNamespace(bind=lambda _token: self.provider)
        config = SimpleNamespace(rolling_planning_enabled=True, forecast_rate_per_minute=50)
        mount_rolling_routes(app, config, base.Identity(), data, self.service, SlidingWindowRateLimiter())
        with TestClient(app) as client:
            self.assertEqual(client.post("/api/forecast/planning-runs", json=self.request()).status_code, 401)
            self.assertEqual(client.post("/api/forecast/planning-runs", json=self.request(), headers={"Authorization": "Bearer reader", "Idempotency-Key": "r"}).status_code, 403)
            created = client.post("/api/forecast/planning-runs", json=self.request(), headers={"Authorization": "Bearer manager", "Idempotency-Key": "m"})
            self.assertEqual(created.status_code, 202)
            self.service.close()
            run_id = created.json()["run_id"]
            response = client.get("/api/forecast/planning-runs/" + run_id, headers={"Authorization": "Bearer reader"})
            self.assertEqual(response.json()["status"], "READY")
            report = client.post(f"/api/forecast/planning-runs/{run_id}/report", json={k: v for k, v in self.query(response.json()).items() if k not in {"window_start", "window_end"}}, headers={"Authorization": "Bearer reader"})
            self.assertEqual(report.status_code, 200)
            self.assertEqual(len(report.json()["horizons"]), 12)


if __name__ == "__main__":
    unittest.main()
