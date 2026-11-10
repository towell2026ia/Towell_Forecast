"""Build a private, deterministic unification candidate. Never connects to a database."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import uuid
import zipfile
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .preparation import REPO, _snapshot_index, file_hash, fingerprint, label

FACTS = "01_Hechos_Cierre_2025_2026.csv"
REFERENCE = "02_Historicos_Maestro_2024_2026_REFERENCIA.csv"
PLAN = "03_Pronosticos_y_Propuesta_Dic2026.csv"
CUTOFF = "2026-10"
PERIOD = re.compile(r"20(?:25|26)-(?:0[1-9]|1[0-2])")
METRICS = {
    "venta_piezas": "SALES", "pedido_piezas": "ORDER", "entrega_piezas": "DELIVERY",
    "fcst_cliente_piezas": "FCST_CLIENTE_HISTORY", "fcst_towell_piezas": "FCST_TOWELL_HISTORY",
    "inventario_reportado_piezas": "INVENTORY_AGGREGATE",
}
ACTUALS = {"SALES", "ORDER", "DELIVERY"}
OBS_FIELDS = ("chain_id", "product_id", "period", "metric_code", "observation_id", "version_no",
              "value", "availability_source", "available_at")
ROW_FIELDS = ("fila", "cadena", "tipo_compra", "formato", "item", "upc",
              "periodo_fecha_q", "periodo_etiqueta_rs", "referencia_origen")
DIFF_FIELDS = (*ROW_FIELDS, "chain_id", "product_id", "period", "metric_code", "status",
               "previous_id", "previous_value", "value", "observation_id")


def csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def json_read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def verify_inputs(package, package_zip=None):
    manifest_path = package / "manifest.json"
    manifest = json_read(manifest_path)
    if manifest.get("status") != "CANDIDATA_PRIVADA_NO_ACTIVADA":
        raise ValueError("unexpected_package_status")
    hashes = {"manifest.json": file_hash(manifest_path)}
    for entry in manifest["sources"] + manifest["files"]:
        name = entry["name"]
        if Path(name).name != name or name in hashes:
            raise ValueError("unsafe_or_duplicate_manifest_path")
        file = package / name
        if file_hash(file) != entry["sha256"] or file.stat().st_size != entry.get("bytes", file.stat().st_size):
            raise ValueError("input_hash_or_size_mismatch:" + name)
        hashes[name] = entry["sha256"]
    if not {FACTS, REFERENCE, PLAN, "README_PARA_CODEX.md"} <= hashes.keys():
        raise ValueError("required_package_files_missing")
    if package_zip:
        with zipfile.ZipFile(package_zip) as archive:
            members = {}
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = Path(info.filename).name
                if name in members:
                    raise ValueError("duplicate_zip_member")
                members[name] = hashlib.sha256(archive.read(info)).hexdigest()
            expected = {entry["name"]: hashes[entry["name"]] for entry in manifest["files"]}
            expected["manifest.json"] = hashes["manifest.json"]
            if expected != members:
                raise ValueError("zip_and_extracted_package_differ")
    return hashes


def catalog_index(snapshot):
    live, _ = _snapshot_index(snapshot)
    scopes, identifiers = defaultdict(set), defaultdict(lambda: defaultdict(set))
    for profile in snapshot["profiles"]:
        mapping = profile.get("mapping_json", {})
        scope = mapping.get("scope", {})
        if scope.get("level") != "COMMERCIAL_UNIT" or not scope.get("actual_bearing"):
            continue
        chain_id = profile["chain_id"]
        parent, separator, subformat = scope["code"].partition("::")
        scopes[label(scope.get("parent_code") or parent), label(subformat if separator else parent)].add(chain_id)
        for product in mapping.get("products", []):
            pid = product["id"]
            if live.get((pid, chain_id), {}).get("status") != "ACTIVE":
                continue
            for item, upc in product.get("identifier_mappings", []):
                if upc:
                    known = identifiers[chain_id, str(upc).strip()][pid]
                    if item:
                        known.add(str(item).strip())
    return scopes, identifiers


def identity(row, scopes, identifiers):
    scope = scopes.get((label(row["cadena"]), label(row["formato"])), set())
    if len(scope) != 1:
        return None, "scope_not_unique_or_missing"
    chain_id = next(iter(scope))
    candidates = identifiers.get((chain_id, row["upc"].strip()), {})
    if len(candidates) != 1:
        return None, "upc_not_in_scope" if not candidates else "ambiguous_upc_in_scope"
    pid, items = next(iter(candidates.items()))
    item = row["item"].strip()
    if item and items and item not in items:
        return None, "contradictory_item"
    return (chain_id, pid), None


def parse_value(raw, metric):
    if not raw.strip():
        return None, "blank"
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None, "non_numeric"
    if not value.is_finite() or value < 0:
        return None, "non_finite_or_negative"
    if metric in ACTUALS and (value >= 10 ** 15 or value != value.quantize(Decimal(".001"))):
        return None, "actual_outside_numeric_18_3"
    return value, None


def observation_key(row):
    return row["chain_id"], row["product_id"], row["period"][:7], row["metric_code"]


def source_key(row, period):
    return (label(row["cadena"]), row["tipo_compra"], label(row["formato"]), row["upc"].strip(), period)


def source_period(row):
    month = row["periodo_fecha_q"]
    if month != row["periodo_etiqueta_rs"]:
        return None, "period_q_rs_conflict"
    if not PERIOD.fullmatch(month) or month > CUTOFF:
        return None, "period_outside_approved_range"
    return month, None


def read_rows(facts, scopes, identifiers):
    counts = {
        column: Counter(source_key(row, row[column]) for row in facts)
        for column in ("periodo_fecha_q", "periodo_etiqueta_rs")
    }
    mapped = []
    for row in facts:
        month, bad_period = source_period(row)
        ids, bad_identity = identity(row, scopes, identifiers)
        reasons = [reason for reason in (bad_period, bad_identity) if reason]
        if row["tipo_compra"] not in {"RS", "CE"}:
            reasons.append("invalid_purchase_type")
        if any(index[source_key(row, row[column])] > 1 for column, index in counts.items()):
            reasons.append("source_duplicate")
        mapped.append((row, month, ids, reasons))
    # Evaluate destination collisions even when one source row is already in quarantine.
    # The legacy actual contract has no purchase-type key.
    target_counts = Counter((*ids, month) for _, month, ids, _ in mapped if ids and month)
    for row, month, ids, reasons in mapped:
        if ids and month and target_counts[(*ids, month)] > 1:
            reasons.append("shared_destination_scope_product_month")
        yield row, month, ids, reasons


def summarize_rows(rows):
    group = defaultdict(list)
    for row in rows:
        group[(row["period"], row["cadena"], row["formato"], row["tipo_compra"])].append(row)
    output = []
    for (period, chain, store_format, purchase_type), selected in sorted(group.items()):
        values = {}
        for metric in METRICS.values():
            known = [row[metric] for row in selected if row.get(metric) is not None]
            values[metric] = str(sum(known, Decimal(0))) if known else None
            values[metric + "_rows"] = len(known)
        matched = [r for r in selected if "ORDER" in r and "DELIVERY" in r]
        order = sum((r["ORDER"] for r in matched), Decimal(0))
        delivered = sum((r["DELIVERY"] for r in matched), Decimal(0))
        output.append({"period": period, "cadena": chain, "formato": store_format, "tipo_compra": purchase_type,
                       "rows": len(selected), **values, "fill_rate_paired_rows": len(matched),
                       "fill_rate": str(delivered / order) if order else None})
    return output


def prepare_unification(facts, plans, snapshot):
    scopes, identifiers = catalog_index(snapshot)
    a = {observation_key(r): dict(r) for r in snapshot["observations"]}
    b = dict(a)
    accepted, rejected, differences, inventory, ce = [], [], [], [], []
    statuses = defaultdict(Counter)
    for row, month, ids, reasons in read_rows(facts, scopes, identifiers):
        trace = {name: row[name] for name in ROW_FIELDS}
        if reasons:
            statuses[row["tipo_compra"]]["quarantined"] += 1
            rejected.extend({**trace, "metric_code": None, "reason": reason} for reason in reasons)
            continue
        chain_id, product_id = ids
        statuses[row["tipo_compra"]]["reconciled"] += 1
        selected = {**trace, "chain_id": chain_id, "product_id": product_id, "period": month}
        rs = row["tipo_compra"] == "RS"
        for field, metric in METRICS.items():
            value, issue = parse_value(row[field], metric)
            key = (*ids, month, metric)
            previous = a.get(key)
            proposed = None
            if issue:
                state = "preserve_previous" if previous else "not_supplied"
                if issue != "blank":
                    rejected.append({**trace, "metric_code": metric, "reason": issue})
            else:
                selected[metric] = value
                if not rs:
                    state = "retained_ce"
                    ce.append({**selected, "metric_code": metric, "value": row[field]})
                elif metric == "INVENTORY_AGGREGATE":
                    state = "new_inventory"
                    inventory.append({**selected, "value": row[field]})
                elif previous and value == Decimal(str(previous["value"])):
                    state, proposed = "unchanged", previous["observation_id"]
                else:
                    state = "replaced" if previous else "new"
                    proposed = str(uuid.uuid5(uuid.NAMESPACE_URL, fingerprint([key, row[field], trace["referencia_origen"],
                                                                              previous["observation_id"] if previous else None])))
                    b[key] = dict(zip(OBS_FIELDS, (*key, proposed, int(previous["version_no"]) + 1 if previous else 1,
                                                  row[field], "UNKNOWN", None), strict=True))
            differences.append({**trace, "chain_id": chain_id, "product_id": product_id, "period": month,
                                "metric_code": metric, "status": state, "previous_id": previous["observation_id"] if previous else None,
                                "previous_value": previous["value"] if previous else None, "value": row[field],
                                "observation_id": proposed})
        accepted.append(selected)
    assert a.keys() <= b.keys()
    preserved_2024 = [r for k, r in a.items() if k[2].startswith("2024") and k[3] in ACTUALS]
    assert all(b[observation_key(row)] == row for row in preserved_2024)
    fendi = fendi_control(facts, plans, accepted)
    rows_a, rows_b = [a[key] for key in sorted(a)], [b[key] for key in sorted(b)]
    return {"a": rows_a, "b": rows_b, "diff": differences, "issues": rejected, "inventory": inventory, "ce": ce,
            "totals": summarize_rows(accepted), "fendi": fendi,
            "stats": {"purchase_types": {k: dict(v) for k, v in sorted(statuses.items())},
                      "rows_a": len(rows_a), "rows_b": len(rows_b), "actuals_2024_preserved": len(preserved_2024),
                      "unchanged_prior_keys": sum(b[key] == v for key, v in a.items()),
                      "changes_by_metric": dict(sorted(Counter(f"{r['metric_code']}:{r['status']}" for r in differences).items())),
                      "issues": dict(sorted(Counter(r["reason"] for r in rejected).items()))}}


def fendi_control(facts, plans, accepted):
    def is_fendi(row):
        return (label(row["cadena"]) == "WALMART" and label(row["formato"]) == "BD" and
                row["tipo_compra"] == "RS" and label(row["modelo"]) == "FENDI" and label(row["tamano"]) == "MEDIOBAÑO")

    raw = [r for r in facts if is_fendi(r) and r["periodo_etiqueta_rs"] == CUTOFF]
    validated = {r["fila"] for r in accepted if r["period"] == CUTOFF}
    proposed = [r for r in plans if r["codigo_comercial"] == "MB71581000" and r["formato"] == "BD"
                and r["periodo_objetivo"] == "2026-12"]
    raw_ids, proposed_ids = {r["upc"] for r in raw}, {r["upc"] for r in proposed}
    totals = {metric: str(sum((Decimal(r[field]) for r in raw if r[field]), Decimal(0)))
              for field, metric in METRICS.items()}
    return {"closing_colors": len(raw_ids), "reconciled_colors": len({r["upc"] for r in raw if r["fila"] in validated}),
            "proposal_colors": len(proposed_ids), "closing_without_proposal": sorted(raw_ids - proposed_ids),
            "closing_totals": totals,
            "fill_rate": str(Decimal(totals["DELIVERY"]) / Decimal(totals["ORDER"])) if Decimal(totals["ORDER"]) else None,
            "proposal_december": str(sum((Decimal(r["propuesta_comercial_piezas"]) for r in proposed), Decimal(0)))}


def write_csv(path, rows, fields):
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")


def verify_logical_reversal(rows_a, rows_b):
    """Verify the complete selections and pinning in memory, not a database transaction."""
    selections = {"A": tuple(rows_a), "B": tuple(rows_b)}
    active = "A"
    pin = selections[active]
    hash_a = fingerprint(pin)
    active = "B"
    seen_b = fingerprint(selections[active])
    assert fingerprint(pin) == hash_a
    active = "A"
    restored = fingerprint(selections[active])
    assert restored == hash_a
    return {"method": "offline_logical_selection", "a_hash": hash_a, "b_hash": seen_b,
            "restored_hash": restored, "a_rows": len(rows_a), "b_rows": len(rows_b),
            "restored_rows": len(selections[active])}


def run(package, snapshot_path, output, package_zip=None):
    if output.resolve().is_relative_to(REPO):
        raise ValueError("private_output_required")
    hashes = verify_inputs(package, package_zip)
    snapshot = json_read(snapshot_path)
    facts, plans = csv_rows(package / FACTS), csv_rows(package / PLAN)
    candidate = prepare_unification(facts, plans, snapshot)
    stats = candidate["stats"]
    fendi = candidate["fendi"]
    if (len(facts), len(plans), fendi["closing_colors"], fendi["proposal_colors"]) != (13910, 413, 11, 10):
        raise ValueError("package_control_count_mismatch")
    if fendi["closing_without_proposal"] != ["7501897813160"] or fendi["proposal_december"] != "114315":
        raise ValueError("package_fendi_control_mismatch")
    october = [row for row in facts if row["periodo_etiqueta_rs"] == CUTOFF]
    if len(october) != 644:
        raise ValueError("package_october_row_count_mismatch")
    for column, expected in (("venta_piezas", 302608), ("pedido_piezas", 284845),
                             ("entrega_piezas", 174873), ("inventario_reportado_piezas", 1526382)):
        if sum(Decimal(row[column]) for row in october if row[column]) != expected:
            raise ValueError("package_october_total_mismatch:" + column)
    output.mkdir(parents=True, exist_ok=False)
    for filename, values in (("Observaciones_A.csv", candidate["a"]), ("Observaciones_B.csv", candidate["b"])):
        write_csv(output / filename, values, OBS_FIELDS)
    write_csv(output / "Comparacion.csv", candidate["diff"], DIFF_FIELDS)
    write_csv(output / "Cambios_B.csv", (r for r in candidate["diff"] if r["status"] in {"new", "replaced"}), DIFF_FIELDS)
    write_csv(output / "Excepciones.csv", candidate["issues"], (*ROW_FIELDS, "metric_code", "reason"))
    write_csv(output / "Inventario_B.csv", candidate["inventory"], (*ROW_FIELDS, "chain_id", "product_id", "period", "value"))
    write_csv(output / "CE_Conciliado.csv", candidate["ce"], (*ROW_FIELDS, "chain_id", "product_id", "period", "metric_code", "value"))
    write_csv(output / "Totales.csv", candidate["totals"], ("period", "cadena", "formato", "tipo_compra", "rows",
              *(column for m in METRICS.values() for column in (m, m + "_rows")), "fill_rate_paired_rows", "fill_rate"))
    write_json(output / "Catalogo_completo.json", {key: snapshot[key] for key in ("chains", "products")})
    write_json(output / "Fendi.json", fendi)
    reversal = verify_logical_reversal(candidate["a"], candidate["b"])
    write_json(output / "Reversion_logica.json", reversal)
    snapshot_sha = file_hash(snapshot_path)
    manifest = {"candidate_id": "UNIF-" + fingerprint([hashes, snapshot_sha, file_hash(__file__)])[:20],
                "cutoff": CUTOFF, "status": "PRIVATE_NOT_ACTIVATED", "input_hashes": hashes,
                "zip_sha256": file_hash(package_zip) if package_zip else None,
                "baseline_snapshot_sha256": snapshot_sha, "baseline_evidence": snapshot.get("read_evidence"),
                "catalog_count": len(snapshot["products"]), "scope_count": len(snapshot["chains"]),
                "observations_a": stats["rows_a"], "observations_b": stats["rows_b"], "comparison": stats,
                "plan_records": len(plans), "history_reference_records": len(csv_rows(package / REFERENCE)),
                "inventory_rows": len(candidate["inventory"]), "ce_metrics_retained": len(candidate["ce"]),
                "selection_policy": "Preserve A and replace/add eligible RS; keep CE separate because the legacy key omits purchase type. A does not establish RS eligibility for retained observations.",
                "reversal": reversal,
                "results_sha256": {path.name: file_hash(path) for path in sorted(output.iterdir())}}
    write_json(output / "Manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package-zip", type=Path)
    args = parser.parse_args()
    result = run(args.package, args.snapshot, args.output, args.package_zip)
    print(json.dumps({"candidate_id": result["candidate_id"], "catalog": result["catalog_count"],
                      "comparison": result["comparison"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
