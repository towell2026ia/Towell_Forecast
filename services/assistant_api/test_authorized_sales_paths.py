"""Synthetic source isolation and real-engine independence, no hosted services."""
from __future__ import annotations

import copy
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import httpx
from unittest.mock import Mock, patch

from fastapi import FastAPI

from services.assistant_api.api import create_app
from services.assistant_api.auth import Principal, ROLE_PERMISSIONS
from services.assistant_api.data_provider import NormalizedDataProvider, SupabaseDataProvider
from services.assistant_api.e4_api import CalculationRequest, mount_e4_routes
from services.assistant_api.operational_preview import OperationalMultiChainForecastRunner, snapshot_hash
from services.assistant_api.persistence import LocalPersistenceProvider
from services.assistant_api.preview_api import mount_preview_routes
from services.assistant_api.runner import GeneralizedMonthlyForecastRunner, MonthlyForecastRunner
from services.assistant_api.settings import Settings
from services.assistant_api.supabase_access import PreviewError, SupabaseReadClient, matches_source, require_sales_context
from services.assistant_api.test_operational_preview import A, B, PRODUCT, OTHER, USER, CAT, fixture


def source_context():
    return {"source_hash": "a" * 64, "generation": 2, "first_period": "2023-01", "last_period": "2024-07",
        "sales_contract": "towell-sales-v1", "sales_source": "TOWELL", "sales_sheet": "Towell",
        "sales_file_sha256": "b" * 64, "authorized_source_hash": "a" * 64,
        "sales_training_cutoff": "2024-07", "sales_available": True, "sales_unavailable_reason": None,
        "inventory_source": "ESTADISTICA"}


class AccreditedProvider:
    def __init__(self, rows=None):
        self.rows = copy.deepcopy(fixture(19) if rows is None else rows)
        self.context = source_context()
        self.record_calls = []
        self.inventory = [{"period": "2024-07", "pieces": 5}]
        self.retired_history = [{"value": 700_000, "source": "retired"}]

    def source_context(self):
        return copy.deepcopy(self.context)

    refresh_source_context = source_context

    def records(self, *, chain_id, mode, issue_period=None):
        self.record_calls.append((chain_id, mode, issue_period))
        return [{**r, "chain_id": chain_id} for r in self.rows if not issue_period or r["period"] <= issue_period]

    def load(self, name):
        raise AssertionError("retired payload must never be loaded")


class AuthorizedSalesPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = LocalPersistenceProvider(Path(self.temp.name) / "state.sqlite")
        self.actor = Principal(USER, "manager", ROLE_PERMISSIONS["manager"], "synthetic-session", frozenset({A, B}), frozenset({A, B}))
        self.runner = OperationalMultiChainForecastRunner(self.storage, require_authorized_sales=True)
        self.addCleanup(self.runner.close)

    def finish(self, provider, **kwargs):
        job = self.runner.submit(provider, self.actor, chain_ids=[A], **kwargs)
        self.runner.close()
        return self.storage.get("forecast_jobs", job["job_id"])

    def test_local_context_cannot_be_omitted_or_downgraded_before_execution(self):
        for bad in (None, {"source_hash": "a" * 64, "generation": 2},
                    {**source_context(), "sales_sheet": "Pedidos"},
                    {**source_context(), "generation": True}):
            provider = AccreditedProvider(); provider.context = bad
            with self.assertRaisesRegex(PreviewError, "OFFICIAL_SOURCE_MISMATCH"):
                self.runner.submit(provider, self.actor, chain_ids=[A])
            self.assertFalse(provider.record_calls)
        self.assertFalse(self.storage.list("forecast_jobs"))

    def test_optional_null_reason_is_not_a_different_source_but_sales_markers_are(self):
        context = source_context(); without_reason = dict(context); without_reason.pop("sales_unavailable_reason")
        self.assertTrue(matches_source(context, without_reason))
        for field in ("sales_source", "sales_sheet", "sales_file_sha256", "sales_training_cutoff", "authorized_source_hash"):
            bad = dict(context); bad.pop(field)
            self.assertFalse(matches_source(bad, context), field)
        with self.assertRaisesRegex(PreviewError, "SALES_SOURCE_UNAVAILABLE"):
            require_sales_context({**context, "sales_available": False}, required=True)

    def test_global_run_cannot_expand_to_unauthorized_or_duplicate_scopes(self):
        actor = replace(self.actor, execute_chain_ids=frozenset({A}))
        provider = AccreditedProvider()
        for scopes in ([A, B], [A, A], []):
            with self.assertRaisesRegex(PreviewError, "SCOPE_FORBIDDEN"):
                self.runner.submit(provider, actor, chain_ids=scopes, all_scopes=True)
        self.assertFalse(provider.record_calls)

    def test_missing_master_and_future_cutoff_cannot_start_compute_or_read_cache(self):
        provider = AccreditedProvider(); provider.context["sales_available"] = False
        with self.assertRaisesRegex(PreviewError, "SALES_SOURCE_UNAVAILABLE"):
            self.runner.submit(provider, self.actor, chain_ids=[A])
        provider.context = source_context()
        with self.assertRaisesRegex(PreviewError, "SALES_PERIOD_NOT_CLOSED"):
            self.runner.submit(provider, self.actor, chain_ids=[A], issue_period="2024-08")
        self.assertFalse(provider.record_calls)

    def test_context_change_before_compute_fails_closed(self):
        provider = AccreditedProvider()
        provider.refresh_source_context = lambda: {**source_context(), "source_hash": "c" * 64, "authorized_source_hash": "c" * 64}
        self.runner.compute = Mock(side_effect=AssertionError("must not compute"))
        job = self.finish(provider)
        self.assertEqual(job["status"], "FAILED")
        self.assertEqual(job["scopes"][0]["error_code"], "OFFICIAL_SOURCE_MISMATCH")
        self.runner.compute.assert_not_called()
        self.assertFalse(self.storage.list("forecast_previews"))

    def test_context_change_during_compute_cannot_save_a_ready_preview(self):
        provider = AccreditedProvider()
        def changed(*args, **kwargs):
            provider.context["sales_available"] = False
            return {"chains": [{}]}
        self.runner.compute = changed
        job = self.finish(provider)
        self.assertEqual(job["status"], "FAILED")
        self.assertEqual(job["scopes"][0]["error_code"], "SALES_SOURCE_UNAVAILABLE")
        self.assertFalse(self.storage.list("forecast_previews"))

    def test_explicit_retrospective_cutoff_reloads_its_own_catalogue_metadata(self):
        provider = AccreditedProvider()
        self.runner.compute = lambda *args, **kwargs: {"chains": []}
        self.finish(provider, issue_period="2024-06")
        self.assertEqual([call[2] for call in provider.record_calls], [None, "2024-06"])

    def test_stock_only_catalogue_products_do_not_invent_launch_dates_or_fail_the_scope(self):
        provider = AccreditedProvider()
        stock_ids = ["00000000-0000-4000-8000-000000000020", "00000000-0000-4000-8000-000000000021"]
        provider.product_catalog = lambda chain: [{"id": pid, "product_code": pid, "description": "Stock only",
            "first_seen_period": first_seen} for pid, first_seen in zip(stock_ids, (None, "2030-01-01"))]
        self.runner.compute = lambda *args, **kwargs: {"chains": []}
        job = self.finish(provider)
        self.assertEqual(job["status"], "NOT_ELIGIBLE")
        states = {r["product_id"]: r for r in job["scopes"][0]["products"]}
        self.assertTrue(all(states[pid]["forecast_status"] == "INSUFFICIENT" for pid in stock_ids))
        self.assertTrue(all(states[pid]["history_months"] == 0 for pid in stock_ids))

    def test_stored_preview_cannot_be_relabelled_with_the_current_job_context(self):
        preview = {"chain_id": A, "objective": "Venta", "issue_period": "2024-07", "dataset_hash": "d" * 64,
                   "source_context": {**source_context(), "source_hash": "c" * 64, "authorized_source_hash": "c" * 64},
                   "products": []}
        self.storage.put("forecast_previews", "retired", preview)
        job = {"job_id": "mixed", "kind": "OPERATIONAL_PREVIEW", "chain_ids": [A], "status": "READY_PREVIEW",
               "source_context": source_context(), "scopes": [{"chain_id": A, "preview_id": "retired"}]}
        self.storage.put("forecast_jobs", "mixed", job)
        with self.assertRaisesRegex(PreviewError, "LINEAGE_MISMATCH"):
            self.runner.result("mixed", self.actor, PRODUCT, source_context=source_context())
        self.assertEqual(self.storage.get("forecast_previews", "retired"), preview)
        for read in (lambda: self.runner.get_job("mixed", self.actor),
                     lambda: self.runner.latest(self.actor, A),
                     lambda: self.runner.provisional_forecasts(self.actor)):
            with self.assertRaisesRegex(PreviewError, "OFFICIAL_SOURCE_MISMATCH"):
                read()

    def test_local_cold_start_and_research_poll_never_start_external_research(self):
        provider = AccreditedProvider([])
        provider.product_catalog = lambda chain: [{"id": PRODUCT, "chain_id": chain, "product_code": PRODUCT,
                                                  "description": "Synthetic", "category_id": None}]
        self.runner.researcher = Mock()
        job = {"job_id": "cold", "kind": "OPERATIONAL_PREVIEW", "chain_ids": [A], "product_id": PRODUCT,
               "mode": "RETROSPECTIVE_TRAINING", "status": "READY_PROVISIONAL", "source_context": source_context()}
        with patch("services.assistant_api.operational_preview.launch_forecast", return_value={"horizons": []}):
            scope = self.runner._cold_start_scope(provider, job, A, "2024-07", [{"chain_name": "Synthetic"}])
        self.assertEqual(scope["provisional_cold_start"]["research"], {"status": "DISABLED", "reason": "LOCAL_PILOT_RESEARCH_DISABLED"})
        job["scopes"] = [scope]; self.storage.put("forecast_jobs", "cold", job)
        with self.assertRaisesRegex(PreviewError, "DEEP_RESEARCH_DISABLED"):
            self.runner.poll_research("cold", self.actor, source_context=source_context())
        self.runner.researcher.start.assert_not_called()
        self.runner.researcher.poll.assert_not_called()

    def test_retained_champion_is_never_loaded_by_local_legacy_runners(self):
        provider = AccreditedProvider()
        for factory in (MonthlyForecastRunner, GeneralizedMonthlyForecastRunner):
            runner = factory(provider, state_dir=Path(self.temp.name), require_authorized_sales=True)
            with self.assertRaisesRegex(ValueError, "authorized_sales_requires_operational_preview"):
                runner.run_month("2024-07")
        with patch.dict(os.environ, {"LOCAL_POSTGRES_CONFIG": "synthetic-local-marker"}):
            runner = MonthlyForecastRunner(NormalizedDataProvider(), state_dir=Path(self.temp.name))
            with self.assertRaisesRegex(ValueError, "authorized_sales_requires_operational_preview"):
                runner.run_month("2024-07")
        self.assertFalse((Path(self.temp.name) / "runs").exists())

    def test_composition_uses_literal_applied_scope_ml_not_the_individual_winner(self):
        row = {"statistical": 100., "ml": 200., "forecast_towell": 125., "ml_model": "Scope Forest"}
        composition = self.runner._applied_composition(row, {"statistical_weight": .75})
        self.assertEqual(composition, {"statistical_weight": .75, "ml_weight": .25,
          "ensemble_ml_model": "Scope Forest", "ensemble_ml_component": 200.})
        self.assertTrue(all(value is None for value in self.runner._applied_composition(row, {}).values()))
        statistical = self.runner._applied_composition({**row, "ml": None, "forecast_towell": 100.,
          "model_strategy": "statistical_fallback"}, {"statistical_weight": .75})
        self.assertEqual(statistical, {"statistical_weight": 1., "ml_weight": 0., "ensemble_ml_model": None, "ensemble_ml_component": None})


