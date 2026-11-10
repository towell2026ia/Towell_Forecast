"""Actual PostgreSQL rehearsal. All rows are synthetic and no URL is accepted."""
import os
import unittest
import uuid
from dataclasses import replace
from decimal import Decimal

import psycopg
from psycopg import sql

from .fixtures import A, ADMIN, CHAINS, PRODUCTS, VIEW_A, _batch, actor, document, seed, uid
from .local import LocalLab, digest
from .unification import UnificationLab

P0, P1, P2, _ = tuple(PRODUCTS)
MONTH = "2026-10"
B = uid("unification-b")


class UnificationPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get("GATEB_CONFIG")
        if not path:
            raise ValueError("STOP_B0: GATEB_CONFIG required")
        cls.root = LocalLab.from_file(path)
        name = "gateb_unify_" + uuid.uuid4().hex[:12]
        cls.lab = UnificationLab({**cls.root.config, "database": name})
        with cls.root.connect() as c:
            c.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
        cls.addClassCleanup(cls.remove_database, name)
        cls.lab.initialize()
        cls.fixture = seed(cls.lab, stage_candidates=False, include_prior_year=True)
        cls.lab.install()

    @classmethod
    def remove_database(cls, name):
        if name != cls.lab.config["database"] or not name.startswith("gateb_unify_"):
            raise ValueError("STOP_UNIFICATION_LAB")
        with cls.root.connect() as c:
            c.execute(sql.SQL("drop database {}").format(sql.Identifier(name)))

    def inventory(self, c, version, product, period, qty, kind="RS"):
        chain = PRODUCTS[product]["chain"]
        c.execute("""
            insert into operational_data_version_inventory
              (version_id,chain_id,product_id,period,format,upc,purchase_type,quantity,source_reference)
            values(%s,%s,%s,%s,%s,%s,%s,%s,'SYNTHETIC')
        """, (version, chain, product, period + "-01", PRODUCTS[product]["format"],
              f"{list(PRODUCTS).index(product):013}", kind, qty))

    def baseline_ids(self, c, context):
        return {(r["product_id"], r["period"], r["metric_code"]): r["observation_id"]
                for r in self.lab.read(c, context)}

    def build_replacement(self, c, context):
        self.lab.lock_active(c, context)
        selected = self.baseline_ids(c, context)
        batch = _batch(c, CHAINS[0], uid("profile-version-" + CHAINS[0]), MONTH, "unification")
        for product, metric, value in (
            (P0, "ORDER", "100"), (P0, "DELIVERY", "30"),
            (P1, "ORDER", "20"), (P1, "DELIVERY", "20"),
            (P0, "FCST_TOWELL_HISTORY", "19.0123456789012345"),
            (P0, "FCST_CLIENTE_HISTORY", "25.9"),
        ):
            selected[product, MONTH, metric] = self.lab.put(
                c, chain=CHAINS[0], product=product, period=MONTH, metric=metric, value=value, batch=batch,
            )
        ids = list(selected.values())
        manifest = {**document(c, ids, "Unification B"), "contract": "unification-1"}
        self.lab.build(c, B, manifest, ids, context.version_id)
        self.inventory(c, B, P0, MONTH, "25.1234567890123456789")
        self.inventory(c, B, P0, "2026-09", "15")
        self.inventory(c, B, P2, MONTH, "70")
        return self.lab.seal(c, B)

    def test_U01_existing_seal_and_2024_ids_preserved(self):
        with self.lab.connect() as c:
            actor(c)
            context = self.lab.context(c)
            self.assertEqual(context.version_id, A)
            self.assertEqual(context.manifest_hash, self.fixture["hashes"][A])
            self.assertEqual(c.execute("select private.gateb_manifest_hash(%s) as h", (A,)).fetchone()["h"], context.manifest_hash)
            rows = self.lab.read(c, context)
            self.assertEqual(len([r for r in rows if r["period"].startswith("2024")]), 4)
            self.assertEqual(set(self.fixture["old_ids"]), {r["observation_id"] for r in rows})

    def test_U02_unpublished_physical_import_cannot_commit(self):
        with self.lab.connect() as c:
            actor(c)
            before = c.execute("select count(*) n from monthly_observations").fetchone()["n"]
            with self.assertRaisesRegex(psycopg.Error, "observation_requires_atomic_publication"), c.transaction():
                self.lab.put(c, chain=CHAINS[0], product=P0, period=MONTH, metric="SALES",
                             value="456", batch=self.fixture["batches"][CHAINS[0], MONTH])
            self.assertEqual(c.execute("select count(*) n from monthly_observations").fetchone()["n"], before)

    def test_U03_unpublished_version_and_inventory_are_hidden_even_by_id(self):
        draft = uid("unification-never-published")
        with self.lab.connect() as c:
            actor(c)
            context = self.lab.context(c)
            ids = list(self.baseline_ids(c, context).values())
            with c.transaction():
                self.lab.build(c, draft, {**document(c, ids, "Draft"), "contract": "unification-1"}, ids, A)
                self.inventory(c, draft, P0, MONTH, "321")
                hash_draft = self.lab.seal(c, draft)
            self.assertEqual(c.execute("select count(*) n from operational_data_version_inventory where version_id=%s", (draft,)).fetchone()["n"], 1)
        with self.lab.connect() as c:
            actor(c, VIEW_A, "authenticated")
            for table in ("operational_data_versions", "operational_data_version_observations", "operational_data_version_inventory"):
                key = "id" if table == "operational_data_versions" else "version_id"
                stmt = sql.SQL("select count(*) n from {} where {}=%s").format(sql.Identifier(table), sql.Identifier(key))
                self.assertEqual(c.execute(stmt, (draft,)).fetchone()["n"], 0)
            with self.assertRaisesRegex(ValueError, "unavailable_pinned_version"):
                self.lab.read(c, replace(context, version_id=draft, manifest_hash=hash_draft))

    def test_U04_rejects_history_replacement_and_duplicate_inventory(self):
        with self.lab.connect() as c:
            actor(c)
            context = self.lab.context(c)
            draft = uid("unification-changed-2024")
            with self.assertRaisesRegex(psycopg.Error, "protected_real_history_changed"), c.transaction():
                ids = self.baseline_ids(c, context)
                ids[P0, "2024-10", "SALES"] = self.lab.put(
                    c, chain=CHAINS[0], product=P0, period="2024-10", metric="SALES", value="99",
                    batch=self.fixture["batches"][CHAINS[0], "2024-10"],
                )
                selected = list(ids.values())
                self.lab.build(c, draft, {**document(c, selected, "Protected"), "contract": "unification-1"}, selected, A)
                self.lab.seal(c, draft)
            with self.assertRaises(psycopg.errors.UniqueViolation), c.transaction():
                selected = list(self.baseline_ids(c, context).values())
                self.lab.build(c, draft, {**document(c, selected, "Duplicates"), "contract": "unification-1"}, selected, A)
                self.inventory(c, draft, P0, MONTH, "5")
                self.inventory(c, draft, P0, MONTH, "7", kind="CE")

    def test_U05_atomic_failure_and_pinned_read_after_switch_and_revert(self):
        with self.lab.connect() as writer, self.lab.connect() as reader:
            actor(writer)
            actor(reader, VIEW_A, "authenticated")
            a = self.lab.context(reader)
            rows_a = self.lab.read(reader, a)
            public_a = reader.execute("select observation_id::text id from portal_monthly_observations_current order by 1").fetchall()
            history_a = [r for r in rows_a if r["period"] == "2024-10"]
            self.assertTrue(history_a)
            def stage_and_activate(fail=False):
                with writer.transaction():
                    h = self.build_replacement(writer, a)
                    self.assertEqual(self.lab.context(reader), a)
                    self.assertEqual(self.lab.read(reader, a), rows_a)
                    self.assertEqual(reader.execute("select count(*) n from monthly_observations where value=100").fetchone()["n"], 0)
                    self.lab.activate(writer, B, h, a.version_id, a.generation, str(uuid.uuid4()), fail)
            with self.assertRaisesRegex(psycopg.Error, "injected_activation_failure"):
                stage_and_activate(fail=True)
            self.assertEqual(self.lab.context(reader), a)
            self.assertEqual(writer.execute("select count(*) n from operational_data_versions where id=%s",(B,)).fetchone()["n"], 0)
            stage_and_activate()
            b = self.lab.context(reader)
            self.assertEqual(b.version_id, B)
            try:
                self.assertEqual(self.lab.read(reader, a), rows_a)
                rows_b = self.lab.read(reader, b)
                self.assertEqual([r for r in rows_b if r["period"] == "2024-10"], history_a)
                self.assertEqual({r["chain_id"] for r in rows_b}, {CHAINS[0]})
                by_metric = {r["metric_code"]: Decimal(r["value"]) for r in rows_b if r["product_id"] == P0 and r["period"] == MONTH}
                self.assertEqual(by_metric, {"ORDER": Decimal(100), "SALES": Decimal("7.125"), "DELIVERY": Decimal(30),
                                            "FCST_CLIENTE_HISTORY": Decimal("25.9"),
                                            "FCST_TOWELL_HISTORY": Decimal("19.0123456789012345")})
                fcst = [r for r in rows_b if r["metric_code"].startswith("FCST_")]
                self.assertTrue(all(r["available_at"] is None and r["availability_source"] == "UNKNOWN" for r in fcst))
                self.assertEqual([(r["period"], r["quantity"]) for r in self.lab.inventory(reader, b)],
                                 [("2026-09", "15"), (MONTH, "25.1234567890123456789")])
                fill = next(r for r in self.lab.fill_rate(reader, b) if r["period"] == MONTH)
                self.assertEqual((fill["delivery"], fill["ordered"]), (Decimal(50), Decimal(120)))
                self.assertAlmostEqual(fill["fill_rate"], Decimal(50) / Decimal(120), places=18)
                self.assertEqual(reader.execute("select count(*) n from monthly_observations_current").fetchone()["n"], 0)
                self.assertEqual(reader.execute("select value from portal_monthly_observations_current where product_id=%s and period=%s and metric_code='ORDER'",
                                                (P0, MONTH + "-01")).fetchone()["value"], Decimal(100))
                with self.assertRaisesRegex(psycopg.Error, "stale_active_generation"), writer.transaction():
                    self.lab.lock_active(writer, a)
                with self.assertRaisesRegex(psycopg.Error, "sealed_members_immutable"), writer.transaction():
                    writer.execute("update operational_data_version_inventory set quantity=1 where version_id=%s", (B,))
            finally:
                self.lab.activate(writer, A, a.manifest_hash, b.version_id, b.generation, str(uuid.uuid4()))
            restored = self.lab.context(reader)
            self.assertEqual(restored.version_id, A)
            self.assertEqual(restored.manifest_hash, a.manifest_hash)
            self.assertEqual(digest(self.lab.read(reader, restored)), digest(rows_a))
            self.assertEqual(reader.execute("select observation_id::text id from portal_monthly_observations_current order by 1").fetchall(), public_a)
            self.assertEqual(self.lab.inventory(reader, restored), [])
            self.assertEqual(len(self.lab.inventory(reader, b)), 2)

    def test_U06_inactive_release_blocks_anon_and_foreign_tenant(self):
        with self.lab.connect() as c:
            actor(c, None, "anon")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute("select count(*) from operational_data_version_inventory").fetchone()
            actor(c, VIEW_A, "authenticated")
            a = self.lab.context(c)
            self.assertEqual({r["chain_id"] for r in self.lab.read(c, a)}, {CHAINS[0]})
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.lab.lock_active(c, a)


if __name__ == "__main__":
    unittest.main()
