"""Fail-closed stock capture/import. Only approved exact identities may be staged.

This is an offline preview, NOT permission to load Supabase. Decimal quantities
remain strings on the wire. Missing observations never become zero stock.
"""
from __future__ import annotations

import csv
import io
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext

COLUMNS = ("account", "chain_id", "barcode", "product_id", "store", "cutoff", "version", "pieces")


def csv_rows(text):
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if reader.fieldnames != list(COLUMNS):
        raise ValueError("COLUMNAS: use exactamente " + ",".join(COLUMNS))
    rows = list(reader)
    if any(set(row) != set(COLUMNS) or any(v is None for v in row.values()) for row in rows):
        raise ValueError("FILA_INCOMPLETA")
    return rows


def _text(obj, key):
    value = obj.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("TEXTO_REQUERIDO:" + key)
    return value


def _approval(obj):
    if obj.get("status") != "APPROVED":
        raise ValueError("APROBACION_PENDIENTE")
    for key in ("evidence", "approved_by", "approved_at"):
        _text(obj, key)
    stamp = datetime.fromisoformat(obj["approved_at"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("FECHA_APROBACION_SIN_ZONA")


def _day(value):
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError("FECHA_INVALIDA")


def _pieces(value):
    # Reject locale separators, exponents, infinities and binary floats.
    import re
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+)?", value):
        raise ValueError("PIEZAS_INVALIDAS")
    number = Decimal(value)
    if len(value) > 1000:
        raise ValueError("PIEZAS_EXCEDE_LIMITE_IMPORTADOR")
    return number


def preflight(rows, manifest, mappings, catalog, existing=()):
    """All-or-nothing validation. catalog is a fresh explicit product snapshot.

    mappings require source account/chain/format/UPC, target chain/product,
    attributes, and a human evidence-backed approval. Candidate matches never
    count as approvals. The manifest declares one common store/product scope.
    """
    errors = []
    accepted = []
    groups = defaultdict(list)
    try:
        if not isinstance(manifest, dict) or not isinstance(rows, list) or not isinstance(mappings, list) or not isinstance(catalog, list):
            raise ValueError("ESTRUCTURA_INVALIDA")
        if any(not isinstance(m, dict) for m in [*mappings, *catalog, *existing]):
            raise ValueError("REGISTRO_INVALIDO")
        _approval(manifest)
        for key in ("account", "chain_id", "cutoff", "version", "unit", "source_sha256"):
            _text(manifest, key)
        _day(manifest["cutoff"])
        import re
        if not re.fullmatch(r"[1-9][0-9]{0,9}", manifest["version"]) or int(manifest["version"]) > 2147483647:
            raise ValueError("VERSION_DEBE_SER_ENTERO_POSITIVO")
        if manifest["unit"] != "PIEZAS":
            raise ValueError("UNIDAD_NO_PIEZAS")
        import re
        if not re.fullmatch(r"[a-f0-9]{64}", manifest["source_sha256"]):
            raise ValueError("HASH_FUENTE_INVALIDO")
        stores, expected = manifest["stores"], manifest["identities"]
        if not isinstance(stores, list) or not isinstance(expected, list):
            raise ValueError("SCOPE_DEBE_SER_LISTA")
        if not stores or not expected or len(stores) != len(set(stores)) or len(expected) != len(set(expected)):
            raise ValueError("SCOPE_VACIO_O_DUPLICADO")
        if any(not isinstance(s, str) or not s or s != s.strip() for s in [*stores, *expected]):
            raise ValueError("SCOPE_INVALIDO")
        # Versions append; an existing scope/cut/version can never be replaced.
        signature = tuple(manifest[k] for k in ("account", "chain_id", "cutoff", "version"))
        if any(tuple(s[k] for k in ("account", "chain_id", "cutoff", "version")) == signature for s in existing):
            raise ValueError("VERSION_EXISTENTE_NO_SOBRESCRIBIR")
        lookup = {}
        targets = set()
        for ident in expected:
            candidates = [m for m in mappings if m.get("identity") == ident]
            if len(candidates) != 1:
                raise ValueError("IDENTIDAD_AMBIGUA_O_AUSENTE:" + ident)
            m = candidates[0]
            _approval(m)
            for key in ("account", "chain_id", "barcode", "product_id", "format", "model", "presentation", "commercial_code", "color"):
                _text(m, key)
            if ident != m["account"] + "|" + m["barcode"] or any(m[k] != manifest[k] for k in ("account", "chain_id")):
                raise ValueError("SCOPE_INCOMPATIBLE:" + ident)
            target = [p for p in catalog if p.get("id") == m["product_id"] and p.get("chain_id") == m["chain_id"]]
            if len(target) != 1 or m["barcode"] not in target[0].get("barcodes", []) or target[0].get("status") != "ACTIVE":
                raise ValueError("CATALOGO_NO_ACREDITA_IDENTIDAD:" + ident)
            if m["product_id"] in targets:
                raise ValueError("DOS_IDENTIDADES_MISMO_PRODUCTO")
            targets.add(m["product_id"])
            lookup[ident] = m
            key = tuple(m[k] for k in ("account", "chain_id", "format", "model", "presentation", "commercial_code"))
            groups[key].append(ident)
    except (ValueError, KeyError, TypeError) as exc:
        return {"status": "BLOCKED", "errors": [str(exc)], "rows": [], "models": [], "production_load": False}

    seen = set()
    for line, row in enumerate(rows, 2):
        try:
            if not isinstance(row, dict):
                raise ValueError("FILA_INVALIDA")
            if set(row) != set(COLUMNS):
                raise ValueError("COLUMNAS_INVALIDAS")
            for key in COLUMNS:
                _text(row, key)
            ident = row["account"] + "|" + row["barcode"]
            if ident not in lookup or any(row[k] != manifest[k] for k in ("account", "chain_id", "cutoff", "version")):
                raise ValueError("IDENTIDAD_O_CORTE_FUERA_DE_SCOPE")
            if row["product_id"] != lookup[ident]["product_id"] or row["store"] not in stores:
                raise ValueError("PRODUCTO_O_TIENDA_NO_ACREDITADOS")
            key = (row["product_id"], row["store"])
            if key in seen:
                raise ValueError("OBSERVACION_DUPLICADA")
            _pieces(row["pieces"])
            seen.add(key)
            accepted.append({**row, "identity": ident})
        except (ValueError, KeyError, InvalidOperation, TypeError) as exc:
            errors.append({"line": line, "reason": str(exc)})
    if not rows:
        errors.append({"reason": "SIN_OBSERVACIONES"})
    if errors:
        return {"status": "BLOCKED", "errors": errors, "rows": [], "models": [], "production_load": False}
    models = []
    for key, identities in groups.items():
        subset = [r for r in accepted if r["identity"] in identities]
        expected_pairs = len(identities) * len(stores)
        with localcontext() as ctx:
            ctx.prec = 2010 + len(str(len(subset)))
            total = str(sum((Decimal(r["pieces"]) for r in subset), Decimal(0))) if subset else None
        models.append({"group": list(key), "cutoff": manifest["cutoff"], "version": manifest["version"],
                       "pieces": total,
                       "products_covered": len({r["identity"] for r in subset}), "products_expected": len(identities),
                       "stores_covered": len({r["store"] for r in subset}), "stores_expected": len(stores),
                       "pairs_covered": len(subset), "pairs_expected": expected_pairs,
                       "status": "COMPLETE" if len(subset) == expected_pairs else "PARTIAL" if subset else "NO_DATA"})
    return {"status": "READY_FOR_OFFLINE_REVIEW", "errors": [], "rows": accepted, "models": models,
            "fingerprint": hashlib.sha256(json.dumps({"manifest": manifest, "mappings": mappings, "rows": accepted}, sort_keys=True).encode()).hexdigest(),
            "production_load": False}
