"""Synthetic native SQL tests; no commercial workbook or productive endpoint."""
import copy
import os
import unittest

from . import test_official_source as official
from .fixtures import CHAINS, PRODUCTS, VIEW_A, actor, uid
from .local import REPO
from .official_source import validate_package


def package():
    payload = official.package("fulfillment")
    rows = []
    for index, (product, info) in enumerate(PRODUCTS.items()):
        rows.append({"id": uid("fulfillment-pair-" + product), "chain_id": info["chain"], "product_id": product,
            "period": "2026-10", "format": "SYNTHETIC", "upc": f"{index:07}", "purchase_type": "RS",
            "order_value": "100" if index else "0", "delivery_value": "0.0000000000000001" if index else None,
            "source_id": payload["sources"][1]["id"], "source_sha256": payload["sources"][1]["source_hash"],
            "source_reference": f"BAASE!W{index+18}:X{index+18}", "order_source_reference": f"BAASE!W{index+18}",
            "delivery_source_reference": f"BAASE!X{index+18}", "partial_period": True})
    payload["fulfillment"] = rows
    return payload


class FulfillmentPackageTests(unittest.TestCase):
    def test_pair_precision_missing_and_original_metric_keys_are_separate(self):
        payload = package()
        self.assertIs(validate_package(payload), payload)
        before = copy.deepcopy(payload["observations"])
        payload["fulfillment"][0]["order_value"] = "99999"
        self.assertEqual(payload["observations"], before)
        for value in ("0.00000000000000001", "NaN", "-1", 1.5):
            bad = package()
            bad["fulfillment"][0]["order_value"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_package(bad)

    def test_unaccredited_source_scope_and_cell_references_fail_closed(self):
        for changes in ({"source_sha256": "a" * 64}, {"purchase_type": "CE"},
                        {"source_reference": "Pedidos!M18"}, {"order_source_reference": "BAASE!V18"},
                        {"source_reference": "BAASE!W18:X19"}, {"delivery_source_reference": "BAASE!X19"},
                        {"partial_period": "true"}):
            bad = package()
            bad["fulfillment"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_package(bad)


@unittest.skipUnless(os.environ.get("GATEB_CONFIG"), "NATIVE_FULFILLMENT_REQUIRES_SYNTHETIC_GATEB_CONFIG")
class FulfillmentPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = official.OfficialSourcePostgres
        cls.fixture.setUpClass()
        cls.addClassCleanup(cls.fixture.doClassCleanups)
        cls.lab = cls.fixture.lab
        with cls.lab.connect() as c, c.transaction():
            cls.lab.maintenance(c, cls.fixture.payload["source_hash"])
            c.execute((REPO / "lab/official_source/cutover/03_local_fulfillment.sql").read_text(encoding="utf-8"))

    def setUp(self):
        self.c = self.lab.connect()
        self.c.execute("begin")
        self.addCleanup(self.c.close)
        self.addCleanup(self.c.execute, "rollback")
        actor(self.c)

    def load(self):
        payload = package()
        self.lab.load(self.c, payload, official.PROFILES, expected_generation=1, request_id="synthetic-fulfillment")
        return payload["source_hash"], 2

    def test_native_exact_nullable_pairs_and_october_partial_do_not_replace_order(self):
        pin = self.load()
        rows = self.c.execute("select * from operational_read_fulfillment(%s,%s)", pin).fetchall()
        self.assertEqual(len(rows), len(PRODUCTS))
        self.assertTrue(all(row["partial_period"] for row in rows))
        self.assertTrue(any(row["delivery_text"] == "0.0000000000000001" for row in rows))
        self.assertTrue(any(row["order_text"] == "0" and row["delivery_text"] is None for row in rows))
        original = self.c.execute("select distinct value from operational_read_observations(%s,%s) where metric_code='ORDER'", pin).fetchall()
        self.assertEqual([str(row["value"]) for row in original], ["50"])

    def test_native_rls_filters_and_raw_role_privileges_are_preserved(self):
        pin = self.load()
        actor(self.c, VIEW_A, "authenticated")
        rows = self.c.execute("select * from operational_read_fulfillment(%s,%s)", pin).fetchall()
        self.assertEqual({str(row["chain_id"]) for row in rows}, {CHAINS[0]})
        self.assertFalse(self.c.execute("select * from operational_read_fulfillment(%s,%s,p_chain_id=>%s)", (*pin, CHAINS[1])).fetchall())
        self.c.execute("reset role")
        for role in ("anon", "service_role", "authenticated"):
            self.assertFalse(self.c.execute("select has_table_privilege(%s,'public.monthly_fulfillment','SELECT') allowed", (role,)).fetchone()["allowed"])
        self.assertFalse(self.c.execute("select has_function_privilege('anon','public.operational_read_fulfillment(text,bigint,uuid,uuid[],date,date,text,text)','EXECUTE') allowed").fetchone()["allowed"])

    def test_native_pinned_context_and_uncommitted_stage_cannot_leak(self):
        original = (self.fixture.payload["source_hash"], 1)
        pin = self.load()
        with self.lab.connect() as observer:
            actor(observer, VIEW_A, "authenticated")
            self.assertFalse(observer.execute("select * from operational_read_fulfillment(%s,%s)", original).fetchall())
        with self.assertRaises(Exception) as caught:
            with self.c.transaction():
                self.c.execute("select * from operational_read_fulfillment(%s,%s)", original)
        self.assertEqual(caught.exception.sqlstate, "40001")
        self.assertEqual(pin[1], 2)


if __name__ == "__main__":
    unittest.main()
