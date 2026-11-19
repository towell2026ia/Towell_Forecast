"""Opt-in PostgreSQL checks. Never accepts the live/local operational database.

The disposable database already contains an independently staged sealed run.
All choices below belong to new synthetic actors, never to a real account.
"""

import json
import os
from pathlib import Path
import re
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
import uuid


@unittest.skipUnless(
    os.environ.get("FT_G6_TEST_DATABASE"),
    "Hosted Champion SQL needs a separately authorized disposable G6 database",
)
class HostedChampionPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from services.assistant_api.local_postgres import (
            LocalPostgresGateway,
            unprotect,
        )

        name = os.environ["FT_G6_TEST_DATABASE"]
        if not re.fullmatch(r"gateb_g6_publicacion_\d{6}", name):
            raise ValueError("ISOLATED_PUBLICATION_DATABASE_REQUIRED")
        cfg = json.loads(
            Path(os.environ["FT_G6_TEST_LOCAL_CONFIG"]).read_text(encoding="utf-8-sig")
        )
        cls.gateway = LocalPostgresGateway.__new__(LocalPostgresGateway)
        cls.gateway.config = {**cfg, "database": name}
        cls.gateway.password = unprotect(Path(cfg["password_dpapi_file"]))
        with cls.gateway.connect() as c:
            assert c.execute("select current_database() d").fetchone()["d"] == name
            with c.transaction():
                c.execute(
                    (
                        Path(__file__).resolve().parents[2]
                        / "scripts/publication_g6/02_hosted_champion.sql"
                    ).read_text()
                )
            cls.release = c.execute(
                "select * from private.ft_g6_release where active"
            ).fetchone()
            cls.ctx = cls.release["context"]
            chain_join = (
                "join public.chains ch on ch.id=p.chain_id"
                if c.execute("select to_regclass('public.chains') t").fetchone()["t"]
                else ""
            )
            cls.products = c.execute(
                """select distinct on(p.chain_id) p.* from private.ft_g6_product p
                """
                + chain_join
                + """ where p.run_id=%s
                and jsonb_typeof(p.payload->'candidates'->'STATISTICAL'->'horizons'->0->'value')='string'
                and jsonb_typeof(p.payload->'candidates'->'ML'->'horizons'->0->'value')='string'
                order by p.chain_id,p.product_id limit 2""",
                (cls.release["run_id"],),
            ).fetchall()
            assert len(cls.products) == 2
            cls.actors = {
                role: str(uuid.uuid4())
                for role in ("admin", "viewer", "editor", "foreign", "inactive")
            }
            with c.transaction():
                for role, uid in cls.actors.items():
                    if c.execute("select to_regclass('auth.users') t").fetchone()["t"]:
                        c.execute("insert into auth.users(id) values(%s)", (uid,))
                    c.execute(
                        "insert into profiles(id,global_role,status) values(%s,%s,%s)",
                        (
                            uid,
                            "ADMIN"
                            if role == "admin"
                            else "EDITOR"
                            if role == "editor"
                            else "VIEWER",
                            "INACTIVE" if role == "inactive" else "ACTIVE",
                        ),
                    )
                    if role in {"viewer", "editor", "foreign"}:
                        chain = cls.products[1 if role == "foreign" else 0]["chain_id"]
                        c.execute(
                            "insert into user_chain_access(user_id,chain_id,can_view,can_run_forecast) values(%s,%s,true,%s)",
                            (uid, chain, role == "editor"),
                        )

    def session(self, c, role="admin"):
        c.execute("set local role authenticated")
        c.execute(
            "select set_config('request.jwt.claim.sub',%s,true)", (self.actors[role],)
        )

    def params(self, products=None, family="STATISTICAL", key=None):
        products = self.products[:1] if products is None else products
        return (
            self.ctx["source_hash"],
            self.ctx["generation"],
            self.release["run_id"],
            family,
            2027,
            list({p["chain_id"] for p in products}),
            [p["product_id"] for p in products],
            "Synthetic explicit test choice",
            key or str(uuid.uuid4()),
            True,
        )

    def save(self, c, params):
        return c.execute(
            "select operational_save_champion(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) result",
            params,
        ).fetchone()["result"]

    def current(self, c, product):
        return c.execute(
            "select operational_read_champions(%s,%s,2027,%s,%s) result",
            (
                self.ctx["source_hash"],
                self.ctx["generation"],
                [product["chain_id"]],
                [product["product_id"]],
            ),
        ).fetchone()["result"]

    def test_post_commit_reconnect_exact_values_and_no_source_or_temporal_relabel(self):
        params = self.params()
        with self.gateway.connect() as c, c.transaction():
            self.session(c)
            saved = self.save(c, params)
        with self.gateway.connect() as c, c.transaction():
            self.session(c, "viewer")
            current = self.current(c, self.products[0])
        self.assertEqual(current["covered"], 1)
        (row,) = current["products"]
        self.assertEqual(row["id"], saved["id"])
        self.assertEqual(
            row["product"]["horizons"],
            self.products[0]["payload"]["candidates"]["STATISTICAL"]["horizons"],
        )
        self.assertEqual(row["run_emitted_at"], self.release["metadata"]["emitted_at"])
        self.assertEqual(row["source_context"]["observed_cutoff"], "2026-09")
        self.assertEqual(row["cutoff"], "2026-12")
        self.assertEqual(row["product"]["horizons"][0]["target_period"], "2027-01")
        self.assertFalse(row["official_publication"])
        self.assertNotIn("products", row)

    def test_roles_anonymous_wrong_chain_and_direct_tables_are_denied(self):
        import psycopg

        for role in ("viewer", "editor", "foreign", "inactive"):
            with (
                self.subTest(role=role),
                self.gateway.connect() as c,
                self.assertRaises(psycopg.Error),
                c.transaction(),
            ):
                self.session(c, role)
                self.save(c, self.params())
        with (
            self.gateway.connect() as c,
            self.assertRaises(psycopg.Error),
            c.transaction(),
        ):
            c.execute("set local role anon")
            self.save(c, self.params())
        for table in ("ft_g6_champion_event", "ft_g6_champion_current"):
            with (
                self.gateway.connect() as c,
                self.assertRaises(psycopg.Error),
                c.transaction(),
            ):
                self.session(c)
                c.execute("select * from private." + table)

    def test_metadata_only_and_scope_cap_do_not_expose_other_products(self):
        import psycopg

        with self.gateway.connect() as c, c.transaction():
            self.session(c, "viewer")
            metadata = c.execute(
                "select operational_read_planning_run(%s,%s,'{}'::uuid[],'{}'::uuid[]) result",
                (self.ctx["source_hash"], self.ctx["generation"]),
            ).fetchone()["result"]
            self.assertEqual(metadata["products"], [])
            scoped = c.execute(
                "select operational_read_planning_run(%s,%s,%s,%s) result",
                (
                    self.ctx["source_hash"],
                    self.ctx["generation"],
                    [self.products[0]["product_id"]],
                    [self.products[0]["chain_id"]],
                ),
            ).fetchone()["result"]
            self.assertEqual(
                [p["product_id"] for p in scoped["products"]],
                [str(self.products[0]["product_id"])],
            )
        with (
            self.gateway.connect() as c,
            self.assertRaises(psycopg.Error),
            c.transaction(),
        ):
            self.session(c, "viewer")
            c.execute(
                "select operational_read_planning_run(%s,%s,%s,%s)",
                (
                    self.ctx["source_hash"],
                    self.ctx["generation"],
                    [p["product_id"] for p in self.products],
                    [p["chain_id"] for p in self.products],
                ),
            )

    def test_idempotent_concurrency_and_replay_cannot_replace_a_newer_choice(self):
        params = self.params(key=str(uuid.uuid4()))
        barrier = threading.Barrier(2)

        def operation():
            with self.gateway.connect() as c, c.transaction():
                self.session(c)
                barrier.wait(timeout=10)
                return self.save(c, params)

        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(lambda _: operation(), range(2)))
        self.assertEqual(a["id"], b["id"])
        with self.gateway.connect() as c, c.transaction():
            self.session(c)
            latest = self.save(c, self.params(family="ML"))
            self.assertEqual(self.save(c, params)["id"], a["id"])
            self.assertEqual(
                self.current(c, self.products[0])["products"][0]["id"], latest["id"]
            )

    def test_missing_curve_or_context_blocks_whole_scope_without_partial_choice(self):
        import psycopg

        for path, value in (
            ("{candidates,STATISTICAL,horizons,0,value}", "null"),
            ("{input_snapshot,source_context,target_metric}", '"SALES"'),
        ):
            with (
                self.subTest(path=path),
                self.gateway.connect() as c,
                self.assertRaises(psycopg.Error),
                c.transaction(),
            ):
                c.execute(
                    "update private.ft_g6_product set payload=jsonb_set(payload,%s::text[],%s::jsonb) where run_id=%s and product_id=%s",
                    (
                        path,
                        value,
                        self.release["run_id"],
                        self.products[1]["product_id"],
                    ),
                )
                self.session(c)
                self.save(c, self.params(products=self.products))
                raise AssertionError("malformed candidate accepted")
        with self.gateway.connect() as c:
            restored = c.execute(
                "select payload from private.ft_g6_product where run_id=%s and product_id=%s",
                (self.release["run_id"], self.products[1]["product_id"]),
            ).fetchone()["payload"]
        self.assertEqual(restored, self.products[1]["payload"])

    def test_stale_source_wrong_run_and_idempotency_conflict_rejected(self):
        import psycopg

        for index, value in ((0, "f" * 64), (2, uuid.uuid4()), (9, False)):
            args = list(self.params())
            args[index] = value
            with (
                self.subTest(index=index),
                self.gateway.connect() as c,
                self.assertRaises(psycopg.Error),
                c.transaction(),
            ):
                self.session(c)
                self.save(c, args)
        args = self.params()
        with self.gateway.connect() as c, c.transaction():
            self.session(c)
            self.save(c, args)
        changed = list(args)
        changed[7] = "Synthetic different decision"
        with (
            self.gateway.connect() as c,
            self.assertRaisesRegex(psycopg.Error, "CHAMPION_IDEMPOTENCY_CONFLICT"),
            c.transaction(),
        ):
            self.session(c)
            self.save(c, changed)
