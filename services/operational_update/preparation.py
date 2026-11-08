"""Offline, reproducible preparation of an immutable operational data candidate.

Only source reading, comparison, previews and import-parser validation live here.
There is deliberately no SQL emitter, import confirmation or forecast execution.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import html
import io
import json
import re
import uuid
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from services.assistant_api.historical_corpus import number
from services.assistant_api.monthly_imports import ImportProfileVersion, parse_monthly
from services.inventory_preflight.automatic_reconciliation import group_key
from services.planning_pilot.engine import read_source

POLICY = "operational-preparation-1"
SOURCES = {"Towell": "FCST_TOWELL_HISTORY", "Cliente": "FCST_CLIENTE_HISTORY", "Pedidos": "ORDER"}
REPO = Path(__file__).resolve().parents[2]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def label(value):
    return re.sub(r"[\s_-]+", "", str(value or "").strip().upper())


def read_master(path):
    """Reuse the frozen pilot's source adapter, without invoking any model."""
    products, audit = read_source(Path(path))
    formulas, inventory, malformed = set(), [], []
    book = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    try:
        for sheet in book:
            columns = [{"nonempty": 0, "formulas": 0} for _ in range(sheet.max_column)]
            headers = {}
            for row_no, row in enumerate(sheet.iter_rows(values_only=True), 1):
                if row_no == 5:
                    headers = {get_column_letter(i + 1): str(v or "") for i, v in enumerate(row)}
                if (sheet.title in SOURCES and row_no >= 6 and len(row) > 9
                        and row[9] in "Enero Febrero Marzo Abril Mayo Junio Julio Agosto Septiembre Octubre Noviembre Diciembre".split()
                        and not (row[0] and row[4])):
                    malformed.append({"sheet": sheet.title, "row": row_no, "reason": "missing_account_or_upc"})
                for i, value in enumerate(row):
                    columns[i]["nonempty"] += value is not None
                    if isinstance(value, str) and value.startswith("="):
                        formulas.add(f"{sheet.title}!{get_column_letter(i + 1)}{row_no}")
                        columns[i]["formulas"] += 1
            inventory.append({"sheet": sheet.title, "state": sheet.sheet_state,
                              "rows": sheet.max_row, "columns": sheet.max_column,
                              "headers": headers, "column_stats": columns})
    finally:
        book.close()
    if file_hash(path) != audit["source_sha256"]:
        raise ValueError("source_changed_while_reading")
    records = []
    for product in products:
        for sheet, metric in SOURCES.items():
            for period, observation in sorted(product["history"][sheet].items()):
                state, value = number(observation["original"])
                records.append({"identity": product["id"], "period": period, "metric": metric,
                                "value": value, "value_state": state,
                                "original": str(observation["original"]) if observation["original"] is not None else None,
                                "cell": observation["cell"], "formula": observation["cell"] in formulas})
    return {"source_file": Path(path).name, "source_hash": audit["source_sha256"],
            "products": products, "records": records, "inventory": inventory,
            "malformed_rows": malformed}


def collect_snapshot(client):
    """Read through the existing GET-only, caller-scoped client. Never accept writes."""
    tables = {"chains": "chains", "products": "products", "profiles": "import_profile_versions",
              "observations": "portal_monthly_observations_current"}
    protected = ("forecast_calculations", "forecast_calculation_horizons", "forecast_selection_events",
                 "forecast_live_evaluations", "champion_registry", "performance_metrics", "forecast_vintages")

    def read():
        data = {key: client.rows(table, {"select": "*", "order": "observation_id.asc" if key == "observations" else "id.asc"})
                for key, table in tables.items()}
        data["protected"] = {table: client.rows(table, {"select": "*", "order": "id.asc"}) for table in protected}
        return data

    first, second = read(), read()
    if fingerprint(first) != fingerprint(second):
        raise ValueError("snapshot_changed_while_reading")
    return first


