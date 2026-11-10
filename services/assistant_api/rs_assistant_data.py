"""Read only the authorized, published RS archive under one request-scoped pin."""
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation, localcontext
import json
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from .auth import READ
from .commercial_api import commercial_catalog
from .commercial_catalog import key
from .supabase_access import PreviewError, uuid_value


ARCHIVE_ID = "UNIF-20060057ee8e4dfdbcd9"
ARCHIVE_SHA = "72f4b1a25d7d26823c28d10f39326b2743d3eb2655ad009f017b0257a4dec20d"
ARCHIVE_VERSION_ID = str(uuid5(NAMESPACE_URL, "towell-operational-release:" + ARCHIVE_SHA))
START_PERIOD, CUTOFF = "2025-01", "2026-10"
METRICS = ("SALES", "ORDER", "DELIVERY", "FCST_TOWELL_HISTORY", "FCST_CLIENTE_HISTORY")
PERIOD = re.compile(r"20\d\d-(?:0[1-9]|1[0-2])")
SOURCE = re.compile(r"BAASE!A([1-9][0-9]*):Z\1")
MODEL_BLOCKERS = {
    "DESCRIPTION_ALIAS_CONFLICT", "INSUFFICIENT_MODEL_COLOR_BOUNDARY_EVIDENCE",
    "METADATA_IDENTITY_REVIEW_REQUIRED", "MODEL_BOUNDARY_UNVALIDATED",
    "PRESENTATION_ALIAS_CONFLICT", "SAME_SCOPE_IDENTIFIER_COLLISION",
    "SEASON_CODE_UNVALIDATED", "UNICODE_BOUNDARY_REVIEW_REQUIRED", "UNRESOLVED_ABBREVIATION",
}
PUBLIC_PRODUCT_TYPES = {"TOALLA", "TAPETE", "TRAPO", "BATA"}


class _ScopedCatalog:
    """Constrain the existing commercial resolver's queries to one chain."""

    def __init__(self, client, chain_id):
        self.client, self.chain_id = client, chain_id
        self.data = {}

    def rows(self, table, params):
        if table.startswith("commercial_"):
            return self.client.rows(table, params)
        field = "id" if table == "chains" else "chain_id"
        query = {**params, field: f"eq.{self.chain_id}"}
        if table in {"chains", "products"}:
            query.update(status="eq.ACTIVE")
        rows = self.client.rows(table, query)
        if any(row.get(field) != self.chain_id for row in rows):
            raise PreviewError("DATA_READ_FAILED", 503)
        self.data[table] = rows
        return rows


def _format(value):
    # Match the import scope's existing normalization, without guessing aliases.
    return re.sub(r"[\s_-]+", "", str(value or "").strip().upper())


def _model_scope(client, chain_id, requested_format, model):
    catalog = _ScopedCatalog(client, chain_id)
    rules = json.loads((Path(__file__).resolve().parents[2] / "config/commercial_catalog_rules.json").read_text(encoding="utf-8"))
    try:
        classifications = commercial_catalog(catalog, SimpleNamespace(chain_ids={chain_id}), rules)["products"]
        chain, = catalog.data["chains"]
        formats = set()
        for profile in catalog.data["import_profile_versions"]:
            scope = profile["mapping_json"].get("scope", {})
            if scope.get("level") == "COMMERCIAL_UNIT" and scope.get("actual_bearing") is True:
                parent, separator, child = scope["code"].partition("::")
                value = child if separator else parent
                if value:
                    formats.add(value)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise PreviewError("DATA_READ_FAILED", 503) from None
    if {_format(value) for value in formats} != {_format(requested_format)}:
        raise PreviewError("RS_FORMAT_NOT_VERIFIED", 409)

    matching = [row for row in classifications if key(row["model"]) == key(model)]
    selected = {uuid_value(row["product_id"]): row for row in matching if row["status"] == "APPROVED" or
                (row["source"].startswith("TRANSIENT_PROPOSAL:") and not MODEL_BLOCKERS.intersection(row["reasons"]))}
    if not selected:
        raise PreviewError("RS_MODEL_NOT_VERIFIED", 409)
    presentations = {key(row["size"]) or ("NOT_APPLICABLE" if "size" in row["not_applicable"] else None)
                     for row in selected.values()}
    if len(presentations) != 1 or None in presentations:
        raise PreviewError("RS_MODEL_PRESENTATION_AMBIGUOUS", 409)
    products = {row["id"]: row for row in catalog.data["products"]}
    categories = {row["id"]: row["name"] for row in catalog.data["categories"]}
    return chain["name"], sorted(formats)[0], selected, products, categories, len(matching) - len(selected)


