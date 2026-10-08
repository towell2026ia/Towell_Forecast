from __future__ import annotations

import base64
import copy
import json
import tempfile
import time
import unittest
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from services.forecast_engine.engine import (
    ForecastPolicy, add_month, forecast_dataset, normalize_dataset, normalize_retrospective_dataset,
)
from services.ml_engine.engine import MODEL_FACTORIES
from services.statistical_engine.engine import MODELS
from services.assistant_api.api import create_app
from services.assistant_api.auth import Principal, ROLE_PERMISSIONS, SupabaseAuthProvider
from services.assistant_api.data_provider import SupabaseDataProvider
from services.assistant_api.operational_preview import OperationalMultiChainForecastRunner, snapshot_hash
from services.assistant_api.persistence import LocalPersistenceProvider
from services.assistant_api.settings import Settings
from services.assistant_api.supabase_access import PreviewError, SupabaseReadClient

A, B, PRODUCT, OTHER, USER, CAT = [f"00000000-0000-4000-8000-{i:012d}" for i in range(1, 7)]
URL, KEY = "https://fixture.supabase.co", "sb_publishable_synthetic_fixture_key"


def jwt(role="ADMIN", **overrides):
    claims = {"sub": USER, "iss": URL + "/auth/v1", "aud": "authenticated", "exp": time.time() + 3600,
              "session_id": A, "test_role": role, **overrides}
    return "header." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".signature"


