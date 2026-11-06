"""Conservative offline reconciliation of private SKU identities to catalog products."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


ATTRIBUTES = ("model", "presentation", "color", "commercial_code")


def label(value: str | None) -> str:
    """Normalize typography only; do not invent commercial synonyms."""
    return "".join(c for c in unicodedata.normalize("NFKD", value or "").upper() if c.isalnum())


def group_key(row: dict) -> tuple[str, ...]:
    return (
        row["account"], row["chain"], row["format"], label(row["model"]),
        label(row["size"]), row["commercial_code"] or row["id"],
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def reconcile(rows: list[dict], review: list[dict], current: dict, accreditation: dict | None = None):
    """Return per-identity results; only structured accredited evidence can verify."""
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("DUPLICATE_PRIVATE_IDENTITY")
    if len(review) != len(rows) or [r["identity"] for r in review] != [r["id"] for r in rows]:
        raise ValueError("REVIEW_BASELINE_MISMATCH")
    for r, prior in zip(rows, review):
        if any(r[src] != prior[dst] for src, dst in (("account", "account"), ("barcode", "upc"), ("chain", "chain"), ("format", "format"), ("model", "model"), ("size", "presentation"), ("color", "color"), ("commercial_code", "commercial_code"))):
            raise ValueError("REVIEW_SOURCE_CHANGED")

    accreditation = accreditation or {}
    scopes = defaultdict(list)
    for entry in accreditation.get("account_scopes", []):
        if entry.get("status") == "ACCREDITED" and entry.get("evidence"):
            scopes[(entry["account"], entry["chain_id"], entry["scope"])].append(entry)
    attributes = defaultdict(list)
    for entry in accreditation.get("product_attributes", []):
        if entry.get("status") == "ACCREDITED" and entry.get("evidence"):
            attributes[(entry["product_id"], entry["chain_id"])].append(entry)
    live = {(p[0], p[1]): p for p in current["products"]}
    results = []
    for row in rows:
        candidates = row["candidates"]
        base = {"identity": row["id"], "account": row["account"], "chain": row["chain"],
                "format": row["format"], "upc": row["barcode"], "model": row["model"],
                "presentation": row["size"], "color": row["color"],
                "commercial_code": row["commercial_code"], "group_key": list(group_key(row)),
                "candidate_ids": [c["product_id"] for c in candidates], "verified_product_id": None,
                "verified_chain_id": None, "evidence": [], "differences": []}
        if not candidates:
            base.update(status="SIN_CORRESPONDENCIA", reason="UPC_NO_ENCONTRADO")
        elif len(candidates) > 1:
            base.update(status="AMBIGUO", reason="UPC_CON_MULTIPLES_PRODUCTOS")
        else:
            c = candidates[0]
            key = (c["product_id"], c["chain_id"])
            differences = []
            if key not in live or live[key][3] != "ACTIVE":
                differences.append("PRODUCTO_NO_ACTIVO_EN_CATALOGO")
            scope_matches = [s for s in scopes.get((row["account"], c["chain_id"], c["scope"]), [])
                             if label(s.get("format")) == label(row["format"])
                             and label(s.get("chain")) == label(row["chain"])]
            if len(scope_matches) != 1:
                differences.append("CUENTA_CADENA_FORMATO_NO_ACREDITADOS" if not scope_matches else "CUENTA_SCOPE_AMBIGUO")
            if not c.get("format_hint") and (len(scope_matches) != 1 or scope_matches[0].get("scope_equivalence") is not True):
                differences.append("FORMATO_SCOPE_TEXTUAL_DIFIERE")
            attribute_matches = attributes.get(key, [])
            if len(attribute_matches) != 1:
                differences.append("ATRIBUTOS_CATALOGO_NO_ACREDITADOS" if not attribute_matches else "ATRIBUTOS_CATALOGO_AMBIGUOS")
            else:
                att = attribute_matches[0]
                for field, original in (("model", row["model"]), ("presentation", row["size"]),
                                        ("color", row["color"]), ("commercial_code", row["commercial_code"])):
                    if field not in att:
                        differences.append(field.upper() + "_SIN_EVIDENCIA")
                    elif label(att.get(field)) != label(original):
                        differences.append(field.upper() + "_DIFIERE")
            if differences:
                base.update(status="REVISION_REQUERIDA", reason=";".join(differences), differences=differences)
            else:
                base.update(status="COINCIDENCIA_VERIFICADA", reason="UPC_SCOPE_ATRIBUTOS_ACREDITADOS",
                            verified_product_id=c["product_id"], verified_chain_id=c["chain_id"],
                            evidence=[scope_matches[0]["evidence"], attribute_matches[0]["evidence"]])
        results.append(base)

    # A target cannot represent two private identities, even if each match looks exact.
    by_target = defaultdict(list)
    for r in results:
        if r["status"] == "COINCIDENCIA_VERIFICADA":
            by_target[(r["verified_product_id"], r["verified_chain_id"])].append(r)
    for repeated in by_target.values():
        if len(repeated) > 1:
            for r in repeated:
                r.update(status="AMBIGUO", reason="OBJETIVO_COMPARTIDO_POR_IDENTIDADES", differences=["OBJETIVO_DUPLICADO"], verified_product_id=None, verified_chain_id=None, evidence=[])
    return results


def summary(rows: list[dict], results: list[dict]) -> dict:
    counts = Counter(r["status"] for r in results)
    chains = defaultdict(Counter)
    for r in results:
        chains[(r["account"], r["chain"], r["format"])][r["status"]] += 1
    return {"identities": len(results), "commercial_groups": len({group_key(r) for r in rows}),
            "verified": counts["COINCIDENCIA_VERIFICADA"], "review_required": counts["REVISION_REQUERIDA"],
            "without_correspondence": counts["SIN_CORRESPONDENCIA"], "ambiguous": counts["AMBIGUO"],
            "by_chain": [{"account": k[0], "chain": k[1], "format": k[2], "total": sum(v.values()),
                          "verified": v["COINCIDENCIA_VERIFICADA"], "review_required": v["REVISION_REQUERIDA"],
                          "without_correspondence": v["SIN_CORRESPONDENCIA"], "ambiguous": v["AMBIGUO"]}
                         for k, v in sorted(chains.items())]}
