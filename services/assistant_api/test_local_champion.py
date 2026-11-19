"""Synthetic local-only publication, source/scope guards and durable recovery."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import httpx

from services.assistant_api.auth import AuthFailure, Principal, ROLE_PERMISSIONS
from services.assistant_api.local_champion import LocalChampionService
from services.assistant_api.persistence import LocalPersistenceProvider, content_hash
from services.assistant_api.preview_api import mount_preview_routes
from services.assistant_api.supabase_access import PreviewError, UNIFIED_MASTER_SHA256

CHAIN = "00000000-0000-4000-8000-000000000001"
PRODUCT = "00000000-0000-4000-8000-000000000002"
SECOND = "00000000-0000-4000-8000-000000000003"
ACTOR = "00000000-0000-4000-8000-000000000004"
RUN = "00000000-0000-4000-8000-000000000005"


def context():
    return {"source_hash": "a" * 64, "generation": 5, "first_period": "2024-01", "last_period": "2026-10",
        "forecast_contract": "pedido-unificado-v1", "objective": "Pedido", "target_metric": "ORDER",
        "target_sheet": "Base_2024_2026", "target_file_sha256": UNIFIED_MASTER_SHA256,
        "authorized_source_hash": "a" * 64, "training_cutoff": "2026-09", "forecast_available": True,
        "target_source_available": True, "forecast_block_code": None, "inventory_source": "ESTADISTICA",
        "unit": "PIEZAS", "planning_start": "2027-01", "planning_end": "2027-12"}


def actor(role="manager", scope=True):
    return Principal(ACTOR, role, ROLE_PERMISSIONS[role], "synthetic-session", frozenset({CHAIN}) if scope else frozenset(),
                     frozenset({CHAIN}) if scope and role != "reader" else frozenset())


def catalogue():
    return [{"product_id": product_id, "chain_id": CHAIN, "purchase_type": "RS"} for product_id in (PRODUCT, SECOND)]


def run():
    products = []
    for product_id in (PRODUCT, SECOND):
        candidates = {}
        for family, value in (("STATISTICAL", "10.10"), ("ML", "20.20"), ("ENSEMBLE", "15.15")):
            candidates[family] = {"family": family, "model": "Synthetic " + family, "origin": "PYTHON_EXISTING_ENGINE",
                "eligible": True, "status": "READY", "metrics": {}, "horizons": [
                    {"planning_horizon": month, "horizon": month, "forecast_distance": month + 3,
                     "target_period": f"2027-{month:02}", "value": value,
                     "statistical_weight": "0.5", "ml_weight": "0.5"} for month in range(1, 13)]}
        products.append({"product_id": product_id, "chain_id": CHAIN, "identity_key": product_id,
                         "model_group_key": "SYNTHETIC", "candidates": candidates})
    return {"schema": "unified-planning-run-1", "run_id": RUN, "source_context": context(),
        "objective": "Pedido", "planning_year": 2027, "cutoff": "2026-09", "emitted_at": "2026-10-01T00:00:00+00:00",
        "products": products}


def save_run(folder, payload):
    payload = deepcopy(payload)
    payload["content_digest"] = content_hash(payload)
    path = Path(folder) / "planning_run.json"
    raw = json.dumps(payload, ensure_ascii=False).encode()
    path.write_bytes(raw)
    return {"enabled": True, "path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def request(family="STATISTICAL", products=None):
    return {"run_id": RUN, "candidate_family": family, "planning_year": 2027, "chain_ids": [CHAIN],
        "product_ids": products or [PRODUCT, SECOND], "source_context": context(), "confirm": True,
        "comment": "Synthetic explicit selection"}


class Identity:
    def authenticate(self, *, token, **kwargs):
        if token not in {"manager", "reader", "editor", "foreign"}:
            raise AuthFailure("AUTH_001", 401)
        return actor(token if token != "foreign" else "manager", scope=token != "foreign")


def app_fixture(folder):
    reference = json.loads((Path(folder) / "reference.json").read_text())
    storage = LocalPersistenceProvider(Path(folder) / "state.sqlite")
    app = FastAPI()

    @app.exception_handler(PreviewError)
    async def failure(_request, error):
        return JSONResponse(status_code=error.status, content={"error_code": error.code})

    class Data:
        client = None

        def bind(self, _token):
            self.client = self
            return self

        def source_context(self):
            return context()

        def catalog_rows(self):
            return catalogue()

    config = SimpleNamespace(operational_preview_enabled=True, local_postgres_config=Path(folder) / "local.json",
        app_env="test", api_host="127.0.0.1", railway_runtime=False)
    service = LocalChampionService(storage, reference)
    mount_preview_routes(app, config, Identity(), Data(), None, None, local_champion=service)
    return app


class LocalChampionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.storage = LocalPersistenceProvider(self.folder / "state.sqlite")
        self.reference = save_run(self.folder, run())
        (self.folder / "reference.json").write_text(json.dumps(self.reference))
        self.service = LocalChampionService(self.storage, self.reference)

    def publish(self, body=None, key="one", principal=None):
        return self.service.publish(body or request(), principal or actor(), context(), catalogue(), idempotency_key=key)

    def current(self, principal=None, current=None):
        return self.service.current(principal or actor(), current or context(), catalogue(), planning_year=2027,
                                    chain_ids=[CHAIN], product_ids=[PRODUCT, SECOND])

    def test_explicit_family_server_amounts_and_atomic_idempotency(self):
        self.assertEqual(self.current()["covered"], 0)
        event = self.publish()
        self.assertEqual(len(event["products"]), 2)
        self.assertEqual([row["target_period"] for row in event["products"][0]["horizons"]], [f"2027-{m:02}" for m in range(1, 13)])
        self.assertEqual(event["products"][0]["horizons"][0]["value"], "10.10")
        self.assertEqual(event["selected_by"], ACTOR)
        self.assertEqual(event["run_emitted_at"], "2026-10-01T00:00:00+00:00")
        self.assertFalse(event["ft_decision_changed"])
        self.assertFalse(event["recommendation_changed"])
        self.assertEqual(self.publish()["id"], event["id"])
        self.assertEqual(len(self.storage.list("champion_history")), 1)
        self.assertEqual(len(self.storage.list("champion_registry")), 2)
        with self.assertRaisesRegex(PreviewError, "CHAMPION_IDEMPOTENCY_CONFLICT"):
            self.publish(request("ML"))
        self.assertEqual(self.current()["covered"], 2)
        self.assertEqual(self.storage.list("forecast_vintages"), [])

    def test_overlapping_group_and_variant_share_single_current_pointer(self):
        group = self.publish()
        variant = self.publish(request("ML", [PRODUCT]), key="two")
        result = {row["product"]["product_id"]: row for row in self.current()["products"]}
        self.assertEqual(result[PRODUCT]["id"], variant["id"])
        self.assertEqual(result[SECOND]["id"], group["id"])
        self.assertEqual(len(result), 2)
        self.assertEqual(variant["previous_champion_ids"][PRODUCT], group["id"])
        self.assertEqual(self.publish()["id"], group["id"])
        self.assertEqual(self.current()["products"][0]["id"], variant["id"])

    def test_roles_scope_and_exact_product_binding(self):
        for principal in (actor("reader"), actor("editor"), actor(scope=False)):
            with self.assertRaises(PreviewError):
                self.publish(principal=principal)
        for body in ({**request(), "product_ids": [PRODUCT, PRODUCT]},
                     {**request(), "chain_ids": [SECOND]}, {**request(), "product_ids": [ACTOR]},
                     {**request(), "confirm": False}):
            with self.assertRaises(PreviewError):
                self.publish(body)
        self.assertEqual(self.storage.list("champion_history"), [])
        self.publish()
        with self.assertRaises(PreviewError):
            self.current(actor(scope=False))
        self.assertEqual(self.current(actor("reader"))["covered"], 2)

    def test_other_source_objective_year_cutoff_or_absent_plan_never_falls_back(self):
        self.publish()
        for change in ({"source_hash": "b" * 64, "authorized_source_hash": "b" * 64},
                       {"generation": 6}):
            self.assertEqual(self.current(current={**context(), **change})["covered"], 0)
        for body in ({**request(), "planning_year": 2028}, {**request(), "run_id": ACTOR},
                     {**request(), "source_context": {**context(), "target_file_sha256": "f" * 64}},
                     request("COMMERCIAL_PLAN")):
            with self.assertRaises(PreviewError):
                self.publish(body, key="bad")
        with self.assertRaises(PreviewError):
            self.current(current={**context(), "objective": "Venta"})

    def test_trusted_file_hash_and_content_digest(self):
        path = Path(self.reference["path"])
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(PreviewError, "CHAMPION_RUN_UNAVAILABLE"):
            self.publish()
        payload = run()
        payload["content_digest"] = "f" * 64
        raw = json.dumps(payload).encode()
        path.write_bytes(raw)
        self.service.reference["sha256"] = hashlib.sha256(raw).hexdigest()
        with self.assertRaisesRegex(PreviewError, "CHAMPION_RUN_UNAVAILABLE"):
            self.publish()

    def test_missing_negative_duplicate_ineligible_or_wrong_month_curve_blocks_entire_selection(self):
        for mutation in ("missing", "negative", "duplicate", "ineligible", "wrong_month", "wrong_distance", "nan"):
            payload = run()
            candidate = payload["products"][1]["candidates"]["STATISTICAL"]
            if mutation == "missing":
                candidate["horizons"].pop()
            if mutation == "negative":
                candidate["horizons"][0]["value"] = "-1"
            if mutation == "duplicate":
                candidate["horizons"][1] = deepcopy(candidate["horizons"][0])
            if mutation == "ineligible":
                candidate["eligible"] = False
            if mutation == "wrong_month":
                candidate["horizons"][0]["target_period"] = "2026-10"
            if mutation == "wrong_distance":
                candidate["horizons"][0]["forecast_distance"] = 1
            if mutation == "nan":
                candidate["horizons"][0]["value"] = "NaN"
            self.service.reference = save_run(self.folder, payload)
            with self.assertRaisesRegex(PreviewError, "CHAMPION_CANDIDATE_INELIGIBLE"):
                self.publish()
            self.assertEqual(self.storage.list("champion_history"), [])
            self.assertEqual(self.storage.list("champion_registry"), [])

    def test_persistence_survives_a_fresh_process(self):
        selected = self.publish()
        code = """import json,sys
