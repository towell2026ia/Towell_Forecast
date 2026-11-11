"""Real PostgreSQL tests of the sole official source, using synthetic records."""
import copy
import json
import os
from pathlib import Path
import unittest
import uuid
from decimal import Decimal

import psycopg
from psycopg import sql

from .fixtures import ADMIN, CHAINS, PRODUCTS, VIEW_A, VIEW_B, actor, seed, uid
from .local import REPO, LocalLab, digest
from .official_cleanup import render_cleanup, retirement_plan
from .official_source import AuthorizedOfficialConnection, OfficialSourceLab, validate_package

PROFILES = {chain: uid("profile-version-" + chain) for chain in CHAINS}


def package(label="first", amount="30"):
    source_hash = digest({"synthetic_official_source": label})
    sources = [{"id": uid("official-book-one"), "source_name": "Synthetic forecasts",
                "source_hash": digest("book-one"), "reference": "Synthetic.xlsx"},
               {"id": uid("official-book-" + label), "source_name": "Synthetic close",
                "source_hash": digest("book-two-" + label), "reference": "Synthetic-close.xlsx"}]
    observations, inventory = [], []
    for index, (product, info) in enumerate(PRODUCTS.items()):
        common = {"chain_id": info["chain"], "product_id": product, "format": "SYNTHETIC",
                  "upc": f"{index:07}", "purchase_type": "RS", "model": info["model"],
                  "presentation": "MEDIO BAÑO", "color": info["color"], "product_type": "TOALLA",
                  "commercial_code": "SOURCE-GROUP", "account": info["account"]}
        for period in ("2025-01", "2026-10"):
            for metric, value in (("SALES", amount), ("ORDER", "50"), ("DELIVERY", "40"),
                                  ("FCST_TOWELL_HISTORY", "10.1234567890123456"),
                                  ("FCST_CLIENTE_HISTORY", "12.00")):
                observations.append({
                    **common, "id": uid(f"{source_hash}:{product}:{period}:{metric}"), "period": period,
                    "product_type": "TRAPO" if period=="2025-01" else common["product_type"],
                    "metric_code": metric, "value": value if index else ("0" if metric == "SALES" else value),
                    "source_id": sources[0 if metric.startswith("FCST_") else 1]["id"],
                    "source_reference": f"BAASE!{metric}-{index}-{period}",
                })
            inventory.append({**common, "id": uid(f"{source_hash}:{product}:{period}:inventory"),
                              "product_type": "TRAPO" if period=="2025-01" else common["product_type"],
                              "period": period, "value": "7.0001", "source_id": sources[1]["id"],
                              "source_reference": f"BAASE!Y-{index}-{period}"})
    return {"schema": "official-source-1", "source_hash": source_hash, "sources": sources,
            "first_period": "2025-01", "last_period": "2026-10",
            "observations": observations, "inventory": inventory}


def context(c):
    return c.execute("select * from operational_read_context()").fetchone()


def records(c, pinned, mode="OPERATIONAL"):
    return c.execute("""select * from operational_read_observations(%s,%s,%s)
                        order by chain_id,product_id,period,metric_code""",
                     (pinned["source_hash"], pinned["generation"], mode)).fetchall()


class OfficialSourcePostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = LocalLab.from_file(os.environ["GATEB_CONFIG"])
        name = "gateb_official_" + uuid.uuid4().hex[:12]
        cls.lab = OfficialSourceLab({**cls.root.config, "database": name})
        with cls.root.connect() as c:
            c.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
        cls.addClassCleanup(cls.remove_database, name)
        cls.lab.initialize(include_gateb=False)
        with cls.lab.connect() as c, c.transaction():
            for name in ("202609290010_e3_vintage_transaction.sql","202610080011_e1_policy_v2_guard.sql",
                         "202610080012_e4_calculation_history.sql","202610120014_e4_live_temporal_eligibility.sql"):
                c.execute((REPO / "supabase/migrations" / name).read_text(encoding="utf-8"))
        seed(cls.lab, stage_candidates=False, include_prior_year=True, activate_initial=False,
             temporal_history=True, commercial_profiles=True)
        cls.payload = package()
        cls.lab.install(cls.payload["source_hash"])
        with cls.lab.connect() as c:
            actor(c)
            cls.lab.load(c, cls.payload, PROFILES, expected_generation=0, request_id="synthetic-official-first")

    @classmethod
    def remove_database(cls, name):
        if name != cls.lab.config["database"] or not name.startswith("gateb_official_"):
            raise ValueError("OFFICIAL_LAB_REQUIRED")
        with cls.root.connect() as c:
            c.execute(sql.SQL("drop database {}").format(sql.Identifier(name)))

    def test_OS01_current_only_excludes_old_data_and_2024(self):
        with self.lab.connect() as c:
            actor(c, VIEW_A, "authenticated")
            pinned = context(c)
            self.assertEqual(set(pinned), {"source_hash", "generation", "first_period", "last_period"})
            self.assertEqual((pinned["first_period"], pinned["last_period"]), ("2025-01", "2026-10"))
            current = records(c, pinned)
            self.assertEqual(len(current), 20)
            self.assertEqual({str(r["chain_id"]) for r in current}, {CHAINS[0]})
            self.assertEqual({r["period"].year for r in current}, {2025, 2026})
            self.assertEqual({r["metric_code"] for r in current}, {"SALES", "ORDER", "DELIVERY", "FCST_TOWELL_HISTORY", "FCST_CLIENTE_HISTORY"})
            self.assertTrue(any(r["value"] == 0 for r in current))
            self.assertTrue(all(r["source_reference"].startswith("BAASE!") for r in current))
            self.assertTrue(all(r["available_at"] is None and r["availability_source"] == "UNKNOWN" for r in current))
            self.assertIn("10.1234567890123456", {r["value_text"] for r in current})
            self.assertEqual(records(c, pinned, "AUDIT"), current)
        with self.lab.connect() as c:
            self.assertGreater(c.execute("select count(*) n from monthly_observations where period<'2025-01-01'").fetchone()["n"], 0)
            self.assertIsNone(c.execute("select to_regclass('public.operational_data_versions') t").fetchone()["t"])

    def test_OS02_inventory_months_and_fill_rate_keep_scope(self):
        with self.lab.connect() as c:
            actor(c, VIEW_A, "authenticated")
            pinned = context(c)
            stock = c.execute("""select * from operational_read_inventory(%s,%s,p_from=>'2026-10-01',p_format=>'SYNTHETIC')
                                 order by product_id""", (pinned["source_hash"], pinned["generation"])).fetchall()
            self.assertEqual(len(stock), 2)
            self.assertEqual({r["quantity_text"] for r in stock}, {"7.0001"})
            rate = c.execute("select * from operational_read_fill_rate(%s,%s) order by period",
                             (pinned["source_hash"], pinned["generation"])).fetchall()
            self.assertEqual(len(rate), 2)
            self.assertEqual([(r["delivery"], r["ordered"], r["fill_rate"]) for r in rate],
                             [(Decimal(80), Decimal(100), Decimal(".8"))] * 2)
            periods = c.execute("select * from operational_read_periods(%s,%s) order by period",
                                (pinned["source_hash"], pinned["generation"])).fetchall()
            self.assertEqual([r["period"].strftime("%Y-%m") for r in periods], ["2025-01", "2026-10"])
            catalog = c.execute("select * from operational_read_catalog(%s,%s)",
                                (pinned["source_hash"], pinned["generation"])).fetchall()
            self.assertEqual(len(catalog), 2)
            self.assertEqual({r["model"] for r in catalog}, {"Shared Synthetic Design"})
            self.assertEqual({r["product_type"] for r in catalog}, {"TOALLA"})
            original_catalog = c.execute("select * from operational_read_catalog(%s,%s,p_as_of=>'2025-01-01')",
                                         (pinned["source_hash"],pinned["generation"])).fetchall()
            self.assertEqual({r["product_type"] for r in original_catalog},{"TRAPO"})

    def test_OS03_source_scoped_filters_and_temporal_have_no_fallback(self):
        with self.lab.connect() as c:
            actor(c, VIEW_A, "authenticated")
            pinned = context(c)
            args = pinned["source_hash"], pinned["generation"]
            self.assertEqual(c.execute("select count(*) n from operational_read_observations(%s,%s,p_format=>'OTHER')", args).fetchone()["n"], 0)
            self.assertEqual(c.execute("select count(*) n from operational_read_observations(%s,%s,'TEMPORAL',clock_timestamp())", args).fetchone()["n"], 0)
            with self.assertRaisesRegex(psycopg.Error, "OFFICIAL_FILTER_INVALID"):
                c.execute("select * from operational_read_observations(%s,%s,p_purchase_type=>'CE')", args)
            filtered = c.execute("""select * from operational_read_observations(%s,%s,p_from=>'2026-10-01',
                p_metric_codes=>array['ORDER'])""", args).fetchall()
            self.assertEqual(len(filtered), 2)

    def test_OS04_user_anonymous_service_raw_paths_are_fenced(self):
        with self.lab.connect() as c:
            for role in ("authenticated", "anon", "service_role"):
                actor(c, VIEW_A)
                c.execute(sql.SQL("set role {}").format(sql.Identifier(role)))
                for relation in ("monthly_observations", "monthly_inventory", "private.official_observation_source"):
                    with self.subTest(role=role, relation=relation), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        c.execute(sql.SQL("select * from {}").format(sql.SQL(relation)))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute("select * from operational_read_context()")
            actor(c, VIEW_B, "authenticated")
            self.assertEqual({str(r["chain_id"]) for r in records(c, context(c))}, {CHAINS[1]})
            actor(c, ADMIN, "authenticated")
            self.assertEqual(c.execute("select count(*) n from source_evidence").fetchone()["n"],2)
            self.assertTrue(all(r["official_source_hash"]==context(c)["source_hash"]
                                for r in c.execute("select official_source_hash from import_batches")))
            c.execute("reset role")
            self.assertIsNone(c.execute("select to_regclass('public.monthly_observations_current') t").fetchone()["t"])

    def test_OS05_failed_import_has_no_partial_facts(self):
        bad = package("invalid")
        bad["inventory"][0]["upc"] = "999999999"
        with self.lab.connect() as c:
            actor(c)
            before = c.execute("select count(*) n from monthly_observations").fetchone()["n"]
            generation = context(c)["generation"]
            with self.assertRaisesRegex(ValueError, "official_catalog_mapping_mismatch"):
                self.lab.load(c, bad, PROFILES, expected_generation=generation, request_id="synthetic-bad-source")
            self.assertEqual(context(c)["generation"], generation)
            self.assertEqual(c.execute("select count(*) n from monthly_observations").fetchone()["n"], before)
            self.assertFalse(c.execute("select exists(select 1 from source_evidence where id=%s) e", (bad["sources"][1]["id"],)).fetchone()["e"])

    def test_OS06_atomic_replacement_rejects_stale_pages_and_reuses_no_old_source(self):
        next_source = package("second", "99")
        with self.lab.connect() as writer, self.lab.connect() as reader:
            actor(writer)
            actor(reader, VIEW_A, "authenticated")
            before = context(reader)
            original = records(reader, before)
            with writer.transaction():
                loaded = self.lab.load(writer, next_source, PROFILES, expected_generation=before["generation"],
                                       request_id="synthetic-official-replacement")
                self.assertEqual(context(reader), before)
                self.assertEqual(records(reader, before), original)
            after = context(reader)
            self.assertEqual(after["source_hash"], next_source["source_hash"])
            self.assertEqual(after["generation"], before["generation"] + 1)
            for query in ("operational_read_observations", "operational_read_inventory", "operational_read_periods", "operational_read_fill_rate"):
                with self.subTest(query=query), self.assertRaisesRegex(psycopg.errors.SerializationFailure, "OFFICIAL_SOURCE_CHANGED"):
                    reader.execute(sql.SQL("select * from {}(%s,%s)").format(sql.Identifier(query)),
                                   (before["source_hash"], before["generation"]))
            current = records(reader, after)
            self.assertFalse({r["id"] for r in current} & {r["id"] for r in original})
            self.assertIn(Decimal(99), [r["value"] for r in current if r["metric_code"] == "SALES"])
            self.assertEqual(len(current), len(original))
            repeated = self.lab.load(writer, next_source, PROFILES, expected_generation=after["generation"],
                                     request_id="synthetic-official-repeat")
            self.assertTrue(repeated["unchanged"])
            with self.assertRaisesRegex(ValueError, "official_source_generation_conflict"):
                self.lab.load(writer, self.payload, PROFILES, expected_generation=before["generation"],
                              request_id="synthetic-stale-source")
            self.assertEqual(loaded["generation"], after["generation"])

    def test_OS07_package_rejects_duplicate_cross_source_and_unknown_precision(self):
        for change in ("duplicate", "nan", "ce", "source"):
            invalid = copy.deepcopy(self.payload)
            if change == "duplicate":
                invalid["observations"].append(dict(invalid["observations"][0]))
            elif change == "nan":
                invalid["observations"][0]["value"] = "NaN"
            elif change == "ce":
                invalid["observations"][0]["purchase_type"] = "CE"
            else:
                invalid["observations"][0]["source_id"] = uid("unknown-source")
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_package(invalid)

    def test_OS08_stored_results_are_visible_only_for_current_source(self):
        with self.lab.connect() as c:
            actor(c)
            pinned = context(c)
            current_tag = {"source_hash": pinned["source_hash"], "generation": pinned["generation"]}
            old_id, current_id = uid("old-run"), uid("official-run")
            for rid, source in ((old_id, {}), (current_id, current_tag)):
                c.execute("""insert into forecast_runs(id,run_code,chain_id,objective,issue_period,cutoff_at,status,
                    data_snapshot_hash,engine_version,git_sha,source_context)
                    values(%s,%s,%s,'Venta','2026-10-01','2026-10-31','COMPLETED',%s,'synthetic','synthetic',%s::jsonb)""",
                    (rid, rid, CHAINS[0], "1"*64, json.dumps(source)))
            for number, source in ((1, {}), (2, current_tag)):
                c.execute("""insert into forecast_calculations
                    (id,chain_id,product_id,objective,calculation_no,calculation_code,issue_period,cutoff_at,preview_id,
                     data_snapshot_hash,content_hash,input_snapshot,candidate_metrics,evidence_mode,engine_version,git_sha,
                     created_by,idempotency_key,request_hash)
                    values(%s,%s,%s,'Venta',%s,%s,'2026-10-01','2026-10-31','synthetic',%s,%s,%s::jsonb,
                      '{}','RETROSPECTIVE_TRAINING','synthetic','synthetic',%s,%s,%s)""",
                    (uid("calculation-"+str(number)), CHAINS[0], next(iter(PRODUCTS)), number, "C"+str(number),
                     "1"*64,"2"*64,json.dumps({"source_context":source}),ADMIN,"key-"+str(number),"3"*64))
            actor(c, VIEW_A, "authenticated")
            self.assertEqual([str(r["id"]) for r in c.execute("select id from forecast_runs")], [current_id])
            self.assertEqual([r["calculation_code"] for r in c.execute("select calculation_code from forecast_calculations")], ["C2"])
            c.execute("reset role")
            c.execute("set role service_role")
            for relation in ("forecast_runs", "forecast_calculations", "forecast_vintages"):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    c.execute(sql.SQL("select * from {}").format(sql.Identifier(relation)))

    def test_OS09_existing_calculation_rpc_saves_source_tag_and_twelve_horizons(self):
        with self.lab.connect() as c:
            actor(c)
            pinned = context(c)
            observation = c.execute("""select o.id::text from monthly_observations o
                join private.official_observation_source p on p.observation_id=o.id
                join import_batches b on b.id=o.source_batch_id where b.official_source_hash=%s
                and o.chain_id=%s limit 1""", (pinned["source_hash"], CHAINS[0])).fetchone()["id"]
            source_context = {name: pinned[name] for name in ("source_hash", "generation")}
            horizons = []
            for h in range(1, 13):
                year, month = divmod(2026 * 12 + 9 + h, 12)
                horizons.append({"horizon": h, "target_period": f"{year:04}-{month+1:02}-01",
                                 "statistical_value": 25, "statistical_model": "Synthetic test method",
                                 "p50": 25, "band_basis": "INSUFFICIENT", "band_observations": 0,
                                 "band_status": "INSUFFICIENT_BAND_EVIDENCE"})
            payload = {"actor_id": ADMIN, "chain_id": CHAINS[0], "product_id": next(iter(PRODUCTS)),
                       "objective": "Venta", "issue_period": "2026-10-01", "cutoff_at": "2026-10-31T23:59:59Z",
                       "source_context": source_context, "content_hash": "4"*64, "data_snapshot_hash": "5"*64,
                       "preview_id": "SYNTHETIC", "idempotency_key": "source-rpc-test", "candidates": {},
                       "product_snapshot": {}, "input_observation_ids": [observation], "horizons": horizons,
                       "evidence_mode": "RETROSPECTIVE_TRAINING", "engine_version": "synthetic", "git_sha": "synthetic",
                       "recalculation_reason": "Synthetic test"}
            c.execute("set role service_role")
            with self.assertRaisesRegex(psycopg.errors.SerializationFailure, "OFFICIAL_SOURCE_CHANGED"):
                c.execute("select e4_create_calculation(%s::jsonb)", (json.dumps({**payload, "source_context": {}}),))
            created = c.execute("select e4_create_calculation(%s::jsonb) id", (json.dumps(payload),)).fetchone()["id"]
            repeated = c.execute("select e4_create_calculation(%s::jsonb) id", (json.dumps(payload),)).fetchone()["id"]
            self.assertEqual(created, repeated)
            selection = {"actor_id": ADMIN, "calculation_id": str(created), "selected_candidate": "STATISTICAL",
                         "decision_reason": "Experiencia comercial", "idempotency_key": "synthetic-current-selection"}
            with self.assertRaisesRegex(psycopg.errors.SerializationFailure, "OFFICIAL_CALCULATION_SOURCE_INVALID"):
                c.execute("select e4_select_forecast(%s::jsonb)",
                          (json.dumps({**selection,"calculation_id":uid("calculation-1")}),))
            chosen = c.execute("select e4_select_forecast(%s::jsonb) id", (json.dumps(selection),)).fetchone()["id"]
            self.assertEqual(c.execute("select e4_select_forecast(%s::jsonb) id", (json.dumps(selection),)).fetchone()["id"],chosen)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute("update forecast_runs set source_context='{}'::jsonb")
            actor(c, VIEW_A, "authenticated")
            saved = c.execute("select input_snapshot from forecast_calculations where id=%s", (created,)).fetchone()
            self.assertEqual(saved["input_snapshot"]["source_context"], source_context)
            self.assertEqual(c.execute("select count(*) n from forecast_calculation_horizons where calculation_id=%s", (created,)).fetchone()["n"], 12)
            self.assertEqual(c.execute("select id from current_forecast_selection where calculation_id=%s",(created,)).fetchone()["id"],chosen)

    def test_OS10_existing_vintage_rpc_saves_source_tag_and_readable_model(self):
        with self.lab.connect() as c:
            actor(c)
            pinned = context(c)
            observation = c.execute("""select o.id::text from monthly_observations o
                join private.official_observation_source p on p.observation_id=o.id
                join import_batches b on b.id=o.source_batch_id where b.official_source_hash=%s
                and o.chain_id=%s and o.metric_code='SALES' limit 1""", (pinned["source_hash"], CHAINS[0])).fetchone()["id"]
            source_context = {name: pinned[name] for name in ("source_hash", "generation")}
            horizons, aggregates = [], []
            for h in range(1, 13):
                year, month = divmod(2026 * 12 + 9 + h, 12)
                period = f"{year:04}-{month+1:02}-01"
                horizons.append({"horizon": h, "target_period": period, "product_id": next(iter(PRODUCTS)),
                                 "statistical_value": 25, "forecast_towell": 25, "model_strategy": "synthetic",
                                 "forecast_status": "PROVISIONAL"})
                aggregates.append({"level": "CHAIN", "horizon": h, "target_period": period, "forecast_towell": 25})
            payload = {"actor_id": ADMIN, "chain_id": CHAINS[0], "objective": "Venta", "issue_period": "2026-10-01",
                       "cutoff_at": "2026-10-31T23:59:59Z", "source_context": source_context,
                       "content_hash": "6"*64, "data_snapshot_hash": "7"*64, "certification_status": "PROVISIONAL",
                       "evidence_mode": "RETROSPECTIVE_TRAINING", "engine_version": "synthetic", "git_sha": "synthetic",
                       "inputs": [{"observation_id": observation}], "horizons": horizons, "aggregates": aggregates,
                       "models": [{"family": "STATISTICAL", "algorithm": "Synthetic method"}], "metrics": [],
                       "gates": [{"gate_type": name, "status": "PASS", "details": {}} for name in
                                 ("DATA_QUALITY", "FORECAST_QUALITY", "SERVICE_LEVEL")],
                       "policy_version": "SYNTHETIC"}
            c.execute("set role service_role")
            created = c.execute("select e3_create_vintage_candidate(%s::jsonb) id", (json.dumps(payload),)).fetchone()["id"]
            repeated = c.execute("select e3_create_vintage_candidate(%s::jsonb) id", (json.dumps(payload),)).fetchone()["id"]
            self.assertEqual(created, repeated)
            actor(c, VIEW_A, "authenticated")
            saved = c.execute("""select r.source_context,r.id from forecast_vintages v
                join forecast_runs r on r.id=v.run_id where v.id=%s""", (created,)).fetchone()
            self.assertEqual(saved["source_context"], source_context)
            self.assertEqual(c.execute("select count(*) n from model_versions where run_id=%s", (saved["id"],)).fetchone()["n"], 1)
            self.assertEqual(c.execute("select count(*) n from forecast_horizons where vintage_id=%s", (created,)).fetchone()["n"], 12)

    def test_OS11_authorized_supplied_connection_reuses_tested_importer(self):
        new_source = package("third", "71")
        with self.lab.connect() as c:
            actor(c)
            before = context(c)
            target = {
                "expected_host": "127.0.0.1", "expected_database": self.lab.config["database"],
                "expected_user": "postgres", "project_ref": "SYNTHETIC", "source_hash": new_source["source_hash"],
                "expected_generation": before["generation"], "local_rehearsal": True,
            }
            with self.assertRaisesRegex(ValueError, "official_activation_authorization_required"):
                AuthorizedOfficialConnection(c, **target, authorization="not-authorized")
            writer = AuthorizedOfficialConnection(c, **target,
                authorization=f"ACTIVATE_OFFICIAL_SOURCE:{new_source['source_hash']}:{before['generation']}")
            with self.assertRaisesRegex(ValueError, "official_activation_generation_mismatch"):
                writer.load(c,new_source,PROFILES,expected_generation=before["generation"]+1,request_id="wrong-generation")
            class RollbackFixture(Exception):
                pass
            with self.assertRaises(RollbackFixture), c.transaction():
                loaded = writer.load(c,new_source,PROFILES,expected_generation=before["generation"],request_id="approved-connection")
                self.assertEqual(loaded["generation"], before["generation"]+1)
                self.assertEqual(context(c)["source_hash"], new_source["source_hash"])
                raise RollbackFixture()
            self.assertEqual(context(c), before)

    def test_OS12_prepared_cleanup_follows_foreign_keys_and_retains_current_source(self):
        with self.lab.connect() as c:
            actor(c)
            pinned = context(c)
            current_ids = {r["id"] for r in records(c, pinned)}
            products = c.execute("select count(*) n from products").fetchone()["n"]
            plan = retirement_plan(self.lab,c)
            self.assertIn("public.monthly_observations",plan["rows"])
            self.assertGreater(plan["retained_catalog_creation_batches"],0)
            script = render_cleanup(plan)
            report_dir = os.environ.get("OFFICIAL_SOURCE_REPORT_DIR")
            if report_dir:
                output = Path(report_dir).resolve()
                if output.is_relative_to(REPO):
                    raise ValueError("official_report_requires_private_output")
                output.mkdir(parents=True,exist_ok=True)
                (output/"synthetic_retirement_plan.json").write_text(json.dumps(plan,indent=2),encoding="utf-8")
                (output/"synthetic_retirement.sql").write_text(script,encoding="utf-8")
            self.assertNotIn(" cascade",script.lower())
            with self.assertRaisesRegex(psycopg.Error, "OFFICIAL_CLEANUP_AUTHORIZATION_REQUIRED"):
                c.execute(script)
            c.execute("rollback")
            self.lab.maintenance(c,pinned["source_hash"])
            c.execute("select set_config('towell.official_cleanup_authorization',%s,false)",
                      ("DESTROY_RETIRED_SOURCE_DATA:"+plan["plan_sha256"],))
            c.execute(script)
            self.assertEqual(context(c),pinned)
            self.assertEqual({r["id"] for r in records(c,pinned)},current_ids)
            self.assertEqual(c.execute("select count(*) n from products").fetchone()["n"],products)
            self.assertEqual(c.execute("select count(*) n from monthly_observations where period<'2025-01-01'").fetchone()["n"],0)

    def test_OS13_installation_and_authorized_cleanup_remove_existing_ab_structures(self):
        name = "gateb_official_" + uuid.uuid4().hex[:12]
        lab = OfficialSourceLab({**self.root.config, "database": name})
        with self.root.connect() as c:
            c.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
        try:
            lab.initialize()
            seed(lab,stage_candidates=False,commercial_profiles=True)
            source = package("old-ab-retirement")
            lab.install(source["source_hash"])
            with lab.connect() as c:
                actor(c)
                lab.load(c,source,PROFILES,expected_generation=0,request_id="replace-ab-fixture")
                plan = retirement_plan(lab,c)
                self.assertIn("public.operational_data_versions",plan["retired_tables"])
                self.assertIn("public.operational_data_active",plan["retired_tables"])
                lab.maintenance(c,source["source_hash"])
                c.execute("select set_config('towell.official_cleanup_authorization',%s,false)",
                          ("DESTROY_RETIRED_SOURCE_DATA:"+plan["plan_sha256"],))
                c.execute(render_cleanup(plan))
                self.assertIsNone(c.execute("select to_regclass('public.operational_data_versions') t").fetchone()["t"])
                self.assertIsNone(c.execute("select to_regclass('public.operational_data_active') t").fetchone()["t"])
                actor(c,VIEW_A,"authenticated")
                self.assertEqual(len(records(c,context(c))),20)
        finally:
            with self.root.connect() as c:
                c.execute(sql.SQL("drop database {}").format(sql.Identifier(name)))


if __name__ == "__main__":
    unittest.main()
