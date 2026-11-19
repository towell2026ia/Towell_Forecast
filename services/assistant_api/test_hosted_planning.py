"""Synthetic JWT-scoped G6 transport; no local adapter or forecast execution."""

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI, HTTPException

from services.assistant_api import test_local_champion as base
from services.assistant_api.test_commercial_close_context import context, run
from services.assistant_api.hosted_planning import HostedChampionService, hosted_run
from services.assistant_api.operational_preview import (
    OperationalMultiChainForecastRunner,
)
from services.assistant_api.persistence import LocalPersistenceProvider, content_hash
from services.assistant_api.settings import Settings
from services.assistant_api.supabase_access import PreviewError, SupabaseReadClient
from services.assistant_api.rs_assistant import mount_rs_assistant, RSAssistantRequest


def payload():
    result = run()
    result["content_digest"] = content_hash(result)
    return {**result, "file_sha256": "e" * 64, "transport": "SUPABASE_SEALED_G6"}


class HostedPlanningTests(unittest.TestCase):
    def test_platform_commit_takes_precedence_over_an_old_manual_variable(self):
        with patch.dict(
            os.environ,
            {"RAILWAY_GIT_COMMIT_SHA": "a" * 40, "GIT_SHA": "b" * 40},
            clear=True,
        ):
            self.assertEqual(Settings.from_env().git_sha, "a" * 40)

    def test_jwt_reader_returns_only_explicit_products_without_full_file_or_engine(
        self,
    ):
        saved = payload()
        returned = deepcopy(saved)
        returned["products"] = returned["products"][:1]
        client = Mock()
        client.read_rpc.return_value = returned
        with patch(
            "services.assistant_api.unified_plan_reader.pinned_run",
            side_effect=AssertionError("local file forbidden"),
        ):
            result = hosted_run(
                client, context(), product_ids=[base.PRODUCT], chain_ids=[base.CHAIN]
            )
        self.assertEqual(result["content_digest"], saved["content_digest"])
        self.assertEqual([p["product_id"] for p in result["products"]], [base.PRODUCT])
        params = client.read_rpc.call_args.args[1]
        self.assertEqual(params["p_product_ids"], [base.PRODUCT])
        self.assertEqual(params["p_chain_ids"], [base.CHAIN])

    def test_legacy_wrong_source_extra_product_and_bad_curve_fail_closed(self):
        for change in ("context", "extra", "calendar", "transport", "emission"):
            bad = payload()
            if change == "context":
                bad["source_context"] = base.context()
            if change == "extra":
                pass  # The request below allows just one.
            else:
                bad["products"] = bad["products"][:1]
            if change == "calendar":
                bad["products"][0]["candidates"]["STATISTICAL"]["horizons"][0][
                    "forecast_distance"
                ] = 4
            if change == "transport":
                bad["transport"] = "LOCAL_GATEWAY"
            if change == "emission":
                bad["emitted_at"] = "2026-09-01T00:00:00Z"
            with self.subTest(change=change), self.assertRaises(PreviewError):
                hosted_run(
                    SimpleNamespace(read_rpc=lambda *_: bad),
                    context(),
                    product_ids=[base.PRODUCT],
                    chain_ids=[base.CHAIN],
                )

    def test_bounded_preview_metadata_reads_and_reloaded_jobs_share_sealed_run(self):
        saved = payload()

        def rpc(path, params):
            self.assertEqual(path, "operational_read_planning_run")
            rows = [] if params["p_product_ids"] == [] else saved["products"]
            return {**deepcopy(saved), "products": rows}

        client = SimpleNamespace(read_rpc=rpc, catalog_rows=lambda: base.catalogue())
        provider = SimpleNamespace(
            client=client,
            source_context=context,
            refresh_source_context=context,
            records=Mock(side_effect=AssertionError("training forbidden")),
        )
        with TemporaryDirectory() as temp:
            storage = LocalPersistenceProvider(Path(temp) / "state.sqlite")
            compute = Mock(side_effect=AssertionError("engine forbidden"))
            runner = OperationalMultiChainForecastRunner(
                storage,
                hosted_planning=True,
                require_authorized_sales=True,
                compute=compute,
            )
            self.addCleanup(runner.close)
            job = runner.submit(
                provider,
                base.actor(),
                chain_ids=[base.CHAIN],
                product_ids=[base.PRODUCT, base.SECOND],
                issue_period="2026-12",
                objective="Pedido",
            )
            rendered = runner.result(
                job["job_id"],
                base.actor(),
                source_context=context(),
                planning_provider=provider,
            )
            self.assertEqual(rendered["scopes"][0]["latest_actual_period"], "2026-09")
            self.assertEqual(
                rendered["scopes"][0]["products"][0]["horizons"][0]["target_period"],
                "2027-01",
            )
            self.assertEqual(
                runner.get_job(
                    job["job_id"],
                    base.actor(),
                    source_context=context(),
                    planning_provider=provider,
                )["job_id"],
                job["job_id"],
            )
            with self.assertRaisesRegex(PreviewError, "PLANNING_RUN_UNAVAILABLE"):
                runner.get_job(job["job_id"], base.actor(), source_context=context())
            compute.assert_not_called()
            provider.records.assert_not_called()

    def test_explicit_settings_isolate_hosted_and_local_contracts(self):
        settings = Settings(
            hosted_unified_planning_enabled=True,
            data_provider="supabase",
            supabase_enabled=True,
            operational_preview_enabled=True,
            supabase_url="https://bskoyqhbgrycpwhydnnr.supabase.co",
            supabase_publishable_key="sb_publishable_synthetic_test_only",
            forecast_calculation_history_enabled=True,
        )
        self.assertTrue(settings.validate().hosted_unified_planning_enabled)
        for change in (
            {"local_postgres_config": Path(__file__).resolve()},
            {"data_provider": "normalized"},
            {"openai_enabled": True, "deep_research_enabled": True},
            {"forecast_selection_enabled": True},
            {"champion_publication_enabled": True, "vintage_persistence_enabled": True},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(settings, **change).validate()


class HostedChampionTransportTests(unittest.TestCase):
    def test_assistant_keeps_stored_answers_but_never_runs_forecast_or_research(self):
        app = FastAPI()
        identity, client, limiter, workflow = Mock(), Mock(), Mock(), Mock()
        identity.authenticate.return_value = base.actor()
        mount_rs_assistant(
            app,
            SimpleNamespace(
                ai_assistant_api_enabled=True, hosted_unified_planning_enabled=True
            ),
            identity,
            client,
            limiter,
            workflow,
        )
        endpoint = next(
            r.endpoint for r in app.routes if r.path == "/api/forecast/rs-assistant"
        )
        request = SimpleNamespace(state=SimpleNamespace())
        body = dict(
            chain_id=base.CHAIN,
            purchase_type="RS",
            format="SYNTHETIC",
            model="SYNTHETIC",
            period="2026-12",
        )
        with patch("services.assistant_api.rs_assistant.load_rs_scope") as load:
            for action, code in (
                ("forecast", "SEALED_PLANNING_ONLY"),
                ("research", "DEEP_RESEARCH_DISABLED"),
                ("research_status", "DEEP_RESEARCH_DISABLED"),
            ):
                with (
                    self.subTest(action=action),
                    self.assertRaisesRegex(PreviewError, code),
                ):
                    endpoint(
                        RSAssistantRequest(**body, action=action),
                        request,
                        "Bearer synthetic",
                    )
            load.assert_not_called()
            question = RSAssistantRequest(
                **body, message="Consultar resultado guardado", action="answer"
            )
            endpoint(question, request, "Bearer synthetic")
            workflow.answer.assert_called_once_with(
                question, load.return_value, may_start_research=False
            )

    def test_hosted_api_rejects_all_legacy_forecast_jobs_before_storage_or_compute(
        self,
    ):
        from services.assistant_api.api import (
            create_app,
            ForecastRunRequest,
            HistoricalRunRequest,
        )
        from services.assistant_api.data_provider import NormalizedDataProvider

        with TemporaryDirectory() as temp:
            config = Settings(
                hosted_unified_planning_enabled=True,
                data_provider="supabase",
                supabase_enabled=True,
                operational_preview_enabled=True,
                supabase_url="https://bskoyqhbgrycpwhydnnr.supabase.co",
                supabase_publishable_key="sb_publishable_synthetic_test_only",
                state_dir=Path(temp),
            )
            app = create_app(provider=NormalizedDataProvider(), settings=config)
            request = SimpleNamespace(state=SimpleNamespace(request_id="synthetic"))
            endpoints = {r.path: r.endpoint for r in app.routes if hasattr(r, "path")}
            calls = [
                (
                    "/api/forecast/run",
                    dict(
                        body=ForecastRunRequest(period="2027-01"),
                        request=request,
                        principal=base.actor(),
                    ),
                ),
                (
                    "/api/historical/run",
                    dict(
                        body=HistoricalRunRequest(
                            start_period="2024-01", end_period="2026-09"
                        ),
                        request=request,
                        principal=base.actor(),
                    ),
                ),
                (
                    "/api/historical/first-vintage",
                    dict(request=request, principal=base.actor()),
                ),
                (
                    "/api/forecast/jobs/{job_id}",
                    dict(job_id="old", principal=base.actor()),
                ),
                (
                    "/api/historical/jobs/{job_id}",
                    dict(job_id="old", principal=base.actor()),
                ),
            ]
            for path, kwargs in calls:
                with self.subTest(path=path), self.assertRaises(HTTPException) as error:
                    endpoints[path](**kwargs)
                self.assertEqual(
                    (error.exception.status_code, error.exception.detail),
                    (403, "SEALED_PLANNING_ONLY"),
                )
            self.assertEqual(app.state.persistence.list("forecast_jobs"), [])
            self.assertEqual(app.state.persistence.list("historical_runs"), [])
            app.state.preview_runner.close()

    def test_user_bearer_views_and_only_explicit_champion_mutation(self):
        requests = []

        def transport(request):
            requests.append(request)
            self.assertEqual(
                request.headers["authorization"], "Bearer user-jwt-synthetic"
            )
            return httpx.Response(
                200, json=[] if request.method == "GET" else {"id": base.RUN}
            )

        client = SupabaseReadClient(
            "https://example.supabase.co",
            "sb_publishable_synthetic_test_only",
            "user-jwt-synthetic",
            transport=httpx.MockTransport(transport),
            hosted_g6=True,
        )
        client.rows("products", {"select": "id"})
        client.rows("profiles", {"select": "id"})
        self.assertEqual(
            [r.url.path for r in requests],
            ["/rest/v1/ft_g6_products", "/rest/v1/profiles"],
        )
        client.champion_rpc({"p_confirm": True})
        self.assertEqual(
            requests[-1].url.path, "/rest/v1/rpc/operational_save_champion"
        )
        self.assertNotIn("p_actor_id", json.loads(requests[-1].content))
        with self.assertRaises(PreviewError):
            SupabaseReadClient(
                "https://example.supabase.co", "public", "token"
            ).champion_rpc({})

    def test_manager_scope_server_amounts_and_source_required_before_rpc(self):
        service = HostedChampionService()
        request = {
            **base.request(),
            "run_id": payload()["run_id"],
            "source_context": context(),
        }
        client = Mock()
        client.champion_rpc.return_value = {
            "id": base.RUN,
            "source_context": context(),
            "planning_year": 2027,
            "status": "ACTIVE",
        }
        for principal in (
            base.actor("reader"),
            base.actor("editor"),
            base.actor(scope=False),
        ):
            with (
                self.subTest(principal=principal.role),
                self.assertRaises(PreviewError),
            ):
                service.publish(
                    request,
                    principal,
                    context(),
                    base.catalogue(),
                    idempotency_key="synthetic-choice",
                    client=client,
                )
        client.champion_rpc.assert_not_called()
        result = service.publish(
            request,
            base.actor(),
            context(),
            base.catalogue(),
            idempotency_key="synthetic-choice",
            client=client,
        )
        self.assertEqual(result["id"], base.RUN)
        sent = client.champion_rpc.call_args.args[0]
        self.assertNotIn("quantities", sent)
        self.assertNotIn("p_actor_id", sent)
        self.assertEqual(sent["p_run_id"], request["run_id"])
        self.assertEqual(sent["p_product_ids"], [base.PRODUCT, base.SECOND])

    def test_hosted_flag_never_accepts_remote_legacy_source(self):
        client = SupabaseReadClient(
            "https://example.supabase.co",
            "sb_publishable_synthetic_test_only",
            "token",
            hosted_g6=True,
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=[base.context()])
            ),
        )
        with self.assertRaisesRegex(PreviewError, "OFFICIAL_SOURCE_MISMATCH"):
            client.operational_context()


if __name__ == "__main__":
    unittest.main()
