"""Synthetic ORDER lineage, independent closure and exact bounded previews."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from fastapi import FastAPI
import httpx

from services.assistant_api.auth import Principal, ROLE_PERMISSIONS
from services.assistant_api.data_provider import SupabaseDataProvider
from services.assistant_api.local_sales_policy import LocalSalesPolicy, MASTER_NAME
from services.assistant_api.operational_preview import OperationalMultiChainForecastRunner
from services.assistant_api.persistence import LocalPersistenceProvider
from services.assistant_api.preview_api import PreviewRunRequest, mount_preview_routes
from services.assistant_api.rs_assistant import RSAssistantRequest, RSAssistantWorkflow
from services.assistant_api.rs_assistant_forecasts import stored_rs_forecast
from services.assistant_api.supabase_access import PreviewError, SupabaseReadClient, matches_source, require_sales_context
from services.assistant_api import test_authorized_sales_paths as sales_tests
from services.assistant_api.test_authorized_sales_paths import source_context
from services.assistant_api.test_operational_preview import A, B, PRODUCT, OTHER, USER, fixture


def order_context(*, closed=False):
    return {"source_hash": "c" * 64, "generation": 3, "first_period": "2023-01", "last_period": "2024-07",
        "forecast_contract": "pedido-towell-v1", "objective": "Pedido", "target_metric": "ORDER",
        "target_sheet": "Pedidos", "target_file_sha256": "b" * 64, "authorized_source_hash": "c" * 64,
        "training_cutoff": "2024-07" if closed else None, "forecast_available": closed,
        "target_source_available": True, "forecast_block_code": None if closed else "ORDER_CLOSURE_UNVERIFIED",
        "forecast_unavailable_reason": None if closed else "Sin cálculo: cierre de Pedido no acreditado",
        "inventory_source": "ESTADISTICA", "unit": "PIEZAS"}


class OrderProvider:
    def __init__(self, closed=False):
        self.context = order_context(closed=closed)
        self.calls = []
        self.poison = 0

    def source_context(self):
        return copy.deepcopy(self.context)

    refresh_source_context = source_context

    def records(self, *, chain_id, mode, objective, issue_period=None):
        self.calls.append((chain_id, objective, issue_period))
        assert objective == "Pedido"
        rows = [{**row, "chain_id": chain_id, "objective": "Pedido"}
                for row in fixture(19) if not issue_period or row["period"] <= issue_period]
        extra = "00000000-0000-4000-8000-000000000099"
        return [*rows, *[{**row, "product_id": extra, "source_id": "extra-"+row["period"],
                         "value": self.poison + 1_000_000} for row in rows if row["product_id"] == PRODUCT]]


class OrderPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = LocalPersistenceProvider(Path(self.temp.name) / "state.sqlite")
        self.actor = Principal(USER, "manager", ROLE_PERMISSIONS["manager"], "synthetic",
                               frozenset({A, B}), frozenset({A, B}))

    def runner(self, **kwargs):
        runner = OperationalMultiChainForecastRunner(self.storage, require_authorized_sales=True, **kwargs)
        self.addCleanup(runner.close)
        return runner

    def test_observed_month_does_not_accredit_order_closure(self):
        context = order_context()
        self.assertIs(require_sales_context(context, required=True, for_training=False), context)
        with self.assertRaisesRegex(PreviewError, "ORDER_CLOSURE_UNVERIFIED"):
            require_sales_context(context, required=True)
        self.assertTrue(matches_source(context, copy.deepcopy(context)))
        self.assertFalse(matches_source(context, source_context()))
        for bad in ({"training_cutoff": "2024-07"}, {"forecast_available": True},
                    {"target_metric": "SALES"}, {"target_sheet": "Towell"}):
            altered = {**context, **bad}
            if bad == {"training_cutoff": "2024-07"}:
                # Even a forged date is not training authorization.
                with self.assertRaisesRegex(PreviewError, "ORDER_CLOSURE_UNVERIFIED"):
                    require_sales_context(altered)
            else:
                with self.assertRaisesRegex(PreviewError, "OFFICIAL_SOURCE_MISMATCH"):
                    require_sales_context(altered, for_training=False)

    def test_unknown_close_blocks_submit_and_every_cache_entry_before_any_io(self):
        provider = OrderProvider()
        runner = self.runner(compute=Mock(side_effect=AssertionError("no compute")))
        with self.assertRaisesRegex(PreviewError, "ORDER_CLOSURE_UNVERIFIED"):
            runner.submit(provider, self.actor, chain_ids=[A], product_ids=[PRODUCT], objective="Pedido")
        self.assertFalse(provider.calls)
        self.assertFalse(self.storage.list("forecast_jobs"))
        for read in (lambda: runner.get_job("old", self.actor, source_context=provider.context),
                     lambda: runner.latest(self.actor, A, source_context=provider.context),
                     lambda: runner.provisional_forecasts(self.actor, source_context=provider.context)):
            with self.assertRaisesRegex(PreviewError, "ORDER_CLOSURE_UNVERIFIED"):
                read()
        runner.compute.assert_not_called()

    def test_policy_allows_history_and_stock_without_certifying_training(self):
        master = Path(self.temp.name) / "datos_privados" / MASTER_NAME
        master.parent.mkdir()
        master.write_bytes(b"synthetic-original")
        policy = LocalSalesPolicy({"contract": "pedido-towell-v1", "master_path": str(master),
            "master_sha256": hashlib.sha256(master.read_bytes()).hexdigest(), "source_hash": "c"*64,
            "generation": 3, "sheet": "Pedidos", "unit": "PIEZAS", "last_closed_period": None})
        base = {key: order_context()[key] for key in ("source_hash", "generation", "first_period", "last_period")}
        context = policy.require("rpc/operational_read_observations", base)
        self.assertTrue(context["target_source_available"])
        self.assertIsNone(context["training_cutoff"])
        self.assertFalse(context["forecast_available"])
        self.assertEqual(context["forecast_unavailable_reason"], "Sin cálculo: cierre de Pedido no acreditado")
        self.assertFalse(policy.require("rpc/operational_read_inventory", base)["forecast_available"])
        master.unlink()
        with self.assertRaisesRegex(PreviewError, "ORDER_SOURCE_UNAVAILABLE"):
            policy.require("rpc/operational_read_observations", base)
        self.assertFalse(policy.require("rpc/operational_read_inventory", base)["target_source_available"])

    def test_client_history_read_is_distinct_from_provider_training(self):
        requests = []
        def transport(request):
            requests.append(request.url.path)
            return httpx.Response(200, json=[order_context()] if request.url.path.endswith("operational_read_context") else [])
        client = SupabaseReadClient("http://127.0.0.1:8000", "synthetic", "synthetic",
                                    transport=httpx.MockTransport(transport))
        self.assertEqual(client.observation_rows({"metric_code": "eq.ORDER"}), [])
        with self.assertRaisesRegex(PreviewError, "ORDER_CLOSURE_UNVERIFIED"):
            SupabaseDataProvider(client).records(chain_id=A, objective="Pedido")
        self.assertEqual(requests.count("/rest/v1/rpc/operational_read_observations"), 1)

    def test_closed_provider_requests_order_literal_without_sales_or_inventory_fallback(self):
        calls, poison = [], []
        def transport(request):
            name = request.url.path.rsplit("/", 1)[-1]
            calls.append(name)
            if name == "operational_read_context":
                rows = [order_context(closed=True)]
            elif name == "products":
                rows = [{"id": PRODUCT, "chain_id": A, "product_code": "Synthetic",
                         "description": "Synthetic only", "category_id": None}]
            elif name == "categories":
                rows = []
            elif name == "operational_read_catalog":
                rows = [{"chain_id": A, "product_id": PRODUCT, "purchase_type": "RS",
                         "product_type": "TOALLA", "color": "AZUL"}]
            elif name == "operational_read_observations":
                self.assertEqual(request.url.params["metric_code"], "eq.ORDER")
                rows = [{"chain_id": A, "product_id": PRODUCT, "period": "2024-07-01", "metric_code": "ORDER",
                         "value": 10, "value_text": "10.000000000000001", "version_no": 1,
                         "availability_source": "UNKNOWN", "available_at": None, "source_reference": "Pedidos!M9",
                         "id": OTHER}, *poison]
            else:
                raise AssertionError("no inventory, retired history or unrelated metric route")
            return httpx.Response(200, json=rows)
        client = SupabaseReadClient("http://127.0.0.1:8000", "synthetic", "synthetic",
                                    transport=httpx.MockTransport(transport))
        provider = SupabaseDataProvider(client)
        row, = provider.records(chain_id=A, objective="Pedido")
        self.assertEqual((row["objective"], row["target_metric"], row["value"], row["source_reference"]),
                         ("Pedido", "ORDER", "10.000000000000001", "Pedidos!M9"))
        poison.append({"chain_id": A, "product_id": PRODUCT, "period": "2024-07-01", "metric_code": "SALES"})
        with self.assertRaisesRegex(PreviewError, "DATA_READ_FAILED"):
            provider.records(chain_id=A, objective="Pedido")
        before = len(calls)
        with self.assertRaisesRegex(PreviewError, "FORECAST_OBJECTIVE_MISMATCH"):
            provider.records(chain_id=A, objective="Venta")
        self.assertEqual(len(calls), before)

    def test_bounded_api_rejects_unclosed_context_before_runner_or_catalogue(self):
        source = SimpleNamespace(client=SimpleNamespace(operational_context=lambda: order_context(), catalog_rows=Mock()))
        data = SimpleNamespace(bind=lambda _: source)
        identity = SimpleNamespace(authenticate=lambda **_: self.actor)
        flags = SimpleNamespace(operational_preview_enabled=True, local_postgres_config=Path("synthetic"), forecast_rate_per_minute=5)
        runner, limiter = Mock(), Mock()
        app = FastAPI()
        mount_preview_routes(app, flags, identity, data, runner, limiter)
        endpoint = sales_tests.LocalRouteFencesTests().endpoint(app, "/api/forecast/preview-runs")
        body = PreviewRunRequest(chain_ids=[A], product_ids=[PRODUCT], objective="Pedido", source_context=order_context())
        with self.assertRaisesRegex(PreviewError, "ORDER_CLOSURE_UNVERIFIED"):
            endpoint(body, SimpleNamespace(state=SimpleNamespace()), "Bearer synthetic")
        source.client.catalog_rows.assert_not_called()
        runner.submit.assert_not_called()

    def test_closed_fixture_bounded_scope_requires_every_exact_chain_and_product(self):
        context = order_context(closed=True)
        catalog = [{"chain_id": A, "product_id": PRODUCT, "purchase_type": "RS"},
                   {"chain_id": B, "product_id": OTHER, "purchase_type": "RS"}]
        client = SimpleNamespace(operational_context=lambda: context, catalog_rows=lambda **_: catalog)
        source = SimpleNamespace(client=client)
        flags = SimpleNamespace(operational_preview_enabled=True, local_postgres_config=Path("synthetic"), forecast_rate_per_minute=5)
        runner = Mock()
        limiter = SimpleNamespace(allow=lambda *_: True)
        app = FastAPI()
        mount_preview_routes(app, flags, SimpleNamespace(authenticate=lambda **_: self.actor),
            SimpleNamespace(bind=lambda _: source), runner, limiter)
        endpoint = sales_tests.LocalRouteFencesTests().endpoint(app, "/api/forecast/preview-runs")
        args = {"chain_ids": [A, B], "product_ids": [PRODUCT, OTHER], "objective": "Pedido", "source_context": context}
        request = SimpleNamespace(state=SimpleNamespace())
        endpoint(PreviewRunRequest(**args), request, "Bearer synthetic")
        self.assertEqual(runner.submit.call_args.kwargs["chain_ids"], [A, B])
        self.assertEqual(runner.submit.call_args.kwargs["product_ids"], [PRODUCT, OTHER])
        self.assertFalse(runner.submit.call_args.kwargs["all_scopes"])
        for change in ({"chain_ids": [A]}, {"product_ids": [PRODUCT, PRODUCT]},
                       {"source_context": source_context()}, {"objective": "Venta"}):
            with self.assertRaises(PreviewError):
                endpoint(PreviewRunRequest(**{**args, **change}), request, "Bearer synthetic")
        self.assertEqual(runner.submit.call_count, 1)

    def test_assistant_cannot_read_order_cache_or_compute_without_a_close(self):
        compute = Mock()
        workflow = RSAssistantWorkflow(self.storage, compute=compute, research_disabled=True)
        scoped = {"scope": {"chain_id": A, "format": "BD", "model": "Synthetic", "period": "2024-07"},
                  "source_context": order_context(), "records": [], "metrics": {}, "quality": {}, "stored_forecast": None}
        body = RSAssistantRequest(chain_id=A, purchase_type="RS", format="BD", model="Synthetic", period="2024-07", action="forecast")
        with self.assertRaisesRegex(PreviewError, "ORDER_CLOSURE_UNVERIFIED"):
            workflow.answer(body, scoped)
        compute.assert_not_called()
        client = Mock()
        self.assertIsNone(stored_rs_forecast(client, order_context(), chain_id=A, product_ids=[PRODUCT], period="2024-07"))
        self.assertFalse(client.mock_calls)
        storage = Mock()
        storage.get.side_effect = AssertionError("no cache read with an unverified close")
        observer = RSAssistantWorkflow(storage, compute=compute, research_disabled=True)
        response = observer.answer(body.model_copy(update={"action": "answer", "message": "Explica el forecast"}),
                                   {key: value for key, value in scoped.items() if key != "stored_forecast"})
        self.assertNotIn("forecast", response)
        self.assertIn("Sin cálculo: cierre de Pedido no acreditado", response["message"])
        self.assertFalse(storage.mock_calls)

    def test_order_cannot_retain_a_sales_champion_or_a_retired_order_source(self):
        runner = self.runner(environment="synthetic")
        base = {"chain_id": A, "objective": "Pedido", "scope": "chain", "environment": "synthetic",
                "dataset_hash": "d"*64, "version": "synthetic-old", "strategy": "statistical"}
        for changes in ({"objective": "Venta", "source_context": order_context(closed=True)},
                        {"source_context": source_context()}):
            record = {**base, **changes}
            self.storage.put("champion_registry", f"{A}|Pedido|chain", record)
            self.assertIsNone(runner._compatible_champion(A, "d"*64, order_context(closed=True), "Pedido"))
            self.assertEqual(self.storage.get("champion_registry", f"{A}|Pedido|chain"), record)

    def test_two_independent_real_order_fits_ignore_unselected_and_retired_quantities(self):
        results = []
        for poison in (0, 999_999_999):
            with TemporaryDirectory() as tmp:
                store = LocalPersistenceProvider(Path(tmp) / "state.sqlite")
                provider = OrderProvider(closed=True)
                provider.poison = poison
                runner = OperationalMultiChainForecastRunner(store, require_authorized_sales=True)
                try:
                    job = runner.submit(provider, self.actor, chain_ids=[A], product_ids=[PRODUCT, OTHER], objective="Pedido")
                    runner.close()
                    result = runner.result(job["job_id"], self.actor, source_context=provider.context)
                    self.assertEqual(result["objective"], "Pedido")
                    self.assertEqual(result["status"], "READY_PREVIEW", result)
                    scope, = result["scopes"]
                    self.assertEqual({p["product_id"] for p in scope["products"]}, {PRODUCT, OTHER})
                    self.assertTrue(all(len(p["horizons"]) == 12 for p in scope["products"]))
                    self.assertEqual({row["family"] for row in scope["selection"]["product_candidates"]},
                                     {"statistical", "ml", "ensemble"})
                    self.assertTrue(all(call[1] == "Pedido" for call in provider.calls))
                    results.append({key: scope[key] for key in ("dataset_hash", "objective", "products", "statistical", "ml", "selection")})
                finally:
                    runner.close()
        self.assertEqual(results[0], results[1])


if __name__ == "__main__":
    unittest.main()