def _snapshot_index(snapshot):
    products = {(p["id"], p["chain_id"]): p for p in snapshot["products"]}
    if len(products) != len(snapshot["products"]):
        raise ValueError("duplicate_catalog_identity")
    current = {}
    for row in snapshot["observations"]:
        if (row["product_id"], row["chain_id"]) not in products:
            raise ValueError("snapshot_product_scope_mismatch")
        key = (row["chain_id"], row["product_id"], row["period"][:7], row["metric_code"])
        if key in current:
            raise ValueError("duplicate_snapshot_key")
        state, value = number(row["value"])
        if state != "VALUE":
            raise ValueError("invalid_snapshot_value")
        current[key] = {"id": row["observation_id"], "version": row["version_no"], "value": value}
    return products, current


def reconcile_identities(products, snapshot):
    live, _ = _snapshot_index(snapshot)
    scopes, by_upc = {}, defaultdict(dict)
    for profile in snapshot["profiles"]:
        mapping = profile.get("mapping_json", {})
        scope = mapping.get("scope")
        if not scope:
            continue
        if profile["chain_id"] in scopes and scopes[profile["chain_id"]] != scope:
            raise ValueError("conflicting_scope_metadata")
        scopes[profile["chain_id"]] = scope
        for item in mapping.get("products", []):
            key = (item["id"], item["chain_id"])
            if key not in live:
                continue
            for pair in item.get("identifier_mappings", []):
                if len(pair) == 2 and pair[1]:
                    by_upc[str(pair[1])][key] = live[key]
    output, seen = [], set()
    for product in products:
        if product["id"] in seen:
            raise ValueError("duplicate_private_identity")
        seen.add(product["id"])
        matches = [cid for cid, scope in scopes.items()
                   if label(product["format"]) in {label(scope["code"]), label(scope["code"].split("::")[-1])}]
        candidates = list(by_upc[product["barcode"]].values())
        same = [c for c in candidates if c["status"] == "ACTIVE" and c["chain_id"] in matches]
        if not candidates:
            status, reason = "SIN_CORRESPONDENCIA", "upc_not_found"
        elif len(matches) != 1:
            status, reason = "REVISION_REQUERIDA", "scope_not_unique_or_no_literal_equivalence"
        elif len(same) > 1:
            status, reason = "AMBIGUO", "multiple_active_upc_chain_candidates"
        elif not same:
            status, reason = "REVISION_REQUERIDA", "upc_in_other_scope_or_inactive"
        else:
            status, reason = "COINCIDENCIA_VERIFICADA", "exact_upc_and_unique_literal_scope"
            # Reject a known explicit chain contradiction. Legal-name aliases are
            # not guessed; the row's independent commercial scope remains visible.
            parent = scopes[matches[0]].get("parent_code") or scopes[matches[0]]["code"].split("::")[0]
            known_parents = {s.get("parent_code") or s["code"].split("::")[0] for s in scopes.values()}
            if any(label(product["chain"]) == label(p) and label(p) != label(parent) for p in known_parents):
                status, reason = "REVISION_REQUERIDA", "known_chain_contradiction"
        if product.get("issues"):
            status, reason = "REVISION_REQUERIDA", "source_identity_or_value_anomaly"
        target = same[0] if status == "COINCIDENCIA_VERIFICADA" else None
        output.append({**{k: product.get(k) for k in ("id", "account", "chain", "format", "barcode", "description", "model", "size", "color", "commercial_code")},
                       "state": status, "reason": reason, "chain_id": target["chain_id"] if target else None,
                       "product_id": target["id"] if target else None, "group": list(group_key(product)),
                       "candidates": [{k: c[k] for k in ("id", "chain_id", "description", "status")} for c in candidates],
                       "evidence": "source_column_D/catalog_scope/identifier_mappings" if target else None})
    targets = defaultdict(list)
    for row in output:
        if row["product_id"]:
            targets[row["chain_id"], row["product_id"]].append(row)
    for rows in targets.values():
        if len(rows) > 1:
            for row in rows:
                row.update(state="AMBIGUO", reason="shared_target_between_private_identities", product_id=None, chain_id=None)
    return output


