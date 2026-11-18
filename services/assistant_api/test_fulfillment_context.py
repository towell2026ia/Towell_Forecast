"""Fulfillment metadata pins one independent BAASE source, never ORDER input."""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx

from services.assistant_api.local_sales_policy import LocalSalesPolicy, MASTER_NAME
from services.assistant_api.supabase_access import PreviewError, SupabaseReadClient, matches_source, require_sales_context
from services.assistant_api.test_order_paths import order_context


class FulfillmentContextTests(unittest.TestCase):
    def context(self):
        return {**order_context(closed=True), "fulfillment_source": "ESTADISTICA_BAASE",
                "fulfillment_file_sha256": "f" * 64, "fulfillment_available": True}

    def test_read_client_preserves_accredited_context_separately_from_target_metric(self):
        client = SupabaseReadClient("http://127.0.0.1:8000", "synthetic", "synthetic",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[self.context()])))
        context = client.operational_context()
        self.assertEqual((context["target_metric"], context["target_sheet"]), ("ORDER", "Pedidos"))
        self.assertEqual(context["fulfillment_file_sha256"], "f" * 64)
        self.assertTrue(context["fulfillment_available"])
        self.assertTrue(matches_source(context, self.context()))

    def test_wrong_or_incomplete_compliance_source_is_not_a_compatible_pin(self):
        for changes in ({"fulfillment_source": "Pedidos"}, {"fulfillment_file_sha256": "bad"},
                        {"fulfillment_available": False}):
            invalid = {**self.context(), **changes}
            with self.subTest(changes=changes), self.assertRaisesRegex(PreviewError, "OFFICIAL_SOURCE_MISMATCH"):
                require_sales_context(invalid)
            self.assertFalse(matches_source(invalid, self.context()))
        self.assertFalse(matches_source({**self.context(), "fulfillment_file_sha256": "e" * 64}, self.context()))
        self.assertTrue(matches_source(order_context(), order_context()))

    def test_local_policy_reports_pair_source_without_substituting_its_training_sheet(self):
        with TemporaryDirectory() as temp:
            master = Path(temp) / "datos_privados" / MASTER_NAME
            master.parent.mkdir()
            master.write_bytes(b"synthetic")
            config = {"contract": "pedido-towell-v1", "master_path": str(master),
                "master_sha256": hashlib.sha256(master.read_bytes()).hexdigest(), "source_hash": "c" * 64,
                "generation": 3, "sheet": "Pedidos", "unit": "PIEZAS", "last_closed_period": None,
                "fulfillment_source": "ESTADISTICA_BAASE", "fulfillment_file_sha256": "f" * 64, "fulfillment_available": True}
            actual = {key: order_context()[key] for key in ("source_hash", "generation", "first_period", "last_period")}
            policy = LocalSalesPolicy(config)
            context = policy.require("rpc/operational_read_fulfillment", actual)
            self.assertEqual(context["target_sheet"], "Pedidos")
            self.assertTrue(context["fulfillment_available"])
            self.assertFalse(context["forecast_available"])
            self.assertIsNone(context["training_cutoff"])
            with self.assertRaisesRegex(PreviewError, "ORDER_CLOSURE_UNVERIFIED"):
                require_sales_context(context)
            for changes in ({"fulfillment_source": "UNKNOWN"}, {"fulfillment_file_sha256": ""}):
                with self.assertRaisesRegex(ValueError, "local_fulfillment_policy_invalid"):
                    LocalSalesPolicy({**config, **changes})


if __name__ == "__main__":
    unittest.main()