def _exact(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", value):
            raise ValueError()
        parsed = Decimal(value)
        if parsed >= 10**15 or parsed.as_tuple().exponent < -16:
            raise ValueError()
        return parsed
    except (InvalidOperation, ValueError):
        raise PreviewError("DATA_READ_FAILED", 503) from None


def _month_rows(start, end):
    year, month = map(int, start.split("-"))
    while (period := f"{year:04d}-{month:02d}") <= end:
        yield period
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def _read_facts(client, chain_id, selected, period, scope_format):
    facts, inventory, excluded, seen = defaultdict(dict), {}, Counter(), set()
    ids = sorted(selected)
    for offset in range(0, len(ids), 50):
        batch = ids[offset:offset + 50]
        observations = client.observation_rows({
            "select": "id,chain_id,product_id,period,metric_code,value_text,version_no,availability_source,available_at,source_batch_id,source_reference,source_value_literal",
            "p_product_ids": "{" + ",".join(batch) + "}", "p_from": START_PERIOD + "-01",
            "p_to": period + "-01", "p_metric_codes": "{" + ",".join(METRICS) + "}",
            "order": "product_id.asc,period.asc,metric_code.asc,id.asc",
        }, chain_id=chain_id)
        for row in observations:
            product, date, metric = row["product_id"], row["period"], row["metric_code"]
            month = date[:7]
            identity = product, month, metric
            if (row["chain_id"] != chain_id or product not in batch or metric not in METRICS or identity in seen
                    or not PERIOD.fullmatch(month) or date != month + "-01" or not START_PERIOD <= month <= period):
                raise PreviewError("DATA_READ_FAILED", 503)
            seen.add(identity)
            source, literal = row.get("source_reference"), row.get("source_value_literal")
            if not source or not isinstance(source, str) or not SOURCE.fullmatch(source):
                excluded[metric] += 1
                continue
            if row["availability_source"] != "UNKNOWN" or row["available_at"] is not None or _exact(literal) != _exact(row["value_text"]):
                raise PreviewError("DATA_READ_FAILED", 503)
            facts[month][product, metric] = row

        stock = client.inventory_rows({
            "select": "chain_id,product_id,period,format,purchase_type,quantity_text,source_reference",
            "chain_id": f"eq.{chain_id}", "product_id": "in.(" + ",".join(batch) + ")",
            "period": f"eq.{period}-01", "purchase_type": "eq.RS", "order": "product_id.asc",
        })
        for row in stock:
            product = row["product_id"]
            if (row["chain_id"] != chain_id or product not in batch or product in inventory
                    or row["period"] != period + "-01" or row["purchase_type"] != "RS"
                    or _format(row["format"]) != _format(scope_format)
                    or not SOURCE.fullmatch(row.get("source_reference") or "")):
                raise PreviewError("DATA_READ_FAILED", 503)
            inventory[product] = _exact(row["quantity_text"])
    return facts, inventory, excluded


def _summary(facts, inventory, expected, period):
    by_month = []
    with localcontext() as context:
        context.prec = 50
        for month in _month_rows(START_PERIOD, period):
            values = {(product, metric): _exact(row["source_value_literal"])
                      for (product, metric), row in facts[month].items()}
            metrics = {}
            for metric in METRICS:
                series = [value for (_, code), value in values.items() if code == metric]
                metrics[metric] = {"pieces": format(sum(series), "f") if series else None,
                                   "products_with_data": len(series), "products_expected": expected}
            paired = {product for product, code in values if code == "ORDER" and (product, "DELIVERY") in values}
            ordered = sum(values[product, "ORDER"] for product in paired)
            delivered = sum(values[product, "DELIVERY"] for product in paired)
            metrics["fill_rate"] = {
                "ratio": format(delivered / ordered, "f") if ordered > 0 else None,
                "paired_products": len(paired), "products_expected": expected,
            }
            by_month.append({"period": month, **metrics})
        stock = {"period": period, "level": "CHAIN_FORMAT_PRODUCT_MONTH", "pieces": format(sum(inventory.values()), "f") if inventory else None,
                 "products_with_data": len(inventory), "products_expected": expected}
    return {"by_month": by_month, "inventory": stock}


def load_rs_scope(client, principal, *, chain_id, purchase_type, format, model, period):
    """Use a caller-bound SupabaseReadClient; never substitute the active baseline."""
    chain_id = uuid_value(chain_id)
    if READ not in principal.permissions or chain_id not in principal.chain_ids:
        raise PreviewError("SCOPE_FORBIDDEN", 403)
    if (purchase_type != "RS" or not isinstance(period, str) or not PERIOD.fullmatch(period)
            or not START_PERIOD <= period <= CUTOFF or not isinstance(format, str) or not _format(format)
            or not isinstance(model, str) or not key(model)):
        raise PreviewError("RS_SCOPE_INVALID")
    pin = client.operational_context()
    if pin["version_id"] != ARCHIVE_VERSION_ID:
        raise PreviewError("RS_CANDIDATE_NOT_ACTIVE", 409)
    chain, scope_format, selected, products, categories, unresolved = _model_scope(client, chain_id, format, model)
    try:
        facts, inventory, excluded = _read_facts(client, chain_id, selected, period, scope_format)
        metrics = _summary(facts, inventory, len(selected) + unresolved, period)
        records = []
        for month, rows in sorted(facts.items()):
            for (pid, metric), row in sorted(rows.items()):
                if metric != "SALES":
                    continue
                product = products[pid]
                category = product.get("category_id")
                records.append({
                    "chain_id": chain_id, "chain_name": chain, "product_id": pid, "product_code": product["product_code"],
                    "description": product["description"], "variant": product.get("variant_code") or "",
                    "category": category or "UNCLASSIFIED", "category_id": category,
                    "category_name": categories.get(category, "Sin categoría"), "period": month,
                    "objective": "Venta", "value": row["source_value_literal"], "available_at": None,
                    "availability_source": "UNKNOWN", "evidence_mode": "RETROSPECTIVE_TRAINING",
                    "source_id": uuid_value(row["id"]), "version_no": row["version_no"],
                    "source_batch_id": row["source_batch_id"],
                })
    except (ValueError, KeyError, TypeError, AttributeError):
        raise PreviewError("DATA_READ_FAILED", 503) from None
    representative = next(iter(selected.values()))
    product_types = {key(row["product_type"]) for row in selected.values()}
    public_product_type = next(iter(product_types)) if len(product_types) == 1 and product_types <= PUBLIC_PRODUCT_TYPES else None
    return {
        "scope": {"chain_id": chain_id, "chain": chain, "purchase_type": "RS", "format": scope_format,
                  "model": key(representative["model"]), "presentation": representative["size"], "period": period,
                  "public_product_type": public_product_type,
                  "version_id": pin["version_id"], "generation": pin["generation"], "archive_id": ARCHIVE_ID},
        "eligible_variants": len(selected),
        "quality": {"products_classified": len(selected), "products_excluded_ambiguous": unresolved,
                    "products_approved": sum(row["status"] == "APPROVED" for row in selected.values()),
                    "excluded_without_rs_lineage": dict(sorted(excluded.items())), "sales_records": len(records),
                    "sales_products": len({row["product_id"] for row in records}),
                    "temporal_certification": False, "scope_basis": "CATALOG_MODEL_AND_ARCHIVE_RS_LINEAGE",
                    "coverage_basis": "RESOLVABLE_CATALOG_MODEL_ONLY", "model_total_certified": False},
        "metrics": metrics,
        "records": records,
    }
