"""Read the official commercial source for one authorized RS selection."""
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation, localcontext
import re

from .auth import READ
from .commercial_catalog import key
from .supabase_access import PreviewError, uuid_value


METRICS = ("SALES", "ORDER", "DELIVERY", "FCST_TOWELL_HISTORY", "FCST_CLIENTE_HISTORY")
PERIOD = re.compile(r"20\d\d-(?:0[1-9]|1[0-2])")
PUBLIC_PRODUCT_TYPES = {"TOALLA", "TAPETE", "TRAPO", "BATA"}


def _format(value):
    # Match the import scope's existing normalization, without guessing aliases.
    return re.sub(r"[\s_-]+", "", str(value or "").strip().upper())


def _model_scope(client, chain_id, requested_format, model, presentation, period):
    native = client.catalog_rows(chain_id=chain_id, as_of=period)
    if any(row["chain_id"] != chain_id or row["purchase_type"] != "RS" for row in native):
        raise PreviewError("DATA_READ_FAILED", 503)
    formatted = [row for row in native if _format(row["format"]) == _format(requested_format)]
    if not formatted:
        raise PreviewError("RS_FORMAT_NOT_VERIFIED", 409)
    matching = [row for row in formatted if key(row["model"]) == key(model)]
    if presentation:
        matching = [row for row in matching if key(row["presentation"]) == key(presentation)]
    selected = {uuid_value(row["product_id"]): {**row, "size": row["presentation"], "status": "SOURCE_DOCUMENTED"}
                for row in matching}
    if len(selected) != len(matching):
        raise PreviewError("RS_MODEL_PRESENTATION_AMBIGUOUS", 409)
    if not selected:
        raise PreviewError("RS_MODEL_NOT_VERIFIED", 409)
    presentations = {key(row["size"]) for row in selected.values()}
    if len(presentations) != 1 or "" in presentations:
        raise PreviewError("RS_MODEL_PRESENTATION_AMBIGUOUS", 409)
    products = {row["id"]: row for row in client.rows("products", {
        "select": "id,chain_id,product_code,variant_code,description,category_id",
        "chain_id": f"eq.{chain_id}", "status": "eq.ACTIVE", "order": "id.asc"})}
    chains = client.rows("chains", {"select": "id,name", "id": f"eq.{chain_id}"})
    categories = {row["id"]: row["name"] for row in client.rows("categories", {
        "select": "id,name", "chain_id": f"eq.{chain_id}", "order": "id.asc"})}
    if (len(chains) != 1 or chains[0]["id"] != chain_id or not selected.keys() <= products.keys()
            or any(row["chain_id"] != chain_id for row in products.values())):
        raise PreviewError("DATA_READ_FAILED", 503)
    return chains[0]["name"], formatted[0]["format"], selected, products, categories, 0


def _exact(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", value):
            raise ValueError()
        parsed = Decimal(value)
        if not parsed.is_finite() or parsed < 0:
            raise ValueError()
        return parsed
    except (InvalidOperation, ValueError):
        raise PreviewError("DATA_READ_FAILED", 503) from None


def _month_rows(start, end):
    year, month = map(int, start.split("-"))
    while (period := f"{year:04d}-{month:02d}") <= end:
        yield period
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def _read_facts(client, chain_id, selected, period, scope_format, first_period):
    facts, inventory, excluded, seen = defaultdict(dict), {}, Counter(), set()
    ids = sorted(selected)
    for offset in range(0, len(ids), 50):
        batch = ids[offset:offset + 50]
        observations = client.observation_rows({
            "select": "id,chain_id,product_id,period,metric_code,value_text,version_no,availability_source,available_at,source_batch_id,source_reference,source_value_literal",
            "p_product_ids": "{" + ",".join(batch) + "}", "p_from": first_period + "-01",
            "p_to": period + "-01", "p_metric_codes": "{" + ",".join(METRICS) + "}",
            "p_format": scope_format, "p_purchase_type": "RS",
            "order": "product_id.asc,period.asc,metric_code.asc,id.asc",
        }, chain_id=chain_id)
        for row in observations:
            product, date, metric = row["product_id"], row["period"], row["metric_code"]
            month = date[:7]
            identity = product, month, metric
            if (row["chain_id"] != chain_id or product not in batch or metric not in METRICS or identity in seen
                    or not PERIOD.fullmatch(month) or date != month + "-01" or not first_period <= month <= period):
                raise PreviewError("DATA_READ_FAILED", 503)
            seen.add(identity)
            source, literal = row.get("source_reference"), row.get("source_value_literal")
            if not isinstance(source, str) or not source.strip():
                excluded[metric] += 1
                continue
            if _exact(literal) != _exact(row["value_text"]):
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
                    or not isinstance(row.get("source_reference"), str) or not row["source_reference"].strip()):
                raise PreviewError("DATA_READ_FAILED", 503)
            inventory[product] = _exact(row["quantity_text"])
    return facts, inventory, excluded