def prepare(master, snapshot, cutoff):
    if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", cutoff):
        raise ValueError("invalid_cutoff")
    catalog, current = _snapshot_index(snapshot)
    identities = reconcile_identities(master["products"], snapshot)
    by_id = {row["id"]: row for row in identities}
    procedure_hash = file_hash(__file__)
    candidate_id = "CAND-" + fingerprint([POLICY, procedure_hash, master["source_hash"], fingerprint(snapshot), cutoff])[:20]
    comparisons, changes, histories = [], [], []
    selected = copy.deepcopy(current)
    for record in master["records"]:
        mapping = by_id[record["identity"]]
        key = (mapping["chain_id"], mapping["product_id"], record["period"], record["metric"])
        prior = current.get(key)
        row = {**record, "chain_id": mapping["chain_id"], "product_id": mapping["product_id"],
               "identity_state": mapping["state"], "prior_value": prior["value"] if prior else None,
               "prior_id": prior["id"] if prior else None, "prior_version": prior["version"] if prior else None,
               "next_version": prior["version"] + 1 if prior else 1}
        if record["value_state"] == "MISSING":
            row.update(comparison="SIN_DATO", activation="EXCEPCION", reason="empty_is_not_zero")
        elif record["value_state"] != "VALUE":
            row.update(comparison="NO_COMPARABLE", activation="EXCEPCION", reason="invalid_quantity")
        elif mapping["state"] != "COINCIDENCIA_VERIFICADA":
            row.update(comparison=mapping["state"] if mapping["state"] in {"SIN_CORRESPONDENCIA", "AMBIGUO"} else "NO_COMPARABLE",
                       activation="EXCEPCION", reason=mapping["reason"])
        elif record["metric"] != "ORDER":
            row.update(comparison="NUEVO", activation="PENDIENTE_CONTRATO", reason="historical_forecast_is_not_sales")
            histories.append(row)
        else:
            state = "NUEVO" if not prior else "COINCIDE" if Decimal(record["value"]) == Decimal(prior["value"]) else "DIFERENTE"
            row.update(comparison=state, activation="ELEGIBLE", reason="master_source_confirmed")
            if record["period"] > cutoff:
                row.update(activation="EXCEPCION", reason="period_after_confirmed_closure")
            elif record["formula"]:
                row.update(activation="EXCEPCION", reason="unverified_formula_cache")
            elif state != "COINCIDE" and catalog[(mapping["product_id"], mapping["chain_id"])]["description"] != catalog[(mapping["product_id"], mapping["chain_id"])]["description"].strip():
                row.update(activation="EXCEPCION", reason="import_parser_would_normalize_catalog_description")
            elif Decimal(record["value"]) >= Decimal("1000000000000000"):
                row.update(activation="EXCEPCION", reason="quantity_exceeds_numeric_18_3")
            elif Decimal(record["value"]) != Decimal(record["value"]).quantize(Decimal(".001")):
                row.update(activation="EXCEPCION", reason="precision_exceeds_numeric_18_3")
            elif state == "COINCIDE":
                row.update(activation="CONSERVAR_EXISTENTE", reason="equal_value_no_duplicate")
            if row["activation"] == "ELEGIBLE":
                row["proposed_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, fingerprint([candidate_id, key, record["value"], record["cell"]])))
                changes.append(row)
                selected[key] = {"id": row["proposed_id"], "version": row["next_version"], "value": row["value"]}
        comparisons.append(row)
    source_keys = [(r["identity"], r["period"], r["metric"]) for r in master["records"]]
    if len(set(source_keys)) != len(source_keys):
        raise ValueError("duplicate_source_key")
    for key, row in current.items():
        if key[3] in {"SALES", "DELIVERY"} and selected[key] != row:
            raise ValueError("protected_actual_changed")
    months = sorted({r["period"] for r in master["records"]})
    by_source = {(r["identity"], r["period"], r["metric"]): r for r in comparisons}
    color = []
    for item in identities:
        for month in months:
            for metric in (*SOURCES.values(), "SALES", "DELIVERY"):
                source = by_source.get((item["id"], month, metric))
                observed = selected.get((item["chain_id"], item["product_id"], month, metric))
                quantity = source["value"] if metric != "ORDER" and source else observed["value"] if observed else None
                state = source["activation"] if metric != "ORDER" and source else "SELECCION_SIMULADA" if observed else "SIN_DATO"
                color.append({"identity": item["id"], "account": item["account"], "chain": item["chain"], "format": item["format"],
                              "upc": item["barcode"], "model": item["model"], "presentation": item["size"], "color": item["color"],
                              "commercial_code": item["commercial_code"], "period": month, "metric": metric, "value": quantity,
                              "state": state, "group": item["group"]})
    grouped = defaultdict(list)
    for row in color:
        grouped[tuple(row["group"]), row["period"], row["metric"]].append(row)
    models = []
    for (group, month, metric), rows in sorted(grouped.items(), key=lambda x: str(x[0])):
        amounts = [Decimal(r["value"]) for r in rows if r["value"] is not None]
        models.append({"account": group[0], "chain": group[1], "format": group[2], "model": rows[0]["model"],
                       "presentation": rows[0]["presentation"], "commercial_code": rows[0]["commercial_code"],
                       "group": list(group), "identities": [r["identity"] for r in rows],
                       "period": month, "metric": metric, "value": str(sum(amounts)) if amounts else None,
                       "covered": len(amounts), "expected": len(rows),
                       "state": "COMPLETO" if len(amounts) == len(rows) else "TOTAL_PARCIAL" if amounts else "SIN_DATO"})
    summary = {"identities": len(identities), "groups": len({tuple(r["group"]) for r in identities}),
               "identity_states": dict(Counter(r["state"] for r in identities)), "source_cells": len(comparisons),
               "comparison_states": dict(Counter(r["comparison"] for r in comparisons)),
               "activation_states": dict(Counter(r["activation"] for r in comparisons)),
               "eligible_orders": dict(Counter(r["comparison"] for r in changes)),
               "exceptions": dict(Counter(r["reason"] for r in comparisons if r["activation"] == "EXCEPCION")),
               "base_observations": len(current), "simulated_selected_observations": len(selected)}
    manifest = {"policy": POLICY, "procedure_hash": procedure_hash, "candidate_id": candidate_id, "state": "PREPARADA_PRIVADA_NO_ACTIVA", "unit": "PIEZAS",
                "source_hash": master["source_hash"], "source_file": master["source_file"], "cutoff": cutoff,
                "snapshot_hash": fingerprint(snapshot), "protected_hash": fingerprint(snapshot.get("protected", {})),
                "identities_hash": fingerprint(identities), "records_hash": fingerprint(comparisons),
                "changes_hash": fingerprint(changes), "historical_forecasts_hash": fingerprint(histories),
                "selected_hash": fingerprint([[*key, value] for key, value in sorted(selected.items())]), "summary": summary,
                "preserve_actuals": ["SALES", "DELIVERY"], "blockers": ["historical_forecast_sql_contract", "atomic_release_selector",
                                                                                  "backup_restore_rehearsal", "explicit_production_authorization"],
                "malformed_source_rows": master.get("malformed_rows", []), "production_writes": 0, "forecast_runs": 0}
    manifest["manifest_hash"] = fingerprint(manifest)
    return {"manifest": manifest, "identities": identities, "comparisons": comparisons, "changes": changes,
            "color": color, "model": models, "inventory": master["inventory"]}


def _csv(rows, fields):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        values = {k: json.dumps(row.get(k), ensure_ascii=False) if isinstance(row.get(k), (dict, list)) else row.get(k) for k in fields}
        # CSV is a spreadsheet-facing preview. JSON keeps the untouched literal.
        writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "@", "-", "\t", "\r")) else v for k, v in values.items()})
    return stream.getvalue().encode("utf-8-sig")