def fixture(months=43):
    return [{"chain_id": A, "product_id": product, "product_code": product, "description": "Synthetic only",
        "category": CAT, "category_id": CAT, "variant": "", "period": add_month("2023-01", i),
        "objective": "Venta", "value": (80 if i % 4 == 0 else 0) if product == OTHER else 100 + i,
        "available_at": None, "availability_source": "UNKNOWN", "source_id": f"{product}-{i}", "version_no": 1}
        for product in (PRODUCT, OTHER) for i in range(months)]


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.requests, self.extra_rows = [], []
        def handle(request):
            self.requests.append(request)
            token = request.headers.get("Authorization", "")[7:]
            if token == "invalid":
                return httpx.Response(401, json={"message": "must not leak"})
            role = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))["test_role"]
            path = request.url.path
            if path.endswith("/user"):
                return httpx.Response(200, json={"id": USER})
            tables = {
                "profiles": [{"id": USER, "status": "ACTIVE", "global_role": role}],
                "chains": [{"id": chain, "name": "Fixture", "status": "ACTIVE"} for chain in ([A, B] if role == "ADMIN" else [A])],
                "user_chain_access": [{"chain_id": A, "can_view": True, "can_run_forecast": role == "EDITOR"}],
                "categories": [{"id": CAT, "name": "Actual category"}],
                "products": [{"id": product, "chain_id": A, "product_code": product, "description": "Actual product", "category_id": CAT} for product in (PRODUCT, OTHER)],
                "portal_monthly_observations_current": [{"chain_id": A, "product_id": PRODUCT, "period": "2026-05-01", "metric_code": "SALES", "value": 0, "available_at": None, "availability_source": "UNKNOWN", "version_no": 1},
                    {"chain_id": A, "product_id": PRODUCT, "period": "2026-07-01", "metric_code": "SALES", "value": 12, "available_at": None, "availability_source": "UNKNOWN", "version_no": 1}, *self.extra_rows],
                "monthly_observations_current": [],
            }
            result = tables.get(path.rsplit("/", 1)[-1], [])
            if path.endswith("/products") and request.url.params.get("id"):
                result = [row for row in result if "eq." + row["id"] == request.url.params["id"]]
            if request.url.params.get("offset") != "0":
                result = []
            return httpx.Response(200, json=result)
        self.client = SupabaseReadClient(URL, KEY, transport=httpx.MockTransport(handle))

    def test_E2_D01_D04_D07_D08_D09_scoped_sales_literal_zero_and_missing(self):
        rows = SupabaseDataProvider(self.client.bind(jwt())).records(chain_id=A)
        self.assertEqual([row["period"] for row in rows], ["2026-05", "2026-07"])
        self.assertEqual(rows[0]["value"], 0)
        self.assertEqual(rows[0]["category_name"], "Actual category")
        self.assertEqual(rows[0]["product_id"], PRODUCT)
        self.assertIsNone(rows[0]["available_at"])
        query = self.requests[-1]
        self.assertEqual(query.url.params["metric_code"], "eq.SALES")
        self.assertEqual(query.url.params["chain_id"], "eq." + A)
        self.assertEqual(query.method, "GET")

    def test_E2_D02_anon_denied(self):
        with self.assertRaisesRegex(PreviewError, "AUTH_REQUIRED"):
            self.client.identity()

    def test_E2_D05_D06_foreign_metrics_not_silently_sales(self):
        for metric in ("ORDER", "DELIVERY"):
            self.extra_rows = [{"chain_id": A, "product_id": PRODUCT, "period": "2026-06-01", "metric_code": metric}]
            with self.assertRaisesRegex(PreviewError, "DATA_READ_FAILED"):
                SupabaseDataProvider(self.client.bind(jwt())).records(chain_id=A)

    def test_E2_D10_duplicate_rejected(self):
        self.extra_rows = [{"chain_id": A, "product_id": PRODUCT, "period": "2026-07-01", "metric_code": "SALES"}]
        with self.assertRaisesRegex(PreviewError, "DUPLICATE_OBSERVATION_CONFLICT"):
            SupabaseDataProvider(self.client.bind(jwt())).records(chain_id=A)

    def test_auth_verified_roles_and_grants(self):
        auth = SupabaseAuthProvider(self.client)
        viewer = auth.authenticate(token=jwt("VIEWER"), actor_id="admin", client_host="127.0.0.1")
        self.assertEqual(viewer.role, "reader")
        self.assertEqual(viewer.chain_ids, frozenset({A}))
        self.assertFalse(viewer.execute_chain_ids)
        editor = auth.authenticate(token=jwt("EDITOR"), actor_id=None, client_host=None)
        self.assertEqual(editor.execute_chain_ids, frozenset({A}))

    def test_invalid_expired_wrong_environment_and_manipulated_id(self):
        for token in ("invalid", jwt(exp=0), jwt(iss="https://other.supabase.co/auth/v1"), jwt(sub=B)):
            with self.assertRaisesRegex(PreviewError, "AUTH_REQUIRED"):
                self.client.bind(token).identity()

    def test_PIT_source_and_no_secret_representation(self):
        self.assertEqual(SupabaseDataProvider(self.client.bind(jwt())).records(chain_id=A, mode="POINT_IN_TIME", issue_period="2026-07"), [])
        self.assertTrue(self.requests[-1].url.path.endswith("monthly_observations_current"))
        self.assertEqual(self.requests[-1].url.params["available_at"], "not.is.null")
        self.assertNotIn(KEY, repr(self.client))

    def test_signature_must_be_verified_by_auth_server(self):
        forged = jwt()
        def deny(request):
            self.assertTrue(request.url.path.endswith("/auth/v1/user"))
            return httpx.Response(401, json={"message": "invalid signature"})
        client = SupabaseReadClient(URL, KEY, forged, transport=httpx.MockTransport(deny))
        with self.assertRaisesRegex(PreviewError, "AUTH_REQUIRED"):
            client.identity()

    def test_pagination_and_upstream_failure_are_bounded_safe(self):
        calls = []
        def pages(request):
            calls.append(request)
            return httpx.Response(200, json=[{"id": str(i)} for i in range(500)] if request.url.params["offset"] == "0" else [{"id": "last"}])
        client = SupabaseReadClient(URL, KEY, jwt(), transport=httpx.MockTransport(pages))
        self.assertEqual(len(client.rows("products", {"order": "id.asc"})), 501)
        self.assertEqual(len(calls), 2)
        for status, code in ((403, "SCOPE_FORBIDDEN"), (500, "DATA_READ_FAILED")):
            client = SupabaseReadClient(URL, KEY, jwt(), transport=httpx.MockTransport(lambda request: httpx.Response(status, json={"private": "secret"})))
            with self.assertRaisesRegex(PreviewError, code):
                client.rows("products", {})
        client = SupabaseReadClient(URL, KEY, jwt(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"not": "rows"})))
        with self.assertRaisesRegex(PreviewError, "DATA_READ_FAILED"):
            client.rows("products", {})

    def test_cross_scope_and_product_filters_do_not_trust_upstream_rows(self):
        self.extra_rows = [{"chain_id": B, "product_id": PRODUCT, "period": "2026-06-01", "metric_code": "SALES"}]
        with self.assertRaisesRegex(PreviewError, "DATA_READ_FAILED"):
            SupabaseDataProvider(self.client.bind(jwt())).records(chain_id=A)
        with self.assertRaisesRegex(PreviewError, "DATA_READ_FAILED"):
            SupabaseDataProvider(self.client.bind(jwt())).records(chain_id=A, product_id=OTHER)

    def test_inactive_profile_and_foreign_role_are_denied(self):
        for profile in ({"status": "INACTIVE", "global_role": "ADMIN"}, {"status": "ACTIVE", "global_role": "forged"}):
            def handle(request):
                return httpx.Response(200, json={"id": USER} if request.url.path.endswith("/user") else [{"id": USER, **profile}])
            with self.assertRaisesRegex(PreviewError, "SCOPE_FORBIDDEN"):
                SupabaseReadClient(URL, KEY, jwt(), transport=httpx.MockTransport(handle)).identity()


class TemporalPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = fixture()
        cls.result = forecast_dataset(cls.rows, "2026-07", evidence_mode="RETROSPECTIVE_TRAINING")

    def test_E2_T01_T07_PIT_strict(self):
        with self.assertRaisesRegex(ValueError, "availability_metadata_missing"):
            normalize_dataset(self.rows, "2026-07")
        with self.assertRaisesRegex(ValueError, "data_leakage_detected"):
            normalize_dataset([{**self.rows[0], "available_at": "2027-01-01"}], "2026-07")

    def test_E2_T02_T03_T04_T05_T06_literal_and_provisional(self):
        before = copy.deepcopy(self.rows)
        series = normalize_retrospective_dataset(self.rows, "2026-07")
        self.assertEqual(self.rows, before)
        self.assertTrue(all(item.available_at is None for product in series for item in product.observations.values()))
        chain = self.result["chains"][0]
        self.assertFalse(chain["temporal_certification"])
        self.assertIsNone(chain["model_audit"]["certified_wape"])
        self.assertIsNone(chain["model_audit"]["certified_bias"])
        self.assertFalse(chain["selection"]["automatic_promotion"])
        self.assertEqual(chain["certification_status"], "PROVISIONAL_TEMPORAL_UNKNOWN")

    def test_no_mode_mixing(self):
        with self.assertRaisesRegex(ValueError, "mixed_evidence_modes"):
            forecast_dataset([{**self.rows[0], "evidence_mode": "POINT_IN_TIME"}], "2026-07", evidence_mode="RETROSPECTIVE_TRAINING")

    def test_models_horizons_bands_reconciliation_and_repeatability(self):
        self.assertEqual(len(MODELS), 14)
        self.assertEqual(len(MODEL_FACTORIES), 3)
        chain = self.result["chains"][0]
        for product in (PRODUCT, OTHER):
            rows = [row for row in chain["forecast_towell"] if row["product_id"] == product]
            self.assertEqual(len(rows), 12)
            self.assertEqual(rows[0]["target_period"], "2026-08")
            self.assertEqual(rows[-1]["target_period"], "2027-07")
            self.assertTrue(all(row["forecast_towell"] >= 0 for row in rows))
            for row in rows:
                if row["probability"]:
                    band = [row["probability"][key] for key in ("p10", "p50", "p90", "p95")]
                    self.assertEqual(band, sorted(band))
                    self.assertIn(row["band_basis"], {"PRODUCT", "CATEGORY", "CHAIN"})
                    self.assertGreaterEqual(row["band_observations"], 3)
        for horizon in range(1, 13):
            total = round(sum(row["forecast_towell"] for row in chain["forecast_towell"] if row["horizon"] == horizon), 2)
            self.assertEqual(total, next(row["forecast_towell"] for row in chain["aggregates"] if row["level"] == "chain" and row["horizon"] == horizon))
        self.assertEqual(self.result, forecast_dataset(list(reversed(self.rows)), "2026-07", evidence_mode="RETROSPECTIVE_TRAINING"))

    def test_ML_failure_statistical_fallback(self):
        def fail():
            raise ValueError("synthetic failure")
        with patch.dict(MODEL_FACTORIES, {name: fail for name in MODEL_FACTORIES}):
            result = forecast_dataset(self.rows, "2026-07", evidence_mode="RETROSPECTIVE_TRAINING")
        self.assertEqual(len(result["chains"][0]["forecast_towell"]), 24)
        self.assertTrue(all(row["ml"] is None for row in result["chains"][0]["forecast_towell"]))

    def test_ensemble_failure_keeps_statistical_candidate(self):
        with patch("services.forecast_engine.engine._ensemble_weights", side_effect=RuntimeError("private failure")):
            chain = forecast_dataset(self.rows, "2026-07", evidence_mode="RETROSPECTIVE_TRAINING")["chains"][0]
        self.assertEqual(chain["model_audit"]["ensemble_failures"], ["ENSEMBLE_FAILED"])
        self.assertTrue(all(row["statistical"] == row["forecast_towell"] for row in chain["forecast_towell"]))
        self.assertFalse(chain["selection"]["promotion_allowed"])

    def test_missing_tail_lifecycle_and_no_fabricated_bands(self):
        rows = fixture(8)
        series = normalize_retrospective_dataset(rows, "2023-08")
        self.assertEqual(series[0].lifecycle("2023-08", ForecastPolicy()), "COLD_START")
        missing = normalize_retrospective_dataset([row for row in rows if row["period"] != "2023-07"], "2023-08")
        self.assertTrue(all(len(item.history("2023-08")) <= 1 for item in missing))
        inactive = normalize_retrospective_dataset([{**row, "value": 0 if row["period"] >= "2024-02" else row["value"]} for row in fixture(19)], "2024-07")
        self.assertTrue(all(item.lifecycle("2024-07", ForecastPolicy()) == "INACTIVE" for item in inactive))
        prelaunch = normalize_retrospective_dataset([{**row, "value": 0} for row in rows], "2023-08")
        self.assertTrue(all(item.lifecycle("2023-08", ForecastPolicy()) == "PRE-LAUNCH" for item in prelaunch))
        result = forecast_dataset(rows, "2023-08", evidence_mode="RETROSPECTIVE_TRAINING")
        for row in result["chains"][0]["forecast_towell"]:
            if row["probability"] is None:
                self.assertEqual(row["band_status"], "INSUFFICIENT_BAND_EVIDENCE")
                self.assertEqual(row["band_basis"], "INSUFFICIENT")
            else:
                self.assertGreaterEqual(row["band_observations"], 3)
                self.assertIn(row["band_basis"], {"PRODUCT", "CATEGORY", "CHAIN"})


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = LocalPersistenceProvider(Path(self.temp.name) / "preview.sqlite")
        self.actor = Principal(USER, "manager", ROLE_PERMISSIONS["manager"], A, frozenset({A, B}), frozenset({A, B}))
        self.runner = OperationalMultiChainForecastRunner(self.storage)
        class Data:
            def records(inner, *, chain_id, mode):
                return [{**row, "chain_id": chain_id} for row in fixture(19 if chain_id == A else 18)]
        self.data = Data()

    def tearDown(self):
        self.runner.close()
        self.temp.cleanup()

    def wait(self, job):
        for _ in range(1600):
            result = self.runner.get_job(job["job_id"], self.actor)
            if result["status"] in {"READY_PREVIEW", "READY_PROVISIONAL", "NOT_ELIGIBLE", "FAILED"}:
                return result
            time.sleep(.025)
        self.fail("job timeout")

    def test_jobs_async_idempotency_cutoffs_local_only_and_reauthorization(self):
        job = self.runner.submit(self.data, self.actor, chain_ids=[A, B])
        duplicate = self.runner.submit(self.data, self.actor, chain_ids=[A, B])
        self.assertEqual(job["job_id"], duplicate["job_id"])
        complete = self.wait(job)
        self.assertEqual(complete["status"], "READY_PREVIEW")
        self.assertEqual(complete["cuts_status"], "CUTS_NOT_ALIGNED")
        self.assertTrue(self.storage.list("forecast_previews"))
        for entity in ("forecast_vintages", "champion_registry", "normalized_observations"):
            self.assertEqual(self.storage.list(entity), [])
        viewer = replace(self.actor, role="reader", permissions=ROLE_PERMISSIONS["reader"], chain_ids=frozenset({A}))
        with self.assertRaisesRegex(PreviewError, "SCOPE_FORBIDDEN"):
            self.runner.result(job["job_id"], viewer)
        self.assertTrue(all(not product["horizons"] for scope in self.runner.result(job["job_id"], self.actor)["scopes"] for product in scope["products"]))
        rerun = self.wait(self.runner.submit(self.data, self.actor, chain_ids=[A, B]))
        self.assertTrue(all(scope["reused"] for scope in rerun["scopes"]))
        product_job = self.wait(self.runner.submit(self.data, self.actor, chain_ids=[A], product_id=PRODUCT))
        self.assertTrue(product_job["scopes"][0]["reused"])
        product_view = self.runner.result(product_job["job_id"], self.actor, PRODUCT)["scopes"][0]
        self.assertEqual([row["product_id"] for row in product_view["products"]], [PRODUCT])
        self.assertEqual(product_view["dataset_hash"], snapshot_hash(self.data.records(chain_id=A, mode="RETROSPECTIVE_TRAINING")))
        self.assertTrue(all(row["product_id"] == PRODUCT for row in product_view["selection"]["product_candidates"]))

    def test_missing_or_insufficient_is_not_fake_zero(self):
        class Short:
            def records(inner, **kwargs):
                return fixture(3)
        job = self.wait(self.runner.submit(Short(), self.actor, chain_ids=[A]))
        self.assertEqual(job["status"], "NOT_ELIGIBLE")
        self.assertEqual(job["scopes"][0]["error_code"], "NO_ELIGIBLE_PRODUCTS")
        self.assertEqual(job["scopes"][0]["products"][0]["history_months"], 3)
        self.assertEqual(job["scopes"][0]["products"][0]["minimum_history_months"], 6)
        self.assertEqual(self.storage.list("forecast_previews"), [])
        rendered = self.runner.result(job["job_id"], self.actor)
        self.assertEqual(rendered["status"], "NOT_ELIGIBLE")
        self.assertEqual(rendered["scopes"][0]["products"][0]["horizons"], [])
        self.assertEqual(self.runner.latest(self.actor, A)["status"], "NOT_ELIGIBLE")

    def test_two_prelaunch_zero_months_do_not_make_five_sales_months_eligible(self):
        class ShortLaunch:
            def records(inner, **kwargs):
                return [{**row, "period": add_month("2026-01", index), "value": 0 if index < 2 else 2500 + index}
                        for index, row in enumerate(fixture(7)[:7])]
        job = self.wait(self.runner.submit(ShortLaunch(), self.actor, chain_ids=[A], product_id=PRODUCT))
        self.assertEqual(job["status"], "NOT_ELIGIBLE")
        self.assertEqual(job["scopes"][0]["error_code"], "NO_ELIGIBLE_PRODUCTS")
        self.assertEqual(job["scopes"][0]["products"][0]["history_months"], 5)
        self.assertEqual(self.storage.list("forecast_previews"), [])

    def test_recent_product_gets_provisional_ml_path_without_official_writes(self):
        from services.assistant_api.test_cold_start import corpus, target
        class Launch:
            def records(inner, **kwargs):
                return corpus()
            def product_catalog(inner, chain):
                return [target()]
        job = self.wait(self.runner.submit(Launch(), self.actor, chain_ids=[A], product_id=target()["id"]))
        self.assertEqual(job["status"], "READY_PROVISIONAL")
        proposal = job["scopes"][0]["provisional_cold_start"]
        self.assertEqual(len(proposal["horizons"]), 12)
        self.assertEqual(proposal["observed_months"], 5)
        self.assertIsNone(proposal["target_wape"])
        self.assertEqual(self.runner.latest(self.actor, A, target()["id"])["status"], "READY_PROVISIONAL")
        self.assertEqual(self.storage.list("forecast_previews"), [])
        for entity in ("forecast_vintages", "champion_registry", "normalized_observations"):
            self.assertEqual(self.storage.list(entity), [])

    def test_research_is_scoped_optional_and_never_changes_provisional_quantities(self):
        from services.assistant_api.test_cold_start import corpus, target
        class Launch:
            def records(inner, **kwargs):
                return corpus()
            def product_catalog(inner, chain):
                return [target()]
        class Research:
            def start(inner, **kwargs):
                return {"status": "PENDING", "response_id": "resp_fixture"}
            def poll(inner, response_id):
                self.assertEqual(response_id, "resp_fixture")
                return {"status": "COMPLETED", "summary": "Fuente pública", "sources": []}
        self.runner.close()
        self.runner = OperationalMultiChainForecastRunner(self.storage, researcher=Research())
        job = self.wait(self.runner.submit(Launch(), self.actor, chain_ids=[A], product_id=target()["id"]))
        proposal = job["scopes"][0]["provisional_cold_start"]
        original_horizons = proposal["horizons"]
        self.assertEqual(proposal["research"]["status"], "PENDING")
        revoked = replace(self.actor, chain_ids=frozenset())
        with self.assertRaisesRegex(PreviewError, "SCOPE_FORBIDDEN"):
            self.runner.poll_research(job["job_id"], revoked)
        result = self.runner.poll_research(job["job_id"], self.actor)
        updated = result["scopes"][0]["provisional_cold_start"]
        self.assertEqual(updated["research"]["status"], "COMPLETED")
        self.assertEqual(updated["horizons"], original_horizons)
        self.assertEqual(self.storage.list("forecast_previews"), [])

    def test_research_route_requires_verified_jwt_and_scoped_job(self):
        config = replace(Settings(), data_provider="supabase", supabase_enabled=True,
            operational_preview_enabled=True, supabase_url=URL, supabase_publishable_key=KEY,
            ai_assistant_api_enabled=False, state_dir=Path(self.temp.name))
        case = ProviderTests(); case.setUp()
        self.storage.put("forecast_jobs", "OPJ-research-test", {"job_id": "OPJ-research-test",
            "kind": "OPERATIONAL_PREVIEW", "status": "READY_PROVISIONAL", "creator_id": USER,
            "chain_ids": [A], "product_id": PRODUCT, "mode": "RETROSPECTIVE_TRAINING",
            "scopes": [{"chain_id": A, "status": "PROVISIONAL_COLD_START",
                "provisional_cold_start": {"research": {"status": "DISABLED"}, "horizons": []}}]})
        app = create_app(settings=config, persistence=self.storage, provider=SupabaseDataProvider(case.client),
            auth_provider=SupabaseAuthProvider(case.client))
        route = "/api/forecast/preview-runs/OPJ-research-test/research"
        with TestClient(app) as client:
            self.assertEqual(client.get(route).status_code, 401)
            self.assertEqual(client.get(route, headers={"X-User-Role": "admin"}).status_code, 401)
            viewer = {"Authorization": "Bearer " + jwt("VIEWER")}
            self.assertEqual(client.get(route, headers=viewer).status_code, 200)
            foreign_job = self.storage.get("forecast_jobs", "OPJ-research-test")
            foreign_job["chain_ids"] = [B]
            self.storage.put("forecast_jobs", "OPJ-research-test", foreign_job)
            self.assertEqual(client.get(route, headers=viewer).status_code, 403)

    def test_hash_literal_deterministic_and_context_sensitive(self):
        rows = fixture(3)
        self.assertEqual(snapshot_hash(rows), snapshot_hash(rows[::-1]))
        self.assertNotEqual(snapshot_hash(rows), snapshot_hash([{**row, "available_at": "2026-01-01"} for row in rows]))

    def test_stage_progression_results_ready_only_and_maximum_two(self):
        release, entered = threading.Event(), threading.Event()
        active, maximum = 0, 0
        lock = threading.Lock()
        def compute(*args, **kwargs):
            nonlocal active, maximum
            with lock:
                active += 1; maximum = max(maximum, active)
                if active == 2:
                    entered.set()
            release.wait(5)
            try:
                return forecast_dataset(*args, **kwargs)
            finally:
                with lock:
                    active -= 1
        self.runner.compute = compute
        jobs = [self.runner.submit(self.data, self.actor, chain_ids=[A], product_id=PRODUCT),
                self.runner.submit(self.data, self.actor, chain_ids=[B], product_id=PRODUCT),
                self.runner.submit(self.data, self.actor, chain_ids=[A], product_id=OTHER)]
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaisesRegex(PreviewError, "PREVIEW_NOT_READY"):
                self.runner.result(jobs[0]["job_id"], self.actor)
            self.assertEqual(maximum, 2)
        finally:
            release.set()
        complete = [self.wait(job) for job in jobs]
        stages = [item["stage"] for item in complete[0]["stage_history"]]
        for stage in ("READING_DATA", "ELIGIBILITY", "STATISTICAL", "ML", "ENSEMBLE", "QUALITY_GATE", "READY_PREVIEW"):
            self.assertIn(stage, stages)
        self.assertEqual(maximum, 2)

    def test_persistence_survives_restart_and_interrupted_jobs_fail_safe(self):
        job = self.wait(self.runner.submit(self.data, self.actor, chain_ids=[A]))
        self.storage.put("forecast_jobs", "interrupted", {"job_id": "interrupted", "kind": "OPERATIONAL_PREVIEW", "status": "ML"})
        self.runner.close()
        self.runner = OperationalMultiChainForecastRunner(LocalPersistenceProvider(self.storage.path))
        self.assertEqual(self.runner.result(job["job_id"], self.actor, PRODUCT)["status"], "READY_PREVIEW")
        self.assertEqual(self.storage.get("forecast_jobs", "interrupted")["error_code"], "PREVIEW_INTERRUPTED")

    def test_legacy_registry_is_never_borrowed_and_context_must_match(self):
        self.storage.put("champion_registry", f"{A}|Venta|chain", {"version": "legacy", "chain_id": A, "objective": "Venta", "scope": "chain"})
        self.assertIsNone(self.runner._compatible_champion(A, "digest"))
        self.storage.put("champion_registry", f"{A}|Venta|chain", {"version": "matching", "chain_id": A, "objective": "Venta", "scope": "chain", "environment": "production", "dataset_hash": "digest"})
        self.assertEqual(self.runner._compatible_champion(A, "digest")["version"], "matching")
        self.assertIsNone(self.runner._compatible_champion(A, "different"))

    def test_latest_all_chain_does_not_return_single_scope_and_revoked_scope_denied(self):
        single = self.wait(self.runner.submit(self.data, self.actor, chain_ids=[A]))
        with self.assertRaisesRegex(PreviewError, "PREVIEW_NOT_FOUND"):
            self.runner.latest(self.actor)
        batch = self.wait(self.runner.submit(self.data, self.actor, chain_ids=[A, B], all_scopes=True))
        self.assertEqual(self.runner.latest(self.actor)["job_id"], batch["job_id"])
        scoped = self.runner.latest(self.actor, A, PRODUCT)
        self.assertEqual(scoped["job_id"], batch["job_id"])
        self.assertEqual(scoped["chain_ids"], [A])
        self.assertEqual(len(scoped["scopes"]), 1)
        viewer = replace(self.actor, role="reader", chain_ids=frozenset({A}), execute_chain_ids=frozenset())
        self.assertEqual(self.runner.latest(viewer, A, PRODUCT)["scopes"][0]["products"][0]["product_id"], PRODUCT)
        revoked = replace(self.actor, chain_ids=frozenset())
        with self.assertRaisesRegex(PreviewError, "SCOPE_FORBIDDEN"):
            self.runner.get_job(single["job_id"], revoked)
        with self.assertRaisesRegex(PreviewError, "SCOPE_FORBIDDEN"):
            self.runner.latest(revoked, A)

    def test_safe_complete_failure_and_invalid_predictions(self):
        self.runner.compute = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("sensitive upstream message"))
        job = self.wait(self.runner.submit(self.data, self.actor, chain_ids=[A]))
        self.assertEqual(job["status"], "FAILED")
        self.assertEqual(job["scopes"][0]["error_code"], "STATISTICAL_FAILED")
        self.assertNotIn("sensitive", json.dumps(job))
        self.assertEqual(self.storage.list("forecast_previews"), [])
        for value in (-1, float("nan"), float("inf")):
            with self.assertRaisesRegex(PreviewError, "STATISTICAL_FAILED"):
                self.runner._quality({"issue_period": "2026-07", "products": [{"horizons": [{"horizon": i, "target_period": add_month("2026-07", i), "forecast_towell": value, "statistical_value": value, "p50": value, "p10": None, "p90": None, "p95": None} for i in range(1, 13)]}]})

    def test_API_no_jwt_viewer_spoof_and_assigned_scopes(self):
        config = replace(Settings(), data_provider="supabase", supabase_enabled=True, operational_preview_enabled=True,
            supabase_url=URL, supabase_publishable_key=KEY, ai_assistant_api_enabled=False, state_dir=Path(self.temp.name))
        case = ProviderTests(); case.setUp()
        app = create_app(settings=config, persistence=self.storage, provider=SupabaseDataProvider(case.client), auth_provider=SupabaseAuthProvider(case.client))
        with TestClient(app) as client:
            self.assertEqual(client.post("/api/forecast/preview-runs", json={}).status_code, 401)
            self.assertEqual(client.post("/api/forecast/preview-runs", json={}, headers={"Authorization": "Bearer invalid"}).status_code, 401)
            viewer = {"Authorization": "Bearer " + jwt("VIEWER"), "X-User-Role": "admin", "X-User-ID": USER}
            self.assertEqual(client.post("/api/forecast/preview-runs", json={}, headers=viewer).status_code, 403)
            editor = {"Authorization": "Bearer " + jwt("EDITOR")}
            self.assertEqual(client.post("/api/forecast/preview-runs", json={"chain_id": B}, headers=editor).status_code, 403)
            self.assertEqual(client.post("/api/forecast/preview-runs", json={"chain_id": A}, headers=editor).status_code, 202)
            self.assertEqual(client.post("/api/forecast/preview-runs", json={"chain_id": A}, headers={"Authorization": "Bearer " + jwt()}).status_code, 202)
            self.assertEqual(client.get("/api/ready").status_code, 200)
            ready = client.get("/api/ready").json()
            self.assertFalse(ready["vintage_persistence"])
            self.assertFalse(ready["official_publication"])
            self.assertFalse(ready["champion_publication"])
            self.assertEqual(ready["service_target_fill_rate"], 95.0)
            candidate = "/api/forecast/vintages/candidates"
            self.assertEqual(client.post(candidate).status_code, 401)
            self.assertEqual(client.post(candidate, headers=viewer).status_code, 403)
            self.assertEqual(client.post(candidate, headers=editor).status_code, 403)
            self.assertEqual(client.post(candidate, headers={"Authorization": "Bearer " + jwt()}).json()["error_code"], "VINTAGE_PERSISTENCE_DISABLED")
            self.assertEqual(client.post(f"/api/forecast/vintages/{A}/publish", headers={"Authorization": "Bearer " + jwt()}).json()["error_code"], "OFFICIAL_PUBLICATION_DISABLED")
            close = f"/api/forecast/vintages/{A}/close"
            close_body = {"target_period": "2026-08-01", "confirm": True}
            self.assertEqual(client.post(close, json=close_body).status_code, 401)
            self.assertEqual(client.post(close, json=close_body, headers=viewer).status_code, 403)
            self.assertEqual(client.post(close, json=close_body, headers={"Authorization": "Bearer " + jwt()}).json()["error_code"], "VINTAGE_PERSISTENCE_DISABLED")
            self.assertEqual(client.post("/api/forecast/champion/promote", headers={"Authorization": "Bearer " + jwt()}).json()["error_code"], "CHAMPION_PUBLICATION_DISABLED")
            self.assertEqual(client.post("/api/forecast/run", json={"period": "2026-07"}, headers=editor).status_code, 503)

    def test_settings_only_allow_e2_not_future_or_bypass(self):
        valid = replace(Settings(), data_provider="supabase", supabase_enabled=True, operational_preview_enabled=True,
            supabase_url=URL, supabase_publishable_key=KEY, ai_assistant_api_enabled=False)
        valid.validate()
        self.assertFalse(valid.vintage_persistence_enabled)
        self.assertFalse(valid.official_publication_enabled)
        self.assertFalse(valid.champion_publication_enabled)
        replace(valid, vintage_persistence_enabled=True).validate()
        for invalid in (replace(valid, official_publication_enabled=True),
                        replace(valid, champion_publication_enabled=True)):
            with self.assertRaises(ValueError):
                invalid.validate()
        for settings in (replace(valid, openai_enabled=True), replace(valid, supabase_publishable_key="service-secret"),
                         replace(valid, ai_assistant_api_enabled=True), replace(valid, max_forecast_runs=3), replace(Settings(), supabase_enabled=True)):
            with self.assertRaises(ValueError):
                settings.validate()

    def test_E3_quality_read_is_scoped_and_missing_write_secret_fails_closed(self):
        config = replace(Settings(), data_provider="supabase", supabase_enabled=True,
            operational_preview_enabled=True, vintage_persistence_enabled=True,
            supabase_url=URL, supabase_publishable_key=KEY, ai_assistant_api_enabled=False,
            state_dir=Path(self.temp.name))
        case = ProviderTests(); case.setUp()
        data = SupabaseDataProvider(case.client)
        sales = data.bind(jwt()).records(chain_id=A)
        digest = snapshot_hash(sales)
        preview = {"status": "PREVIEW", "chain_id": A, "objective": "Venta", "issue_period": "2026-07",
            "mode": "RETROSPECTIVE_TRAINING", "dataset_hash": digest,
            "products": [], "eligibility": {"visible_products": 0, "stat_eligible": 0, "ml_eligible": 0},
            "statistical": {}, "ml": {}, "selection": {}}
        self.storage.put("forecast_previews", "fixture-preview", preview)
        self.storage.put("forecast_jobs", "OPJ-fixture", {"job_id": "OPJ-fixture", "kind": "OPERATIONAL_PREVIEW",
            "status": "READY_PREVIEW", "creator_id": USER, "chain_ids": [A], "product_id": None,
            "mode": "RETROSPECTIVE_TRAINING", "created_at": time.time(),
            "scopes": [{"chain_id": A, "preview_id": "fixture-preview", "status": "READY_PREVIEW"}]})
        app = create_app(settings=config, persistence=self.storage, provider=data,
            auth_provider=SupabaseAuthProvider(case.client))
        with TestClient(app) as client:
            admin = {"Authorization": "Bearer " + jwt()}
            viewer = {"Authorization": "Bearer " + jwt("VIEWER")}
            self.assertEqual(client.get(f"/api/forecast/quality-gates?chain_id={A}").status_code, 401)
            self.assertEqual(client.get(f"/api/forecast/quality-gates?chain_id={B}", headers=viewer).status_code, 403)
            quality = client.get(f"/api/forecast/quality-gates?chain_id={A}", headers=viewer)
            self.assertEqual(quality.status_code, 200)
            self.assertEqual(quality.json()["dataset_hash"], digest)
            self.assertEqual(quality.json()["service_level"]["status"], "NOT_MEASURABLE")
            self.assertEqual(client.get("/api/ready").json()["providers"]["vintage_write"]["error_code"], "REQUIRED_SECRET_MISSING")
            candidate = client.post("/api/forecast/vintages/candidates", json={"chain_id": A,
                "preview_id": "fixture-preview"}, headers={**admin, "Idempotency-Key": "key-1"})
            self.assertEqual(candidate.status_code, 503)
            self.assertEqual(candidate.json()["error_code"], "REQUIRED_SECRET_MISSING")

    def test_API_ready_preview_viewer_result_product_filter_and_rate_limit(self):
        config = replace(Settings(), data_provider="supabase", supabase_enabled=True, operational_preview_enabled=True,
            supabase_url=URL, supabase_publishable_key=KEY, ai_assistant_api_enabled=False, state_dir=Path(self.temp.name), forecast_rate_per_minute=2)
        case = ProviderTests(); case.setUp()
        app = create_app(settings=config, persistence=self.storage, provider=SupabaseDataProvider(case.client), auth_provider=SupabaseAuthProvider(case.client))
        admin, viewer = {"Authorization": "Bearer " + jwt()}, {"Authorization": "Bearer " + jwt("VIEWER")}
        with TestClient(app) as client, patch.object(SupabaseDataProvider, "records", return_value=fixture(19)):
            self.assertEqual(client.post("/api/forecast/preview-runs", json={"objective": "Pedido"}, headers=admin).status_code, 400)
            self.assertEqual(client.post("/api/forecast/preview-runs", json={"product_id": PRODUCT}, headers=admin).status_code, 400)
            self.assertEqual(client.post("/api/forecast/preview-runs", json={"chain_id": A, "product_id": B}, headers=admin).status_code, 403)
            result = client.post("/api/forecast/preview-runs", json={"chain_id": A, "product_id": PRODUCT}, headers=admin)
            self.assertEqual(result.status_code, 202)
            job_id = result.json()["job_id"]
            for _ in range(400):
                status = client.get("/api/forecast/preview-runs/" + job_id, headers=viewer)
                if status.json()["status"] in {"FAILED", "READY_PREVIEW"}:
                    break
                time.sleep(.025)
            self.assertEqual(status.json()["status"], "READY_PREVIEW")
            self.assertEqual(len(client.get(f"/api/forecast/preview-runs/{job_id}/result?product_id={PRODUCT}", headers=viewer).json()["scopes"][0]["products"][0]["horizons"]), 12)
            self.assertEqual(client.get(f"/api/forecast/preview-latest?chain_id={A}&product_id={PRODUCT}", headers=viewer).status_code, 200)
            self.assertEqual(client.get("/api/forecast/preview-latest?mode=unknown", headers=admin).status_code, 400)
            self.assertEqual(client.get(f"/api/forecast/preview-latest?chain_id={B}", headers=viewer).status_code, 403)
            self.assertEqual(client.get("/api/forecast/preview-runs/missing", headers=admin).status_code, 404)
            self.assertEqual(client.post("/api/forecast/preview-runs", json={"chain_id": A}, headers=admin).status_code, 202)
            self.assertEqual(client.post("/api/forecast/preview-runs", json={"chain_id": A}, headers=admin).status_code, 429)
