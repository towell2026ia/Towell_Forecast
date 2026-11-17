"""Synthetic provenance failures must not recover replaced sales or curves."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from services.assistant_api.data_provider import SupabaseDataProvider
from services.assistant_api.local_sales_policy import LocalSalesPolicy, MASTER_NAME, SALES_UNAVAILABLE
from services.assistant_api.supabase_access import PreviewError, SupabaseReadClient
from services.assistant_api import test_local_hosted_gateway as gateway_fixtures
from services.assistant_api.test_local_hosted_gateway import (
    Cursor, FakeConnection, SESSION, USER, sql_module,
)


class LocalSalesPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.master = Path(self.temp.name) / "datos_privados" / MASTER_NAME
        self.master.parent.mkdir()
        self.master.write_bytes(b"synthetic-authorized-workbook")
        self.config = {"contract": "towell-sales-v1", "master_path": str(self.master),
                       "master_sha256": hashlib.sha256(self.master.read_bytes()).hexdigest(),
                       "source_hash": "a" * 64, "generation": 2, "sheet": "Towell",
                       "unit": "PIEZAS", "last_closed_period": "2026-10"}
        self.context = {"source_hash": "a" * 64, "generation": 2,
                        "first_period": "2024-01", "last_period": "2026-10"}
        self.policy = LocalSalesPolicy(self.config)

    def test_no_default_or_other_sheet_or_copied_master_is_accepted(self):
        for change in [None, {}, {**self.config, "sheet": "Cliente"},
                       {**self.config, "master_path": str(self.master.with_name("Copia " + MASTER_NAME))},
                       {**self.config, "source_hash": "unknown"}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                LocalSalesPolicy(change)

    def test_missing_master_does_not_use_identical_copy_inventory_or_old_source(self):
        self.master.with_name("Copia " + MASTER_NAME).write_bytes(self.master.read_bytes())
        self.master.unlink()
        for policy in (self.policy, LocalSalesPolicy(self.config)):
            self.assertFalse(policy.available())
            result = policy.require("rpc/operational_read_inventory", self.context)
            self.assertFalse(result["sales_available"])
            self.assertEqual(result["sales_unavailable_reason"], SALES_UNAVAILABLE)
            for resource in ("rpc/operational_read_observations", "forecast_calculations"):
                with self.subTest(resource=resource), self.assertRaises(PreviewError) as caught:
                    policy.require(resource, self.context)
                self.assertEqual(caught.exception.code, "SALES_SOURCE_UNAVAILABLE")

    def test_replaced_source_is_rejected_across_restarts_and_generations(self):
        for policy in (self.policy, LocalSalesPolicy(self.config)):
            for changes in ({"source_hash": "b" * 64}, {"generation": 1}, {"last_period": "2026-07"}):
                with self.subTest(changes=changes), self.assertRaises(PreviewError) as caught:
                    policy.require("forecast_calculations", {**self.context, **changes})
                self.assertEqual(caught.exception.code, "OFFICIAL_SOURCE_MISMATCH")

    def test_file_change_invalidates_sales_without_changing_original_policy(self):
        self.assertTrue(self.policy.available())
        self.master.write_bytes(b"changed synthetic values")
        self.assertFalse(self.policy.available())
        self.assertFalse(LocalSalesPolicy(self.config).available())

    def test_legacy_readers_cannot_become_operational_fallbacks(self):
        for resource in ("portal_monthly_observations_current", "portal_history_periods", "forecast_runs", "forecast_horizons"):
            with self.subTest(resource=resource), self.assertRaises(PreviewError) as caught:
                self.policy.require(resource, self.context)
            self.assertEqual(caught.exception.code, "REPLACED_SOURCE_AUDIT_ONLY")

    def test_backend_context_preserves_provenance_and_blocks_cached_forecast_routes(self):
        context = self.policy.context(self.context)
        context["sales_available"] = False
        client = SupabaseReadClient("http://127.0.0.1:8000", "synthetic", "synthetic-token",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[context])))
        self.assertEqual(client.operational_context()["sales_file_sha256"], self.config["master_sha256"])
        with self.assertRaises(PreviewError) as caught:
            SupabaseDataProvider(client).source_context()
        self.assertEqual(caught.exception.code, "SALES_SOURCE_UNAVAILABLE")
        with self.assertRaises(PreviewError):
            client.observation_rows({})

    def test_bad_provenance_is_not_cached_as_valid(self):
        context = {**self.policy.context(self.context), "sales_source": "OTHER"}
        client = SupabaseReadClient("http://127.0.0.1:8000", "synthetic", "synthetic-token",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[context])))
        for _ in range(2):
            with self.assertRaises(PreviewError) as caught:
                client.operational_context()
            self.assertEqual(caught.exception.code, "OFFICIAL_SOURCE_MISMATCH")

    def test_http_failure_keeps_specific_source_unavailability(self):
        client = SupabaseReadClient("http://127.0.0.1:8000", "synthetic", "synthetic-token",
            transport=httpx.MockTransport(lambda _: httpx.Response(503, json={"code": "SALES_SOURCE_UNAVAILABLE"})))
        with self.assertRaises(PreviewError) as caught:
            client.get("/rest/v1/forecast_calculations")
        self.assertEqual(caught.exception.code, "SALES_SOURCE_UNAVAILABLE")

    def test_replaced_source_rejection_is_distinct_from_missing_scope_permissions(self):
        client = SupabaseReadClient("http://127.0.0.1:8000", "synthetic", "synthetic-token",
            transport=httpx.MockTransport(lambda _: httpx.Response(403, json={"code": "REPLACED_SOURCE_AUDIT_ONLY"})))
        with self.assertRaises(PreviewError) as caught:
            client.get("/rest/v1/portal_monthly_observations_current")
        self.assertEqual((caught.exception.code, caught.exception.status), ("REPLACED_SOURCE_AUDIT_ONLY", 403))

    def test_inventory_month_does_not_certify_or_shift_sales_cutoff(self):
        policy = LocalSalesPolicy({**self.config, "last_closed_period": "2026-09"})
        actual = policy.context(self.context)
        self.assertEqual(actual["last_period"], "2026-10")
        self.assertEqual(actual["sales_training_cutoff"], "2026-09")
        client = SupabaseReadClient("http://127.0.0.1:8000", "synthetic", "synthetic-token",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[actual])))
        with self.assertRaises(PreviewError) as caught:
            SupabaseDataProvider(client).records(chain_id=USER, issue_period="2026-10")
        self.assertEqual(caught.exception.code, "SALES_PERIOD_NOT_CLOSED")

    def gateway(self, actual_context=None):
        helper = gateway_fixtures.LocalHostedGatewayTests()
        gateway = helper.make(rows=[])
        context = self.context if actual_context is None else actual_context

        class Connection(FakeConnection):
            def execute(self, query, params=None):
                if str(query) == "select * from public.operational_read_context()":
                    self.calls.append((str(query), params))
                    return Cursor([context])
                return super().execute(query, params)

        self.connection = Connection([{"id": USER, "status": "ACTIVE", "global_role": "VIEWER"}])
        gateway.connect.return_value = self.connection
        gateway.sales_policy = self.policy
        return helper, gateway

    def test_gateway_blocks_old_pointer_before_reading_any_curve(self):
        helper, gateway = self.gateway({**self.context, "source_hash": "b" * 64})
        with patch.dict("sys.modules", {"psycopg": sql_module()}):
            response = gateway.handle(helper.request(path="/rest/v1/forecast_calculations"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "OFFICIAL_SOURCE_MISMATCH")
        self.assertFalse(any('from "public"."forecast_calculations"' in query for query, _ in self.connection.calls))

    def test_gateway_filters_stored_curves_to_current_provenance_and_keeps_rls(self):
        for table in ("forecast_calculations", "forecast_calculation_horizons"):
            helper, gateway = self.gateway()
            with patch.dict("sys.modules", {"psycopg": sql_module()}):
                response = gateway.handle(helper.request(path="/rest/v1/" + table))
            self.assertEqual(response.status_code, 200)
            queries = [(query, values) for query, values in self.connection.calls if "source_hash'=%s" in query]
            self.assertEqual(len(queries), 2)  # count and paginated read
            self.assertTrue(all(values[:2] == ["a" * 64, "2"] for _, values in queries))
            self.assertIn(("set local role authenticated", None), self.connection.calls)
            self.assertIn(("select set_config('request.jwt.claim.sub',%s,true)", (SESSION["sub"],)), self.connection.calls)


if __name__ == "__main__":
    unittest.main()