def write_package(candidate, snapshot, output, preview_model=None, preview_format=None, protected_files=None):
    output = Path(output).resolve()
    if output.is_relative_to(REPO):
        raise ValueError("private_output_must_be_outside_repository")
    if output.exists():
        raise ValueError("refusing_to_overwrite_previous_version")
    files = {}
    encode = lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False).encode()
    for name, key in (("Identidades.json", "identities"),
                      ("Conciliacion.json", "comparisons"), ("Cambios_ORDER.json", "changes"), ("Inventario_hojas.json", "inventory")):
        files[name] = encode(candidate[key])
    files["Resumen.json"] = encode(candidate["manifest"]["summary"])
    for name, key in (("Identidades.csv", "identities"), ("Conciliacion.csv", "comparisons"),
                      ("Vista_por_color.csv", "color"), ("Vista_por_modelo.csv", "model")):
        rows = candidate[key]
        files[name] = _csv(rows, list(rows[0]) if rows else ["state"])
    exceptions = [r for r in candidate["comparisons"] if r["activation"] in {"EXCEPCION", "PENDIENTE_CONTRATO"}]
    files["Excepciones.csv"] = _csv(exceptions, list(candidate["comparisons"][0]) if candidate["comparisons"] else ["state"])
    coverage = Counter((r["chain"], r["format"], r["state"]) for r in candidate["identities"])
    files["Cobertura_por_cadena.csv"] = _csv([dict(chain=c, format=f, state=s, identities=n) for (c, f, s), n in sorted(coverage.items())], ["chain", "format", "state", "identities"])
    coverage = Counter((r["metric"], r["period"][:4], r["value_state"]) for r in candidate["comparisons"])
    files["Cobertura_por_variable_anio.csv"] = _csv([dict(metric=m, year=y, value_state=s, cells=n) for (m, y, s), n in sorted(coverage.items())], ["metric", "year", "value_state", "cells"])
    by_chain, profiles = defaultdict(list), []
    identities = {r["id"]: r for r in candidate["identities"]}
    catalog = {r["id"]: r for r in snapshot["products"]}
    for row in candidate["changes"]:
        by_chain[row["chain_id"]].append(row)
    for chain, rows in sorted(by_chain.items()):
        base = max((p for p in snapshot["profiles"] if p["chain_id"] == chain), key=lambda p: p["version"])
        profile = ImportProfileVersion(base["profile_id"], chain, base["version"] + 1,
                                       {"product_code": "product_code", "variant_code": "variant_code", "description": "description", "period": "period", "order": "order"})
        imports = [{"product_code": catalog[r["product_id"]]["product_code"], "variant_code": catalog[r["product_id"]].get("variant_code"),
                    "description": catalog[r["product_id"]]["description"], "source_upc": identities[r["identity"]]["barcode"],
                    "period": r["period"], "order": r["value"], "source_cell": r["cell"], "proposed_id": r["proposed_id"]} for r in rows]
        content = _csv(imports, ["product_code", "variant_code", "description", "source_upc", "period", "order", "source_cell", "proposed_id"])
        name = f"Pedido_{chain}.csv"
        parsed, rejected, total = parse_monthly(content, name, profile)
        if rejected or len(parsed) != len(rows) or total != len(rows):
            raise ValueError("existing_import_contract_rejected_candidate")
        if any(Decimal(p.metrics["ORDER"]) != Decimal(r["value"]) or p.period != r["period"] for p, r in zip(parsed, rows)):
            raise ValueError("import_parser_changed_quantity")
        for parsed_row, row in zip(parsed, rows):
            target = catalog[row["product_id"]]
            matching = [p for p in snapshot["products"] if p["chain_id"] == chain
                        and p["product_code"] == parsed_row.product_code
                        and (p.get("variant_code") or None) == parsed_row.variant_code]
            if len(matching) != 1 or matching[0]["id"] != target["id"] or parsed_row.description != target["description"]:
                raise ValueError("import_contract_identity_mismatch")
        files[name] = content
        profiles.append({"chain_id": chain, "profile_id": base["profile_id"], "version": profile.version, "columns": profile.columns,
                         "file": name, "file_hash": hashlib.sha256(content).hexdigest(), "valid_rows": total,
                         "state": "VALIDADO_LOCAL_NO_IMPORTADO", "available_at": None, "availability_source": "UNKNOWN"})
    files["Paquetes_importacion.json"] = encode(profiles)
    selected = [r for r in candidate["model"] if (not preview_model or label(r["model"]) == label(preview_model))
                and (not preview_format or label(r["format"]) == label(preview_format))]
    esc = lambda x: html.escape("Sin dato" if x is None else str(x))
    table = "<table><tr>" + "".join(f"<th>{esc(k)}</th>" for k in ("Cadena", "Modelo", "Presentación", "Mes", "Variable", "Piezas", "Cobertura", "Estado")) + "</tr>"
    for row in selected:
        table += "<tr>" + "".join(f"<td>{esc(x)}</td>" for x in (row["chain"], row["model"], row["presentation"], row["period"], row["metric"], row["value"], f'{row["covered"]}/{row["expected"]}', row["state"])) + "</tr>"
    color_table = "<h2>Detalle por color · últimos cuatro meses del cierre</h2><table><tr>" + "".join(f"<th>{esc(k)}</th>" for k in ("Formato", "UPC", "Color", "Mes", "Variable", "Piezas", "Estado")) + "</tr>"
    months = sorted({r["period"] for r in candidate["color"] if r["period"] <= candidate["manifest"]["cutoff"]})[-4:]
    for row in candidate["color"]:
        if row["period"] not in months or preview_model and label(row["model"]) != label(preview_model) or preview_format and label(row["format"]) != label(preview_format):
            continue
        color_table += "<tr>" + "".join(f"<td>{esc(x)}</td>" for x in (row["format"], row["upc"], row["color"], row["period"], row["metric"], row["value"], row["state"])) + "</tr>"
    summary = candidate["manifest"]["summary"]
    document = ('<!doctype html><html lang="es"><meta charset="utf-8"><title>Vista previa de actualización</title>'
                '<style>body{font:15px system-ui;margin:32px;color:#19384d}table{border-collapse:collapse}td,th{border:1px solid #ccd7df;padding:8px}th{background:#19384d;color:white}pre{white-space:pre-wrap}</style>'
                '<h1>Versión candidata privada · No activa</h1><p>' + esc(candidate["manifest"]["candidate_id"]) + '</p>'
                '<p>Towell = forecast interno histórico; Cliente = forecast recibido; Pedidos = pedido real. PIEZAS. Venta y Entrega se conservan desde la base; no se infieren del archivo. Las historias de forecast requieren contrato SQL y se muestran sólo como series privadas. Pedido muestra la selección simulada elegible; las excepciones no se activan.</p>'
                '<pre>' + esc(json.dumps(summary, ensure_ascii=False, indent=2)) + '</pre>' + color_table + '</table><h2>Consolidado por modelo · años disponibles</h2>' + table + '</table>'
                '<p>Vista completa: <a href="Vista_por_color.csv">Color</a> · <a href="Vista_por_modelo.csv">Modelo</a> · <a href="Conciliacion.csv">Conciliación</a> · <a href="Excepciones.csv">Excepciones</a></p>'
                '<p>Respaldo, activación y reversión requieren autorización posterior. No se ejecutaron SQL, importaciones, cálculos ni publicaciones.</p></html>')
    files["Vista_previa.html"] = document.encode()
    files["Validacion.json"] = encode({"existing_import_parser": True, "accepted_rows": sum(p["valid_rows"] for p in profiles),
                                      "rejected_rows": 0, "profiles": len(profiles), "database_writes": 0, "forecast_runs": 0})
    files["Protected_files.json"] = encode(protected_files or {})
    files["Plan_activacion.txt"] = activation_plan(candidate["manifest"]).encode()
    manifest = {k: v for k, v in candidate["manifest"].items() if k != "manifest_hash"}
    manifest["payload_hashes"] = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
    manifest["manifest_hash"] = fingerprint(manifest)
    files["Version_candidata.json"] = encode(manifest)
    files["Indice_integridad.json"] = encode({name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())})
    output.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        (output / name).write_bytes(data)
    verify_package(output, manifest["manifest_hash"])
    return manifest


