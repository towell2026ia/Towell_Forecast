"""Validate the private A/B package before any database connection."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from uuid import UUID, NAMESPACE_URL, uuid5

from .local import METRICS, quantity


def file_sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def rows(content):
    with io.TextIOWrapper(io.BytesIO(content), encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream)


def key(row):
    return row["chain_id"], row["product_id"], row["period"], row["metric_code"]


def scoped_month(row):
    chain, product, period = (row[name] for name in ("chain_id", "product_id", "period"))
    if (str(UUID(chain)) != chain or str(UUID(product)) != product
            or not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", period)):
        raise ValueError("invalid_archive_scope_month")
    return chain, product, period


def index_observations(records, products):
    by_key, ids = {}, set()
    for row in records:
        chain, product, _ = scoped_month(row)
        k, oid = key(row), row["observation_id"]
        if (products.get(product) != chain or row["metric_code"] not in METRICS
                or str(UUID(oid)) != oid or oid in ids or k in by_key
                or int(row["version_no"]) <= 0
                or row["availability_source"] not in {"UNKNOWN", "SYSTEM_INGESTION", "E1", "E2", "E3"}
                or bool(row["available_at"]) == (row["availability_source"] == "UNKNOWN")):
            raise ValueError("invalid_archive_observation")
        quantity(row["value"])
        by_key[k] = row
        ids.add(oid)
    return by_key


@dataclass(frozen=True)
class CutoverArchive:
    candidate_id: str
    sha256: str
    catalog: dict
    baseline: dict
    candidate: dict
    changes: dict
    inventory: list

    @property
    def version_id(self):
        return str(uuid5(NAMESPACE_URL, "towell-operational-release:" + self.sha256))

    def summary(self):
        return {
            "candidate_id": self.candidate_id, "manifest_sha256": self.sha256,
            "observations_a": len(self.baseline), "observations_b": len(self.candidate),
            "physical_inserts": len(self.changes), "inventory": len(self.inventory),
            "new_batches": len({(k[0], k[2]) for k in self.changes}),
            "changes_by_metric": dict(sorted(Counter(k[3] for k in self.changes).items())),
        }


def read_archive(root, manifest_sha):
    root = Path(root)
    manifest_content = (root / "Manifest.json").read_bytes()
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha) or hashlib.sha256(manifest_content).hexdigest() != manifest_sha:
        raise ValueError("candidate_manifest_sha_mismatch")
    manifest = json.loads(manifest_content)
    if manifest["status"] != "PRIVATE_NOT_ACTIVATED":
        raise ValueError("unexpected_candidate_status")
    required = {"Observaciones_A.csv", "Observaciones_B.csv", "Cambios_B.csv",
                "Inventario_B.csv", "Catalogo_completo.json"}
    files = manifest["results_sha256"]
    if not required <= files.keys():
        raise ValueError("candidate_artifacts_missing")
    verified = {}
    for name, expected in files.items():
        if Path(name).name != name:
            raise ValueError("candidate_artifact_sha_mismatch")
        path = root / name
        if name in required:
            verified[name] = path.read_bytes()
            actual = hashlib.sha256(verified[name]).hexdigest()
        else:
            actual = file_sha(path)
        if actual != expected:
            raise ValueError("candidate_artifact_sha_mismatch")
    catalog = json.loads(verified["Catalogo_completo.json"])
    chains = {row["id"] for row in catalog["chains"]}
    products = {row["id"]: row["chain_id"] for row in catalog["products"]}
    if (len(chains) != len(catalog["chains"]) or len(products) != len(catalog["products"])
            or len(chains) != manifest["scope_count"] or len(products) != manifest["catalog_count"]
            or set(products.values()) - chains):
        raise ValueError("candidate_catalog_drift")
    baseline = index_observations(rows(verified["Observaciones_A.csv"]), products)
    candidate = index_observations(rows(verified["Observaciones_B.csv"]), products)
    if not baseline.keys() <= candidate.keys() or len(baseline) != manifest["observations_a"] or len(candidate) != manifest["observations_b"]:
        raise ValueError("candidate_membership_drift")
    baseline_ids = {row["observation_id"] for row in baseline.values()}
    changes = {}
    for row in rows(verified["Cambios_B.csv"]):
        k = key(row)
        prior, new = baseline.get(k), candidate.get(k)
        if (k in changes or not new or row["status"] != ("replaced" if prior else "new")
                or not row["referencia_origen"] or row["tipo_compra"] != "RS"
                or new["observation_id"] != row["observation_id"] or new["observation_id"] in baseline_ids
                or new["value"] != row["value"] or new["availability_source"] != "UNKNOWN" or new["available_at"]
                or new["period"] < "2025-01" or new["period"] > manifest["cutoff"]
                or row["previous_id"] != (prior["observation_id"] if prior else "")
                or (prior and (row["previous_value"] != prior["value"] or Decimal(prior["value"]) == Decimal(new["value"])))
                or int(new["version_no"]) != (int(prior["version_no"]) + 1 if prior else 1)):
            raise ValueError("invalid_candidate_change")
        changes[k] = row
    if changes.keys() != {k for k, row in candidate.items() if row != baseline.get(k)}:
        raise ValueError("candidate_change_coverage_mismatch")
    inventory, scoped_ids, product_ids = [], set(), set()
    for row in rows(verified["Inventario_B.csv"]):
        chain, product, period = scoped_month(row)
        sku, scope = (chain, product, period), (chain, row["formato"], row["upc"], period)
        if (products.get(product) != chain or sku in product_ids or scope in scoped_ids
                or row["tipo_compra"] != "RS" or not row["formato"] or not row["referencia_origen"]
                or not re.fullmatch(r"[0-9]+", row["upc"])
                or period < "2025-01" or period > manifest["cutoff"]):
            raise ValueError("invalid_candidate_inventory")
        quantity(row["value"])
        inventory.append(row)
        scoped_ids.add(scope)
        product_ids.add(sku)
    if len(inventory) != manifest["inventory_rows"]:
        raise ValueError("candidate_inventory_coverage_mismatch")
    return CutoverArchive(manifest["candidate_id"], manifest_sha, catalog, baseline, candidate, changes, inventory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(read_archive(args.directory, args.manifest_sha256).summary(), sort_keys=True))


if __name__ == "__main__":
    main()
