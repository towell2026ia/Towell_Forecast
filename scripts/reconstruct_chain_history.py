"""Chain-aware source replay with private, reviewed sheet links. NO remote writes."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.assistant_api.chain_ingestion import (  # noqa: E402
    SourceSheetProfile, identifier, is_formula, read_sources, reconcile_chain_aware, trace_old_conflicts, unit_key,
)
from services.assistant_api.historical_corpus import HEADER_PERIOD, MONTHLY_METRICS, SourceSpec, sha256_file  # noqa: E402
from services.assistant_api.source_adjudication import cluster_conflicts, fingerprint, review_pack  # noqa: E402

MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
METRIC_LABELS = {"venta mensual": "SALES", "pedido mensual": "ORDER", "entrega mensual": "DELIVERY"}
IDENTITY_LABELS = {"item", "sku", "prime item nbr"}


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)


def resumen_mapping(books):
    """Literal GRAL/CADENA/commercial-code mapping; never expands models to UPCs."""
    result = []
    for digest, sheets in books.items():
        for name, sheet in sheets.items():
            for row_no, row in sheet["rows"].items():
                fields = {str(v).strip().casefold(): k for k, v in row.items() if isinstance(v, str) and not is_formula(v)}
                if not {"gral", "cadena", "modelo"} <= set(fields):
                    continue
                code_column = fields.get("codigo new") or fields.get("código") or fields.get("codigo")
                if not code_column:
                    continue
                for n, values in sheet["rows"].items():
                    if n <= row_no:
                        continue
                    group, chain, product = (values.get(fields["gral"]), values.get(fields["cadena"]), values.get(code_column))
                    if not all(isinstance(v, str) and v.strip() and not is_formula(v) for v in (group, chain)):
                        continue
                    result.append({"source_hash": digest, "sheet": name, "row": n, "group": group.strip(),
                                   "chain_label": chain.strip(), "commercial_code": product.strip() if isinstance(product, str) and not is_formula(product) else None,
                                   "not_operational_item_or_upc": True,
                                   "locator": f"{fields['gral']}{n}:{code_column}{n}"})
    return sorted(result, key=lambda r: (r["source_hash"], r["sheet"], r["row"]))


def build_profiles(books, config):
    profiles = []
    for digest, sheets in sorted(books.items()):
        scope = config["sources"].get(digest)
        if scope is None:
            raise ValueError("source_hash_not_reviewed")
        for name, data in sorted(sheets.items()):
            rules = {**config.get("rule_sets", {}).get(scope.get("rule_set"), {}), **scope.get("sheets", {})}
            rule = rules.get(name, {"role": "UNKNOWN", "notes": "No reviewed business assignment"})
            headers, fields = [], {}
            for n, row in data["rows"].items():
                labels = {str(v).strip().casefold(): k for k, v in row.items() if isinstance(v, str) and not is_formula(v)}
                if set(labels) & (IDENTITY_LABELS | {"upc"}):
                    headers.append(n)
                    if not fields:
                        fields = labels
            item = next((fields[label] for label in ("item", "sku", "prime item nbr") if label in fields), "")
            upc = fields.get("upc", "") or rule.get("reviewed_upc_column", "")
            description = next((fields[label] for label in ("modelo", "desc  artículo principal", "prime item desc", "descripción") if label in fields), "")
            category = fields.get("linea", "")
            chain_column, format_column = fields.get("cadena", ""), fields.get("formato", "")
            metric_layout = []
            header = data["rows"].get(min(headers, default=0), {})
            # Raw monthly measures carry their own year/month labels.
            for column, label in header.items():
                match = HEADER_PERIOD.search(str(label or ""))
                if match:
                    metric = next((m for fragment, m in MONTHLY_METRICS.items() if fragment in match[3]), None)
                    if metric:
                        metric_layout.append({"column": column, "period": f"{match[1]}-{match[2]}", "metric": metric,
                                              "evidence_level": "C" if str(label).startswith(("Suma de ", "Sum of ")) else "A"})
            # Chain tabs with explicit monthly metric bands and reviewed year/cutoff.
            if rule.get("monthly_bands") and scope.get("year"):
                groups = {}
                for n in range(1, min(headers, default=1)):
                    for column, value in data["rows"].get(n, {}).items():
                        metric = METRIC_LABELS.get(str(value or "").strip().casefold())
                        if isinstance(value, str) and not is_formula(value):
                            # Every band boundary resets the demand metric. Inventory,
                            # fill-rate and currency bands must never inherit DELIVERY.
                            groups[column] = metric
                columns = list(header)
                from openpyxl.utils import column_index_from_string
                active = None
                for column in sorted(columns, key=column_index_from_string):
                    if column in groups:
                        active = groups[column]
                    token = str(header[column]).strip().lower()[:3]
                    if active and token in MONTHS:
                        metric_layout.append({"column": column, "period": f"{scope['year']}-{MONTHS.index(token)+1:02d}",
                                              "metric": active, "evidence_level": rule.get("level", "A")})
                    elif column not in groups and str(header[column]).strip().lower() in {"total", "ttl", "%fr"}:
                        active = None
            # Explicit date-column master, field meaning independent of sheet name.
            if {"fecha", "venta", "pedido", "entrega"} <= set(fields):
                metric_layout = [{"column": fields[label], "date_column": fields["fecha"], "metric": metric, "evidence_level": "A"}
                                 for label, metric in (("venta", "SALES"), ("pedido", "ORDER"), ("entrega", "DELIVERY"))]
            # Dated input blocks: never substitute the workbook year for an older header date.
            if rule.get("dated_sales"):
                for index, n in enumerate(headers):
                    for column, when in data["rows"].get(n-1, {}).items():
                        if isinstance(when, (date, datetime)):
                            metric_layout.append({"column": column, "period": when.strftime("%Y-%m"), "metric": "SALES", "evidence_level": "A",
                                                  "row_from": n+1, "row_to": headers[index+1]-2 if index+1 < len(headers) else data["max_row"]})
            parent, fmt = rule.get("parent", ""), rule.get("format", "")
            unit = rule.get("unit", "") or unit_key(parent, fmt)
            role = rule["role"]
            membership_unit = unit if role == "RAW_BASE" and rule.get("documented_link") else ""
            if role == "RAW_BASE":
                unit = ""
            profile = SourceSheetProfile(digest, name, role, unit, rule.get("label", unit), parent, fmt,
                                         item, upc, description, category,
                                         {"year": scope.get("year"), "cutoff": scope.get("cutoff", ""),
                                          "basis": "Reviewed scope or explicit source header; never mtime"}, tuple(metric_layout),
                                         "LITERAL_WITH_FORMULAS_EXCLUDED", "EXPLICIT_IDENTIFIERS" if item or upc else "NO_ATOMIC_PRODUCT_ID",
                                         rule.get("membership", "CHAIN_MEMBERSHIP" if unit or membership_unit or chain_column else "NONE"),
                                         "ACTUAL" if rule.get("values") and metric_layout else "NONE", rule.get("notes", ""),
                                         chain_column, format_column, tuple(headers),
                                         ({"source_hash": digest, "locator": "SHEET:" + name, "basis": rule.get("basis", "Reviewed source sheet role")},),
                                         rule.get("review", role == "UNKNOWN" or not (unit or membership_unit or chain_column)),
                                         rule.get("pair_group", ""), membership_unit)
            profiles.append(profile)
    return profiles


def inventory(books, profiles, manifests, report):
    records = []
    members = report["chain_membership_ledger"]
    facts = report["source_assignments"]
    for manifest in manifests:
        digest = manifest["sha256"]
        for profile in (p for p in profiles if p.source_hash == digest):
            data = books[digest][profile.sheet_name]
            matching = [m for m in members if m["source_hash"] == digest and m["sheet"] == profile.sheet_name]
            values = [a for a in facts if a["source_sha256"] == digest and a["sheet"] == profile.sheet_name]
            periods = sorted({a["key"][2] for a in values})
            records.append({"source_hash": digest, "filename": manifest["source_file"], "sheet_name": profile.sheet_name,
                            "duplicate_copy": bool(manifest["duplicate_relationship"]), "range": data["range"], "sheet_role": profile.sheet_role,
                            "chain_assignment": profile.canonical_chain_code or profile.membership_chain_code or
                            "EXPLICIT_ROW_CHAIN" if profile.chain_column else profile.canonical_chain_code or profile.membership_chain_code or None,
                            "item_count": len({m["item"] for m in matching if m["item"]}),
                            "upc_count": len({m["upc"] for m in matching if m["upc"]}), "formula_count": len(data["formulas"]),
                            "direct_metric_count": len(values), "period_min": periods[0] if periods else None,
                            "period_max": periods[-1] if periods else None, "classification_confidence": "SOURCE_STRUCTURE" if profile.sheet_role != "UNKNOWN" else "UNKNOWN",
                            "review_required": profile.review_required})
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--old-report", type=Path, required=True)
    parser.add_argument("--old-clusters", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if not output.is_relative_to(Path(__file__).resolve().parents[1] / "outputs") or (output / "reconciliation.json").exists():
        parser.error("fresh private output under ignored outputs/ is required")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    specs = [SourceSpec(Path(path)) for path in config["files"]]
    if {sha256_file(spec.path) for spec in specs} != set(config["sources"]):
        raise ValueError("reviewed_source_hashes_changed")
    old = json.loads(args.old_report.read_text(encoding="utf-8"))
    old_clusters = json.loads(args.old_clusters.read_text(encoding="utf-8"))
    if old["summary"]["blocking_keys"] != 594 or old["summary"]["dataset_sha256"] != config["old_dataset_sha"]:
        raise ValueError("old_594_baseline_mismatch")
    if fingerprint(old["versions"]) != config["old_versions_fingerprint"]:
        raise ValueError("old_versions_changed")
    books, manifests = read_sources(specs)
    profiles = build_profiles(books, config)
    report = reconcile_chain_aware(books, manifests, profiles)
    reverse_books, reverse_manifests = read_sources(specs[::-1])
    reverse = reconcile_chain_aware(reverse_books, reverse_manifests, list(reversed(profiles)))
    for field in ("versions", "selected", "chain_membership_ledger", "source_assignments"):
        if report[field] != reverse[field]:
            raise ValueError("source_order_changed_" + field)
    blocked = lambda r: sorted((d for d in r["resolution_ledger"] if d["blocking"]), key=fingerprint)
    if blocked(report) != blocked(reverse) or report["summary"] != reverse["summary"]:
        raise ValueError("source_order_changed_conflicts_or_counts")
    trace = trace_old_conflicts(old, report, old_clusters)
    clusters = cluster_conflicts(report)
    pack = review_pack(clusters, report)
    catalog = defaultdict(lambda: {"sources": set(), "parents": set(), "formats": set()})
    for profile in profiles:
        key = profile.canonical_chain_code or profile.membership_chain_code
        if key:
            entry = catalog[key]
            entry["sources"].add(profile.source_hash)
            if profile.parent_chain: entry["parents"].add(profile.parent_chain)
            if profile.commercial_format: entry["formats"].add(profile.commercial_format)
    for member in report["chain_membership_ledger"]:
        if member["chain"]:
            entry = catalog[member["chain"]]
            entry["sources"].add(member["source_hash"])
            if member["parent_chain"]: entry["parents"].add(member["parent_chain"])
            if member["commercial_format"]: entry["formats"].add(member["commercial_format"])
    artifacts = {"sheet_inventory": inventory(books, profiles, manifests, report),
                 "sheet_profiles": [asdict(p) for p in profiles],
                 "chain_catalog": [{"canonical_chain": key, **{k: sorted(v) for k, v in entry.items()}} for key, entry in sorted(catalog.items())],
                 "chain_membership_ledger": report["chain_membership_ledger"], "resumen_mapping": resumen_mapping(books),
                 "old_to_new_conflict_map": trace, "reconciliation": report,
                 "conflict_clusters": clusters, "manual_review_pack": pack,
                 "determinism": {"status": "PASS", "counts": True, "versions": True, "conflicts": True,
                                  "sha": report["summary"]["dataset_sha256"]},
                 "reclassification": dict(sorted(Counter(row["new_resolution"] for row in trace).items()))}
    if {sha256_file(spec.path) for spec in specs} != set(config["sources"]):
        raise ValueError("source_bytes_changed_during_scan")
    for name, value in artifacts.items():
        write_new(output / f"{name}.json", value)
    print(json.dumps({"summary": report["summary"], "roles": dict(Counter(s["sheet_role"] for s in artifacts["sheet_inventory"])),
                      "sheet_count": len(artifacts["sheet_inventory"]), "canonical_units": len(catalog),
                      "resumen_lines": len(artifacts["resumen_mapping"]), "old_accounting": artifacts["reclassification"]}, indent=2))


if __name__ == "__main__": main()