def activation_plan(manifest):
    return f"""PRD 3A.7 — PLAN PENDIENTE DE AUTORIZACIÓN, NO EJECUTADO
Candidata: {manifest['candidate_id']} | Corte confirmado: {manifest['cutoff']} | PIEZAS
Fuente SHA256: {manifest['source_hash']}
Instantánea SHA256: {manifest['snapshot_hash']}
Resultados protegidos SHA256: {manifest['protected_hash']}

1. Preparación completada: sólo lectura de Excel y base; comparación Decimal,
   excepciones individuales, overlays privados y validación del parser existente.
   Ninguna importación, publicación, forecast, WAPE o escritura productiva.
2. Antes de autorizar: resolver las excepciones que se desee incluir. Las restantes
   quedan fuera del cambio. Vacío no borra ni sustituye por cero. Venta y Entrega
   existentes permanecen intactas. Las dos historias forecast NO son SALES.
3. Contratos pendientes: ampliar explícitamente los códigos de métricas y el
   mapeo del importador para las dos series históricas, sin tocar sus objetivos
   estadísticos. Resolver cantidades que exceden numeric(18,3) sin redondear en
   silencio. No se suministra ni aplica una migración en esta preparación.
4. Respaldo previo: exportación privada verificable de filas físicas y versiones,
   lotes, perfiles, catálogo, metadatos de origen y objetos protegidos; conservar
   DDL y políticas. Verificar restauración en un entorno aislado. La instantánea
   usada por esta candidata es comparación, NO un respaldo restaurable completo.
5. Revalidar: repetir GET y comparar huellas y versiones previas de cada clave.
   Cualquier cambio concurrente invalida esta candidata; preparar otra versión.
   Verificar el sello externo y todos los archivos con el comando verify.
6. Activación propuesta: autorización expresa del responsable sobre hash, alcance
   y excepciones. Reutilizar perfiles, lotes, parser, trazabilidad e inserciones
   append-only existentes; version_no anterior + 1, sin UPDATE ni DELETE de hechos.
   Los CSV son paquetes de parser, NO una autorización de confirmación. Revalidar
   producto, categoría y scope antes de confirmar; no crear productos/categorías.
7. Bloqueo de reversión: la vista actual elige mayor versión IMPORTED y no dispone
   de selector de release reversible. Antes de activar debe aprobarse e implantarse
   un selector mínimo de versión en los lectores actuales, conservando RLS, o un
   mecanismo equivalente demostrado con ensayo. No implementar aquí ese cambio.
8. Tras carga aislada autorizada: comprobar cantidades por producto/variable/mes,
   cobertura de cada modelo, ausencia de doble conteo, mismos C1 y V01–V03,
   mismas SALES/DELIVERY y cero nuevas decisiones/Champions/evaluaciones LIVE.
9. Reversión propuesta: cambiar atómicamente el selector al manifiesto anterior;
   conservar físicamente ambas versiones. Ensayar antes de activar. Una nueva fila
   compensatoria con valor anterior no elimina las claves nuevas: NO es rollback
   completo. Sin selector y respaldo probado, mantener producción sin cambios.

La confirmación de cierre no acredita available_at histórico ni WAPE LIVE.
Este programa sólo ofrece prepare/verify; no contiene activate/apply.
"""


def verify_package(output, expected_manifest_hash):
    output = Path(output)
    index = json.loads((output / "Indice_integridad.json").read_text(encoding="utf-8"))
    for name, expected in index.items():
        if Path(name).name != name or file_hash(output / name) != expected:
            raise ValueError("package_file_hash_mismatch")
    manifest = json.loads((output / "Version_candidata.json").read_text(encoding="utf-8"))
    stored = manifest.pop("manifest_hash")
    if stored != expected_manifest_hash or fingerprint(manifest) != stored:
        raise ValueError("manifest_hash_mismatch")
    expected_index = {**manifest["payload_hashes"], "Version_candidata.json": file_hash(output / "Version_candidata.json")}
    if index != expected_index:
        raise ValueError("package_index_not_bound_to_manifest")
    changes = json.loads((output / "Cambios_ORDER.json").read_text(encoding="utf-8"))
    if fingerprint(changes) != manifest["changes_hash"]:
        raise ValueError("changes_hash_mismatch")
    return {"candidate_id": manifest["candidate_id"], "manifest_hash": stored, "files_verified": len(index)}
