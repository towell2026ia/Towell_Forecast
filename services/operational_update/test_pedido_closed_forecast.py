"""Synthetic closed-Pedido orchestration with partial capture isolated from training."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from services.forecast_engine.engine import ForecastPolicy, forecast_dataset
from .official_forecast import engine_records, eligibility, run, source_inputs
from .pedidos_order_source import read_pedidos_order_source
from .pedido_source_runtime import prepare_packet
from .preparation import file_hash
from . import test_pedidos_source as fixture_module
from .test_official_forecast import source_row
from .towell_sales_source import read_towell_sales_source
from .towell_source_runtime import prepare_packet as prepare_sales
from .unification import write_csv, write_json


class ClosedPedidoForecastTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.PedidosSourceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.evidence = {"sheet": "Pedidos", "metric_code": "ORDER", "last_closed_period": "2026-09",
            "confirmed_by": "PROJECT_OWNER_USER", "reference": "SYNTHETIC ONLY",
            "statement": "Synthetic September closure; October partial", "document_sha256": "e" * 64,
            "confirmed_at": None, "available_at": None}

    def packet(self, **options):
        f = self.fixture
        sales = read_towell_sales_source(f.root)
        g2 = prepare_sales(sales, f.bindings, f.snapshot, [], fixture_module.STOCK, rules=f.rules)["import"]
        doc = read_pedidos_order_source(f.root, confirmed_closed_period="2026-09", closure_evidence=self.evidence)
        return prepare_packet(doc, f.bindings, f.snapshot, g2, rules=f.rules, **options)

    def test_closed_september_keeps_october_for_history_but_never_as_training_or_metadata(self):
        result = self.packet()
        self.assertEqual(result["summary"]["order_observations"], 2)
        self.assertEqual(result["summary"]["order_training_observations"], 1)
        self.assertEqual(result["summary"]["order_partial_observations"], 1)
        self.assertEqual(result["forecast_source_context"]["training_cutoff"], "2026-09")
        self.assertTrue(result["forecast_source_context"]["forecast_available"])
        self.assertIsNone(result["forecast_source_context"]["forecast_block_code"])
        facts = result["import"]["observations"]
        before, metadata = engine_records(facts, "2026-09", objective="Pedido", target_metric="ORDER")
        self.assertEqual([r["value"] for r in before], ["12.125"])
        self.assertEqual({r["objective"] for r in before}, {"Pedido"})
        changed = deepcopy(facts)
        for row in changed:
            if row["period"] > "2026-09" or row["metric_code"] != "ORDER":
                row.update(value="987654321", product_type="UNAUTHORIZED FUTURE METADATA")
        self.assertEqual(engine_records(changed, "2026-09", objective="Pedido", target_metric="ORDER"), (before, metadata))
        self.assertEqual({r["metric_code"] for r in facts}, {"ORDER", "SALES"})
        with self.assertRaisesRegex(ValueError, "target_metric_mismatch"):
            engine_records(facts, "2026-09", objective="Pedido", target_metric="SALES")

    def test_closed_packet_reaches_shared_order_engine_without_relabeling_or_partial_input(self):
        result = self.packet()
        with TemporaryDirectory() as temp:
            source, output = Path(temp) / "source", Path(temp) / "result"
            source.mkdir()
            packet, context = result["import"], result["forecast_source_context"]
            write_json(source / "Import.json", packet)
            write_csv(source / "Facts.csv", packet["observations"], list(packet["observations"][0]))
            policy = {"contract": "pedido-towell-v1", "master_path": str(self.fixture.path),
                "master_sha256": context["target_file_sha256"], "source_hash": packet["source_hash"],
                "generation": 4, "sheet": "Pedidos", "unit": "PIEZAS", "last_closed_period": "2026-09"}
            write_json(source / "Authorized_Sales_Policy.json", policy)
            write_json(source / "Closure_Evidence.json", self.evidence)
            manifest = {"schema": "official-source-1", "source_hash": packet["source_hash"], "generation": 4,
                "cutoff": "2026-09", "source_last_period": packet["last_period"], "forecast_source_context": context,
                "outputs_sha256": {p.name: file_hash(p) for p in source.iterdir()}}
            write_json(source / "Manifest.json", manifest)
            recorded = []
            def compute(rows, cutoff, **kwargs):
                recorded.append((rows, cutoff, kwargs))
                return {"chains": []}
            selected = self.fixture.bindings[0]["product_id"]
            state = {"chain_id": self.fixture.bindings[0]["chain_id"], "product_id": selected,
                "history_months": 1, "status": "ACTIVE", "eligible": True,
                "minimum_product_observations": 6, "reason": None}
            # Isolate the existing compute boundary from model eligibility for
            # this routing test; real default eligibility is exercised below.
            with patch("services.operational_update.official_forecast.eligibility", return_value=([state], {selected})):
                run(source, file_hash(source / "Manifest.json"), output, first_period="2024-01", context_generation=4, compute=compute)
            self.assertEqual(len(recorded), 1)
            self.assertEqual(recorded[0][1], "2026-09")
            self.assertEqual(recorded[0][2]["objective"], "Pedido")
            self.assertEqual(len(recorded[0][0]), 1)
            self.assertEqual(recorded[0][0][0]["value"], "12.125")
            self.assertEqual(recorded[0][0][0]["objective"], "Pedido")
            self.assertEqual(recorded[0][0][0]["period"], "2026-09")
            invalid = {**manifest, "cutoff": None}
            (source / "Manifest.json").write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "training_cutoff_unverified"):
                source_inputs(source, file_hash(source / "Manifest.json"), first_period="2024-01")

    def test_existing_engine_returns_pedido_twelve_month_contract_on_synthetic_order(self):
        facts = [{**source_row(f"{2024 + i // 12}-{i % 12 + 1:02d}", str(20 + i % 4)), "metric_code": "ORDER"}
                 for i in range(33)]
        facts += [{**source_row("2026-10", "999999999"), "metric_code": "ORDER"}, source_row("2026-09", "888888888")]
        records, _ = engine_records(facts, "2026-09", objective="Pedido", target_metric="ORDER")
        self.assertEqual(len(records), 33)
        states, selected = eligibility(records, "2026-09", ForecastPolicy(), objective="Pedido")
        self.assertEqual(selected, {"p1"})
        result = forecast_dataset(records, "2026-09", objective="Pedido", policy=ForecastPolicy(), evidence_mode="RETROSPECTIVE_TRAINING")
        self.assertEqual(result["objective"], "Pedido")
        candidates = result["chains"][0]["model_audit"]["product_candidates"]
        for candidate in candidates:
            self.assertEqual(len(candidate["horizons"]), 12)
            self.assertEqual(candidate["horizons"][0]["target_period"], "2026-10")
            self.assertEqual(candidate["horizons"][-1]["target_period"], "2027-09")
        self.assertTrue(states[0]["eligible"])

    def test_independent_fulfillment_pairs_change_provenance_but_never_order_training(self):
        stock_source = {**fixture_module.STOCK, "id": "44444444-4444-4444-8444-444444444444",
            "reference": "SYNTHETIC W/X PAIRS AND Y STOCK ONLY"}
        pair = {"chain_id": self.fixture.bindings[0]["chain_id"], "product_id": self.fixture.bindings[0]["product_id"],
            "period": "2026-09", "format": "A", "upc": "000001", "purchase_type": "RS",
            "source_id": stock_source["id"], "source_sha256": stock_source["source_hash"],
            "source_reference": "BAASE!W18:X18", "order_source_reference": "BAASE!W18",
            "delivery_source_reference": "BAASE!X18", "order_value": "200.25", "delivery_value": "180.125", "partial_period": False}
        original = self.packet(fulfillment_rows=[pair], fulfillment_source=stock_source)
        changed = self.packet(fulfillment_rows=[{**pair, "order_value": "999999", "delivery_value": None}], fulfillment_source=stock_source)
        self.assertNotEqual(original["import"]["source_hash"], changed["import"]["source_hash"])
        self.assertEqual({r["metric_code"] for r in original["import"]["observations"]}, {"ORDER", "SALES"})
        normalize = lambda rows: [{k: v for k, v in row.items() if k != "source_id"} for row in rows]
        before, _ = engine_records(original["import"]["observations"], "2026-09", objective="Pedido", target_metric="ORDER")
        after, _ = engine_records(changed["import"]["observations"], "2026-09", objective="Pedido", target_metric="ORDER")
        self.assertEqual(normalize(before), normalize(after))
        self.assertEqual(original["import"]["fulfillment"][0]["order_value"], "200.25")
        self.assertEqual(original["forecast_source_context"]["fulfillment_source"], "ESTADISTICA_BAASE")
        self.assertTrue(original["policy"]["fill_rate_never_training_target"])
        self.assertEqual(len(original["import"]["sources"]), 2)


if __name__ == "__main__":
    unittest.main()