def _summary(facts, inventory, expected, period, first_period):
    by_month = []
    with localcontext() as context:
        context.prec = 50
        for month in _month_rows(first_period, period):
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


def load_rs_scope(client, principal, *, chain_id, purchase_type, format, model, period, presentation=None):
    """Use the current source contract and the caller's chain permissions."""
    chain_id = uuid_value(chain_id)
    if READ not in principal.permissions or chain_id not in principal.chain_ids:
        raise PreviewError("SCOPE_FORBIDDEN", 403)
    if (purchase_type != "RS" or not isinstance(period, str) or not PERIOD.fullmatch(period)
            or not isinstance(format, str) or not _format(format)
            or not isinstance(model, str) or not key(model)):
        raise PreviewError("RS_SCOPE_INVALID")
    context = client.operational_context()
    first_period = context["first_period"]
    if not first_period <= period <= context["last_period"]:
        raise PreviewError("RS_SCOPE_INVALID")
    chain, scope_format, selected, products, categories, unresolved = _model_scope(client, chain_id, format, model, presentation, period)
    try:
        facts, inventory, excluded = _read_facts(client, chain_id, selected, period, scope_format, first_period)
        metrics = _summary(facts, inventory, len(selected) + unresolved, period, first_period)
        records = []
        for month, rows in sorted(facts.items()):
            for (pid, metric), row in sorted(rows.items()):
                if metric != "SALES":
                    continue
                product = products[pid]
                category = product.get("category_id")
                records.append({
                    "chain_id": chain_id, "chain_name": chain, "product_id": pid, "product_code": product["product_code"],
                    "description": product["description"], "variant": selected[pid].get("color") or "",
                    "category": selected[pid].get("product_type") or "UNCLASSIFIED", "category_id": category,
                    "category_name": selected[pid].get("product_type") or categories.get(category, "Sin categoría"), "period": month,
                    "objective": "Venta", "value": row["source_value_literal"], "available_at": row["available_at"],
                    "availability_source": row["availability_source"], "evidence_mode": "RETROSPECTIVE_TRAINING",
                    "source_id": uuid_value(row["id"]), "version_no": row["version_no"],
                    "source_batch_id": row["source_batch_id"],
                })
    except (ValueError, KeyError, TypeError, AttributeError):
        raise PreviewError("DATA_READ_FAILED", 503) from None
    representative = next(iter(selected.values()))
    product_types = {key(row["product_type"]).split(" ")[0] for row in selected.values()}
    public_product_type = next(iter(product_types)) if len(product_types) == 1 and product_types <= PUBLIC_PRODUCT_TYPES else None
    return {
        "scope": {"chain_id": chain_id, "chain": chain, "purchase_type": "RS", "format": scope_format,
                  "model": key(representative["model"]), "presentation": representative["size"], "period": period,
                  "public_product_type": public_product_type,
                  "source_hash": context["source_hash"], "generation": context["generation"]},
        "eligible_variants": len(selected),
        "quality": {"products_classified": len(selected), "products_excluded_ambiguous": unresolved,
                    "products_approved": sum(row["status"] == "APPROVED" for row in selected.values()),
                    "excluded_without_rs_lineage": dict(sorted(excluded.items())), "sales_records": len(records),
                    "sales_products": len({row["product_id"] for row in records}),
                    "temporal_certification": False, "scope_basis": "CATALOG_MODEL_AND_OFFICIAL_RS_LINEAGE",
                    "coverage_basis": "RESOLVABLE_CATALOG_MODEL_ONLY", "model_total_certified": False},
        "metrics": metrics,
        "records": records,
    }