class IndependentRealEngineTests(unittest.TestCase):
    def test_retired_history_and_inventory_changes_cannot_change_three_real_engine_runs(self):
        """Three fresh fits, fixed built-in seeds/policy; no engine mocks/cache."""
        results = []
        actor = Principal(USER, "manager", ROLE_PERMISSIONS["manager"], "synthetic-session", frozenset({A}), frozenset({A}))
        for mutation in ("baseline", "retired_history", "inventory"):
            with TemporaryDirectory() as tmp:
                storage = LocalPersistenceProvider(Path(tmp) / "state.sqlite")
                fixture_provider = AccreditedProvider()
                if mutation == "retired_history": fixture_provider.retired_history = [{"value": 999_999_999, "source": "retired"}]
                if mutation == "inventory": fixture_provider.inventory = [{"period": "2024-07", "pieces": 999_999_999}]
                requests = []
                def transport(request):
                    path = request.url.path; params = request.url.params
                    requests.append(path)
                    if path.endswith("/operational_read_context"):
                        return httpx.Response(200, json=[source_context()])
                    if path.endswith("/operational_read_catalog"):
                        return httpx.Response(200, json=[{"chain_id": A, "product_id": pid, "purchase_type": "RS",
                            "product_type": "Toalla", "color": "Synthetic color"} for pid in (PRODUCT, OTHER)])
                    if path.endswith("/products"):
                        return httpx.Response(200, json=[{"id": pid, "chain_id": A, "product_code": pid,
                            "description": "Synthetic only", "category_id": CAT, "first_seen_period": None}
                            for pid in (PRODUCT, OTHER)])
                    if path.endswith("/categories"):
                        return httpx.Response(200, json=[{"id": CAT, "name": "Synthetic category"}])
                    if path.endswith("/operational_read_observations"):
                        self.assertEqual(params["p_source_hash"], source_context()["source_hash"])
                        self.assertEqual(params["p_generation"], "2")
                        self.assertEqual(params["metric_code"], "eq.SALES")
                        return httpx.Response(200, json=[{"chain_id": A, "product_id": r["product_id"],
                            "period": r["period"]+"-01", "metric_code": "SALES", "value": r["value"],
                            "value_text": str(r["value"]), "available_at": None, "availability_source": "UNKNOWN",
                            "version_no": 1, "observation_id": r["source_id"], "source_batch_id": None}
                            for r in fixture_provider.rows])
                    if path.endswith("/monthly_observations"):
                        return httpx.Response(200, json=fixture_provider.retired_history)
                    if path.endswith("/operational_read_inventory"):
                        return httpx.Response(200, json=fixture_provider.inventory)
                    raise AssertionError("unexpected external/data route: "+path)
                provider = SupabaseDataProvider(SupabaseReadClient("https://synthetic.invalid", "synthetic-not-a-key",
                    token="synthetic-fixture-not-a-JWT", transport=httpx.MockTransport(transport)))
                active_rows = provider.records(chain_id=A)
                # A poisoned old registry even matches scope/environment/data
                # digest. Its retired source still cannot become an incumbent.
                retired = {"chain_id": A, "objective": "Venta", "scope": "chain", "environment": "test",
                    "dataset_hash": snapshot_hash(active_rows), "version": mutation,
                    "strategy": "ml", "statistical_weight": 0.,
                    "source_context": {**source_context(), "source_hash": "f" * 64, "authorized_source_hash": "f" * 64}}
                storage.put("champion_registry", f"{A}|Venta|chain", retired)
                runner = OperationalMultiChainForecastRunner(storage, environment="test", require_authorized_sales=True)
                try:
                    job = runner.submit(provider, actor, chain_ids=[A], product_id=PRODUCT)
                    runner.close()
                    saved = storage.get("forecast_jobs", job["job_id"])
                    self.assertEqual(saved["status"], "READY_PREVIEW", saved)
                    result = runner.result(job["job_id"], actor, PRODUCT, source_context=source_context())["scopes"][0]
                    self.assertIsNone(result["selection"]["published_champion"])
                    self.assertEqual(storage.get("champion_registry", f"{A}|Venta|chain"), retired)
                    self.assertTrue(all(r["ml_value"] is not None for r in result["products"][0]["horizons"]))
                    self.assertEqual({c["family"] for c in result["selection"]["product_candidates"]}, {"statistical", "ml", "ensemble"})
                    self.assertNotIn("/rest/v1/monthly_observations", requests)
                    self.assertNotIn("/rest/v1/rpc/operational_read_inventory", requests)
                    results.append({key: result[key] for key in ("dataset_hash", "products", "statistical", "ml", "selection", "aggregates")})
                finally:
                    runner.close()
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])


