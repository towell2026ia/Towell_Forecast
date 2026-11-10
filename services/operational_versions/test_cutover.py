"""Exercise the proposed read boundary and exact release loader in PostgreSQL."""
import csv
import json
import os
import unittest
import uuid
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import psycopg
from psycopg import sql

from .cutover_archive import file_sha, key, read_archive
from .cutover_loader import initialize_baseline, promote_archive
from .cutover_sql import proposed_sql
from .fixtures import A, CHAINS, PRODUCTS, VIEW_A, actor, document, seed, uid
from .local import LocalLab
from .unification import UnificationLab

P0, P1, P2, _ = PRODUCTS
MONTH = "2026-10"
BASELINE_MONTH = "2026-09"
PROFILES = {chain: uid("profile-version-" + chain) for chain in CHAINS}


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def archive_fixture(root, baseline):
    root.mkdir()
    a = {key(row): dict(row) for row in baseline}
    b = dict(a)
    changes = []
    for product, metric, amount in ((P0, "ORDER", "100"), (P0, "DELIVERY", "30"),
                                    (P0, "FCST_TOWELL_HISTORY", "18.234567890123456"),
                                    (P0, "FCST_CLIENTE_HISTORY", "25"), (P1, "DELIVERY", "20")):
        identity = CHAINS[0], product, MONTH, metric
        prior = a.get(identity)
        changed = dict(zip(("chain_id", "product_id", "period", "metric_code", "observation_id", "version_no",
                            "value", "availability_source", "available_at"),
                           (*identity, uid(f"archive-{product}-{metric}"), int(prior["version_no"])+1 if prior else 1,
                            amount, "UNKNOWN", ""), strict=True))
        b[identity] = changed
        changes.append({**changed, "status": "replaced" if prior else "new", "tipo_compra": "RS",
                        "cadena": "SYNTHETIC_0", "formato": "SYNTHETIC", "item": "",
                        "upc": f"{list(PRODUCTS).index(product):07}",
                        "referencia_origen": "synthetic:A1", "previous_id": prior["observation_id"] if prior else "",
                        "previous_value": prior["value"] if prior else ""})
    write_csv(root / "Observaciones_A.csv", list(a.values()))
    write_csv(root / "Observaciones_B.csv", list(b.values()))
    write_csv(root / "Cambios_B.csv", changes)
    write_csv(root / "Inventario_B.csv", [{"chain_id": CHAINS[0], "product_id": P0, "period": MONTH,
             "cadena": "SYNTHETIC_0", "formato": "SYNTHETIC", "item": "", "upc": "0000000",
             "tipo_compra": "RS", "referencia_origen": "synthetic:Y1",
             "value": "9.123456789012345"}])
    catalog = {"chains": [{"id": chain} for chain in CHAINS],
               "products": [{"id": product, "chain_id": info["chain"]} for product, info in PRODUCTS.items()]}
    (root / "Catalogo_completo.json").write_text(json.dumps(catalog), encoding="utf-8")
    manifest_path = root / "Manifest.json"
    manifest_path.write_text(json.dumps({
        "candidate_id": "UNIF-SYNTHETIC", "status": "PRIVATE_NOT_ACTIVATED", "cutoff": MONTH,
        "results_sha256": {path.name: file_sha(path) for path in sorted(root.iterdir())},
        "catalog_count": len(PRODUCTS), "scope_count": len(CHAINS), "observations_a": len(a),
        "observations_b": len(b), "inventory_rows": 1,
    }), encoding="utf-8")
    return file_sha(manifest_path)


def pin(c):
    row = c.execute("select * from operational_read_context()").fetchone()
    return str(row["version_id"]), row["manifest_hash"], row["generation"]


def read(c, pinned, mode="OPERATIONAL", cutoff=None):
    return c.execute("select * from operational_read_observations(%s,%s,%s,%s,%s) order by id",
                     (*pinned, mode, cutoff)).fetchall()


def maintenance(c, database, manifest_sha):
    c.execute("""select set_config('towell.cutover_mode','SYNTHETIC_REVIEW',false),
                 set_config('towell.cutover_database',%s,false),
                 set_config('towell.cutover_manifest_sha256',%s,false)""", (database, manifest_sha))


class CutoverPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = LocalLab.from_file(os.environ["GATEB_CONFIG"])
        name = "gateb_unify_" + uuid.uuid4().hex[:12]
        cls.lab = UnificationLab({**cls.root.config, "database": name})
        with cls.root.connect() as c:
            c.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
        cls.addClassCleanup(cls.remove_database, name)
        cls.lab.initialize(include_gateb=False)
        cls.fixture = seed(cls.lab, stage_candidates=False, include_prior_year=True,
                           temporal_history=True, activate_initial=False, commercial_profiles=True)
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.archive_dir = Path(directory.name) / "archive"
        with cls.lab.connect() as c:
            actor(c)
            c.execute("""
                create function public.e4_close_live(uuid,uuid,date,uuid,text) returns bigint
                language sql security definer as $$ select count(*) from public.monthly_observations $$;
                grant execute on function public.e4_close_live(uuid,uuid,date,uuid,text) to service_role;
            """)
            records = c.execute("""
                select o.chain_id::text,o.product_id::text,to_char(o.period,'YYYY-MM') period,o.metric_code,
                  o.id::text observation_id,o.version_no,o.value::text,o.availability_source,o.available_at
                from monthly_observations o where o.id=any(%s::uuid[])
            """, (cls.fixture["old_ids"],)).fetchall()
            for row in records:
                row["available_at"] = row["available_at"].isoformat() if row["available_at"] else ""
            cls.archive_sha = archive_fixture(cls.archive_dir, records)
            cls.archive = read_archive(cls.archive_dir, cls.archive_sha)
            phase01, phase02 = proposed_sql()
            with c.transaction():
                maintenance(c, name, cls.archive_sha)
                c.execute(phase01)
                initialize_baseline(cls.lab, c, cls.archive, A, "archive-initial-baseline")
                c.execute(phase02)

    @classmethod
    def remove_database(cls, name):
        if name != cls.lab.config["database"] or not name.startswith("gateb_unify_"):
            raise ValueError("STOP_UNIFICATION_LAB")
        with cls.root.connect() as c:
            c.execute(sql.SQL("drop database {}").format(sql.Identifier(name)))

    def test_CU01_archive_hash_membership_and_no_overwrite(self):
        self.assertEqual(self.archive.summary()["physical_inserts"], 5)
        self.assertEqual(self.archive.summary()["new_batches"], 1)
        with self.assertRaisesRegex(ValueError, "candidate_manifest_sha_mismatch"):
            read_archive(self.archive_dir, "0" * 64)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for artifact in self.archive_dir.iterdir():
                (root / artifact.name).write_bytes(artifact.read_bytes())
            with (root / "Cambios_B.csv").open("a", encoding="utf-8") as changed:
                changed.write("\n")
            with self.assertRaisesRegex(ValueError, "candidate_artifact_sha_mismatch"):
                read_archive(root, self.archive_sha)

    def test_CU02_draft_and_raw_service_role_are_inaccessible(self):
        draft = uid("cutover-draft")
        with self.lab.connect() as c, c.transaction():
            actor(c)
            a = self.lab.context(c)
            ids = [r["observation_id"] for r in self.lab.read(c, a)]
            self.lab.build(c, draft, {**document(c, ids, "Draft"), "contract": "unification-1"}, ids, A)
            draft_hash = self.lab.seal(c, draft)
        with self.lab.connect() as c:
            actor(c, VIEW_A, "authenticated")
            with self.assertRaisesRegex(psycopg.Error, "invalid_operational_pin"):
                read(c, (draft, draft_hash, 2))
            self.assertNotIn(draft, {str(row["id"]) for row in read(c, pin(c), "AUDIT")})
            for role in ("authenticated", "anon", "service_role"):
                c.execute("reset role")
                c.execute(sql.SQL("set role {}").format(sql.Identifier(role)))
                for relation in ("monthly_observations", "operational_data_versions",
                                 "operational_data_version_observations", "operational_data_version_inventory"):
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        c.execute(sql.SQL("select count(*) from {}").format(sql.Identifier(relation)))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute("select * from operational_read_context()")
            self.assertFalse(c.execute("""
                select has_function_privilege('public.e4_close_live(uuid,uuid,date,uuid,text)','EXECUTE') as allowed
            """).fetchone()["allowed"])

    def test_CU03_point_in_time_filters_before_picking_version(self):
        with self.lab.connect() as c:
            actor(c, VIEW_A, "authenticated")
            a = pin(c)
            sales = [r for r in read(c, a, "AUDIT") if str(r["product_id"]) == P0
                     and r["period"].strftime("%Y-%m") == BASELINE_MONTH and r["metric_code"] == "SALES"]
            self.assertEqual({r["value"] for r in sales}, {Decimal(0), Decimal(6)})
            accredited = next(r for r in sales if r["available_at"])
            self.assertEqual(len(read(c, a, "TEMPORAL", accredited["available_at"] - timedelta(seconds=1))), 0)
            self.assertEqual([r["id"] for r in read(c, a, "TEMPORAL", accredited["available_at"])],
                             [accredited["id"]])
            periods = c.execute("select * from operational_read_periods(%s,%s,%s,%s) order by period",
                                (*a, CHAINS[0])).fetchall()
            self.assertEqual([r["period"].strftime("%Y-%m") for r in periods], ["2024-10", *[BASELINE_MONTH, MONTH]])

    def test_CU04_physical_drift_rolls_back_batches(self):
        identity = next(iter(self.archive.changes))
        wrong = dict(self.archive.candidate)
        wrong[identity] = {**wrong[identity], "version_no": 99}
        with self.lab.connect() as c:
            actor(c)
            maintenance(c, self.lab.config["database"], self.archive_sha)
            a = self.lab.context(c)
            before = c.execute("select count(*) from import_batches").fetchone()
            with self.assertRaisesRegex(ValueError, "archive_physical_version_drift"):
                promote_archive(self.lab, c, a, replace(self.archive, candidate=wrong), PROFILES, "bad-physical-version")
            self.assertEqual(self.lab.context(c), a)
            self.assertEqual(c.execute("select count(*) from import_batches").fetchone(), before)

    def test_CU05_exact_load_a_b_a_with_operational_and_audit_pin(self):
        with self.lab.connect() as c, self.lab.connect() as reader:
            actor(c)
            maintenance(c, self.lab.config["database"], self.archive_sha)
            actor(reader, VIEW_A, "authenticated")
            a = self.lab.context(c)
            old_pin, old_rows, old_audit = pin(reader), read(reader, pin(reader)), read(reader, pin(reader), "AUDIT")
            with c.transaction():
                promote_archive(self.lab, c, a, self.archive, PROFILES, "archive-exact-activation")
                self.assertEqual(pin(reader), old_pin)
                self.assertEqual(read(reader, old_pin, "AUDIT"), old_audit)
            b = self.lab.context(c)
            b_pin = pin(reader)
            self.assertEqual(b.version_id, self.archive.version_id)
            self.assertEqual(read(reader, old_pin), old_rows)
            self.assertEqual(read(reader, old_pin, "AUDIT"), old_audit)
            b_rows = read(reader, b_pin)
            self.assertEqual({str(row["chain_id"]) for row in b_rows}, {CHAINS[0]})
            sourced = next(row for row in b_rows if row["metric_code"] == "FCST_TOWELL_HISTORY")
            self.assertEqual(sourced["value"], Decimal("18.234567890123456"))
            self.assertEqual((sourced["source_reference"], sourced["source_value_literal"], sourced["available_at"]),
                             ("synthetic:A1", "18.234567890123456", None))
            self.assertEqual(c.execute("select count(*) from operational_data_version_observations where version_id=%s",
                                       (b.version_id,)).fetchone()["count"], len(self.archive.candidate))
            inventory = reader.execute("select * from operational_read_inventory(%s,%s,%s)", b_pin).fetchall()
            self.assertEqual([r["quantity_text"] for r in inventory], ["9.123456789012345"])
            rate = reader.execute("select * from operational_read_fill_rate(%s,%s,%s) where period=%s",
                                  (*b_pin, MONTH + "-01")).fetchone()
            self.assertEqual((rate["delivery"], rate["ordered"], rate["paired_products"], rate["total_products"]),
                             (Decimal(30), Decimal(100), 1, 2))
            self.assertEqual(rate["fill_rate"], Decimal("0.3"))
            with self.assertRaisesRegex(psycopg.Error, "stale_active_generation"):
                promote_archive(self.lab, c, a, self.archive, PROFILES, "archive-exact-activation")
            self.lab.activate(c, a.version_id, a.manifest_hash, b.version_id, b.generation, "archive-exact-revert")
            restored = pin(reader)
            self.assertEqual(restored[:2], old_pin[:2])
            self.assertEqual(read(reader, restored), old_rows)
            self.assertEqual(read(reader, b_pin), b_rows)
            self.assertGreater(len(read(reader, restored, "AUDIT")), len(old_audit))
            self.assertEqual(reader.execute("select count(*) from operational_read_inventory(%s,%s,%s)", restored).fetchone()["count"], 0)

    def test_CU06_maintenance_marker_archive_and_catalog_fail_closed(self):
        with self.lab.connect() as c:
            actor(c)
            a = self.lab.context(c)
            with self.assertRaisesRegex(psycopg.Error, "STOP_OPERATIONAL_CUTOVER_GUARD"):
                c.execute("select private.operational_assert_maintenance()")
            maintenance(c, "another_database", self.archive_sha)
            with self.assertRaisesRegex(psycopg.Error, "STOP_OPERATIONAL_CUTOVER_GUARD"):
                c.execute("select private.operational_assert_maintenance()")
            maintenance(c, self.lab.config["database"], "0" * 64)
            with self.assertRaisesRegex(ValueError, "maintenance_archive_sha_mismatch"):
                promote_archive(self.lab, c, a, self.archive, PROFILES, "wrong-maintenance-sha")
            maintenance(c, self.lab.config["database"], self.archive_sha)
            changed_catalog = {**self.archive.catalog, "products": self.archive.catalog["products"][:-1]}
            with self.assertRaisesRegex(ValueError, "archive_catalog_identity_drift"):
                promote_archive(self.lab, c, a, replace(self.archive, catalog=changed_catalog), PROFILES, "wrong-catalog")


if __name__ == "__main__":
    unittest.main()