from pathlib import Path
from services.assistant_api.test_local_champion import context,actor,catalogue,CHAIN,PRODUCT,SECOND
from services.assistant_api.persistence import LocalPersistenceProvider
from services.assistant_api.local_champion import LocalChampionService
folder=Path(sys.argv[1]); ref=json.loads((folder/'reference.json').read_text())
result=LocalChampionService(LocalPersistenceProvider(folder/'state.sqlite'),ref).current(actor('reader'),context(),catalogue(),planning_year=2027,chain_ids=[CHAIN],product_ids=[PRODUCT,SECOND])
print(json.dumps({'covered':result['covered'],'id':result['products'][0]['id'],'value':result['products'][0]['product']['horizons'][0]['value']}))
"""
        process = subprocess.run([sys.executable, "-c", code, str(self.folder)], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(process.stdout), {"covered": 2, "id": selected["id"], "value": "10.10"})

    def test_failed_pointer_write_rolls_back_the_history_and_every_product(self):
        with self.storage._connect() as connection:
            connection.execute("""CREATE TRIGGER synthetic_failure BEFORE INSERT ON champion_registry
                BEGIN SELECT RAISE(ABORT,'synthetic_failure'); END""")
        with self.assertRaisesRegex(PreviewError, "CHAMPION_PERSISTENCE_FAILED"):
            self.publish()
        self.assertEqual(self.storage.list("champion_history"), [])
        self.assertEqual(self.storage.list("champion_registry"), [])

    def test_concurrent_identical_requests_create_one_immutable_event(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as executor:
            events = list(executor.map(lambda _: self.publish(), range(4)))
        self.assertEqual(len({row["id"] for row in events}), 1)
        self.assertEqual(len(self.storage.list("champion_history")), 1)
        self.assertEqual(self.current()["covered"], 2)

    def test_http_contract_roles_client_quantities_and_hosted_body_blocked(self):
        with TestClient(app_fixture(self.folder)) as client:
            url = "/api/forecast/champion/promote"
            headers = {"Authorization": "Bearer manager", "Idempotency-Key": "explicit"}
            self.assertEqual(client.post(url, json=request()).status_code, 401)
            for role in ("reader", "editor", "foreign"):
                self.assertEqual(client.post(url, json=request(), headers={**headers, "Authorization": "Bearer " + role}).status_code, 403)
            self.assertEqual(client.post(url, json={**request(), "quantities": [999]}, headers=headers).status_code, 422)
            self.assertEqual(client.post(url, json={"vintage_id": RUN, "model_version_id": ACTOR,
                "comment": "Hosted flow is different", "confirm": True}, headers=headers).status_code, 422)
            result = client.post(url, json=request(), headers=headers)
            self.assertEqual(result.status_code, 200, result.text)
            read = client.get("/api/forecast/champion", params=[("planning_year", "2027"), ("chain_ids", CHAIN),
                ("product_ids", PRODUCT)], headers={"Authorization": "Bearer reader"})
            self.assertEqual(read.status_code, 200, read.text)
            self.assertEqual(read.json()["covered"], 1)
            self.assertEqual(len(read.json()["products"]), 1)
            self.assertEqual(read.json()["products"][0]["product"]["product_id"], PRODUCT)

    def test_real_http_restart_reads_the_same_synthetic_champion(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        code = """import sys,uvicorn