class LocalRouteFencesTests(unittest.TestCase):
    def endpoint(self, app, path):
        def routes(router):
            for route in router.routes:
                if hasattr(route, "original_router"):
                    yield from routes(route.original_router)
                else:
                    yield route
        return next(route.endpoint for route in routes(app) if getattr(route, "path", None) == path)

    def test_local_e4_write_and_stale_preview_never_reach_the_writer(self):
        from services.assistant_api.test_e4_api import FakeIdentity, FakeSource, FakeWriter
        from services.assistant_api.test_calculation_history import ACTOR, CHAIN, PRODUCT as E4_PRODUCT, preview
        saved = {"horizons": []}; source = FakeSource([], saved); writer = FakeWriter(saved)
        ctx = {**source_context(), "last_period": "2026-07", "sales_training_cutoff": "2026-07"}
        source.operational_context = lambda: copy.deepcopy(ctx)
        source.records = Mock(return_value=[])
        archived = preview(); archived["source_context"] = dict(ctx); archived["dataset_hash"] = snapshot_hash([])
        runner = SimpleNamespace(persistence=SimpleNamespace(get=lambda *args: archived))
        flags = SimpleNamespace(forecast_calculation_history_enabled=True, forecast_selection_enabled=False,
          capture_center_enabled=False, live_learning_enabled=False, git_sha="synthetic", local_postgres_config=Path("synthetic-local-config"))
        app = FastAPI(); mount_e4_routes(app, flags, FakeIdentity(), source, runner, writer)
        create = self.endpoint(app, "/api/forecast/calculations")
        request = SimpleNamespace(state=SimpleNamespace())
        body = CalculationRequest(chain_id=CHAIN, product_id=E4_PRODUCT, preview_id="preview-1")
        for context in ({"source_hash": "a" * 64, "generation": 2}, {**ctx, "sales_sheet": "Pedidos"}):
            archived["source_context"] = context
            with self.assertRaisesRegex(PreviewError, "PREVIEW_NOT_READY"):
                create(request, body, "Bearer admin", "synthetic")
        archived["source_context"] = ctx
        with self.assertRaisesRegex(PreviewError, "LOCAL_WRITE_DISABLED"):
            create(request, body, "Bearer admin", "synthetic")
        self.assertFalse(writer.calls)

    def test_local_research_and_vintage_publication_are_blocked_even_if_flags_are_set(self):
        from services.assistant_api.test_e4_api import FakeIdentity
        from services.assistant_api.test_calculation_history import CHAIN
        from services.assistant_api.preview_api import CandidateRequest
        flags = SimpleNamespace(operational_preview_enabled=True, local_postgres_config=Path("synthetic-local-config"),
          forecast_rate_per_minute=10, vintage_persistence_enabled=True, supabase_service_role_key="synthetic-not-a-key")
        data, runner, writer = Mock(), Mock(), Mock()
        limiter = Mock(); limiter.allow.return_value = True
        app = FastAPI(); mount_preview_routes(app, flags, FakeIdentity(), data, runner, limiter, writer)
        request = SimpleNamespace(state=SimpleNamespace())
        with self.assertRaisesRegex(PreviewError, "DEEP_RESEARCH_DISABLED"):
            self.endpoint(app, "/api/forecast/preview-runs/{job_id}/research")("synthetic-job", request, "Bearer admin")
        runner.poll_research.assert_not_called()
        with self.assertRaisesRegex(PreviewError, "LOCAL_WRITE_DISABLED"):
            self.endpoint(app, "/api/forecast/vintages/candidates")(request,
              CandidateRequest(chain_id=CHAIN, preview_id="synthetic-preview"), "Bearer admin", "synthetic-idempotency")
        writer.rpc.assert_not_called()
        data.bind.assert_not_called()

    def test_local_app_never_constructs_external_research_and_requires_source_on_runner(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            config = replace(Settings(), state_dir=directory, data_provider="supabase", supabase_enabled=True,
              operational_preview_enabled=True, supabase_url="http://127.0.0.1:8000",
              supabase_publishable_key="sb_publishable_local_synthetic_marker", local_postgres_config=directory/"config.json",
              local_supabase_auth_url="https://bskoyqhbgrycpwhydnnr.supabase.co",
              local_supabase_auth_publishable_key="sb_publishable_synthetic_public_marker", openai_enabled=True,
              deep_research_enabled=True, openai_api_key="sk-synthetic-test-not-real")
            gateway = Mock(); gateway.transport = httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError("no HTTP")))
            identity = Mock(); identity.health.return_value = {"status": "healthy"}
            with patch("services.assistant_api.local_postgres.LocalPostgresGateway", return_value=gateway), \
                 patch("services.assistant_api.cold_start_research.ColdStartResearch", side_effect=AssertionError("must not construct")):
                app = create_app(settings=config, auth_provider=identity)
            self.addCleanup(app.state.preview_runner.close)
            self.assertTrue(app.state.preview_runner.require_authorized_sales)
            self.assertIsNone(app.state.forecast_runner)
            answer = self.endpoint(app, "/api/ready")()
            import json
            ready = json.loads(answer.body)
            self.assertFalse(ready["deep_research_enabled"])
            self.assertEqual(ready["deep_research_block_reason"], "LOCAL_PILOT_RESEARCH_DISABLED")

    def test_local_assistant_workflow_cannot_start_poll_or_read_research(self):
        from services.assistant_api.rs_assistant import RSAssistantWorkflow, RSAssistantRequest
        with TemporaryDirectory() as tmp:
            storage = LocalPersistenceProvider(Path(tmp)/"state.sqlite")
            research = Mock()
            workflow = RSAssistantWorkflow(storage, research, research_disabled=True)
            body = {"chain_id": A, "purchase_type": "RS", "format": "BD", "model": "Synthetic", "period": "2024-07"}
            scoped = {"scope": {**body, "source_hash": "a"*64}, "records": [], "metrics": {}, "quality": {}}
            for action in ("answer", "research", "research_status"):
                answer = workflow.answer(RSAssistantRequest(**body, action=action, message="Investiga el mercado"), scoped,
                                         may_start_research=True)
                self.assertEqual(answer["research"], {"status": "DISABLED", "reason": "LOCAL_PILOT_RESEARCH_DISABLED"})
            self.assertFalse(research.mock_calls)

    def test_local_assistant_router_blocks_research_even_with_future_flags_enabled(self):
        from services.assistant_api.rs_assistant import RSAssistantRequest, mount_rs_assistant
        app = FastAPI(); identity, data, limiter, workflow = Mock(), Mock(), Mock(), Mock()
        identity.authenticate.return_value = Principal(USER, "manager", ROLE_PERMISSIONS["manager"], "synthetic", frozenset({A}), frozenset({A}))
        limiter.allow.return_value = True
        flags = SimpleNamespace(ai_assistant_api_enabled=True, deep_research_enabled=True, local_postgres_config=Path("synthetic-local-config"))
        mount_rs_assistant(app, flags, identity, data, limiter, workflow)
        endpoint = self.endpoint(app, "/api/forecast/rs-assistant")
        body = {"chain_id": A, "purchase_type": "RS", "format": "BD", "model": "Synthetic", "period": "2024-07"}
        request = SimpleNamespace(state=SimpleNamespace())
        with patch("services.assistant_api.rs_assistant.load_rs_scope") as load:
            for action in ("research", "research_status"):
                with self.assertRaisesRegex(PreviewError, "DEEP_RESEARCH_DISABLED"):
                    endpoint(RSAssistantRequest(**body, action=action), request, "Bearer synthetic-fixture")
            load.assert_not_called(); workflow.answer.assert_not_called()
            question = RSAssistantRequest(**body, message="Investiga el mercado")
            endpoint(question, request, "Bearer synthetic-fixture")
            workflow.answer.assert_called_once_with(question, load.return_value, may_start_research=False)


if __name__ == "__main__":
    unittest.main()