from services.assistant_api.test_local_champion import app_fixture
uvicorn.run(app_fixture(sys.argv[1]),host='127.0.0.1',port=int(sys.argv[2]),log_level='critical',access_log=False)
"""
        params = [("planning_year", "2027"), ("chain_ids", CHAIN), ("product_ids", PRODUCT)]
        def start():
            import time
            process = subprocess.Popen([sys.executable, "-c", code, str(self.folder), str(port)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(100):
                if process.poll() is not None:
                    self.fail("synthetic_server_start_failed")
                try:
                    response = httpx.get(f"http://127.0.0.1:{port}/api/forecast/champion", params=params,
                        headers={"Authorization": "Bearer reader"}, timeout=.2)
                    if response.status_code == 200:
                        return process, response.json()
                except httpx.HTTPError:
                    pass
                time.sleep(.05)
            process.terminate()
            process.wait(timeout=5)
            self.fail("synthetic_server_not_ready")
        first, empty = start()
        try:
            self.assertEqual(empty["covered"], 0)
            response = httpx.post(f"http://127.0.0.1:{port}/api/forecast/champion/promote", json=request(),
                headers={"Authorization": "Bearer manager", "Idempotency-Key": "restart"}, timeout=3)
            self.assertEqual(response.status_code, 200)
            champion_id = response.json()["champion_id"]
        finally:
            first.terminate()
            first.wait(timeout=5)
        restarted, current = start()
        try:
            self.assertEqual(current["covered"], 1)
            self.assertEqual(current["products"][0]["id"], champion_id)
            self.assertEqual(current["products"][0]["product"]["horizons"][0]["value"], "10.10")
        finally:
            restarted.terminate()
            restarted.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
