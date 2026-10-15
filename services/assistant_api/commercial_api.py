"""Read-only commercial facets. Proposals are transient, never catalog writes."""
from datetime import datetime, timezone
import json
from pathlib import Path

from fastapi import Header, Request

from .auth import AuthFailure, READ, authorize
from .commercial_catalog import analyze
from .supabase_access import PreviewError


def commercial_catalog(client, principal, rules):
    chains = client.rows("chains", {"select": "id,name", "order": "id.asc"})
    allowed = set(principal.chain_ids)
    chains = [c for c in chains if c["id"] in allowed]
    categories = client.rows("categories", {"select": "id,chain_id,name", "order": "id.asc"})
    categories = [c for c in categories if c["chain_id"] in allowed]
    products = client.rows("products", {"select": "id,chain_id,category_id,product_code,variant_code,description", "order": "id.asc"})
    products = [p for p in products if p["chain_id"] in allowed]
    versions = client.rows("import_profile_versions", {"select": "id,chain_id,version,mapping_json", "order": "id.asc"})
    versions = [v for v in versions if v["chain_id"] in allowed]
    packet = analyze({"actor": {"status": "ACTIVE"}, "chains": chains, "categories": categories,
        "products": products, "profile_versions": versions, "integrity": {},
        "read_counts": {"chains": len(chains), "categories": len(categories), "products": len(products), "profiles": len(versions)},
        "source": "REQUEST_SCOPED_SUPABASE_RLS", "captured_at": datetime.now(timezone.utc).isoformat()}, rules)
    current = client.rows("product_commercial_classifications_current", {"select": "product_id,chain_id,classification_status,stale_snapshot,color_state,size_state,model_id,color_id,size_id,product_type_id", "order": "product_id.asc"})
    approved = {r["product_id"]: r for r in current if r["chain_id"] in allowed and r["classification_status"] == "APPROVED" and not r["stale_snapshot"]}
    dictionaries = {}
    if approved:
        for field, table in (("model", "commercial_models"), ("color", "commercial_colors"), ("size", "commercial_sizes"), ("product_type", "commercial_product_types")):
            dictionaries[field] = {r["id"]: r["name"] for r in client.rows(table, {"select": "id,name", "order": "id.asc"})}
    output = []
    for row in packet["rows"]:
        decision = approved.get(row["product_id"])
        if decision and decision["chain_id"] != row["chain_id"]:
            raise PreviewError("DATA_READ_FAILED", 503)
        attributes = {}
        for field in ("model", "color", "size", "product_type"):
            if decision:
                identifier = decision[field + "_id"]
                if identifier and identifier not in dictionaries[field]:
                    raise PreviewError("DATA_READ_FAILED", 503)
                attributes[field] = dictionaries[field].get(identifier)
            else:
                attributes[field] = row["proposed_" + field]
        output.append({"product_id": row["product_id"], "chain_id": row["chain_id"], **attributes,
            "not_applicable": [f for f in ("color", "size") if decision and decision.get(f + "_state") == "NOT_APPLICABLE"],
            "status": "APPROVED" if decision else "PENDING_CLASSIFICATION",
            "source": "APPROVED_CATALOG" if decision else "TRANSIENT_PROPOSAL:" + rules["version"],
            "reasons": [] if decision else row["ambiguity_reasons"]})
    return {"products": output, "rules_version": rules["version"], "persisted": False}


def mount_commercial_routes(app, identity, client, limiter):
    rules = json.loads((Path(__file__).resolve().parents[2] / "config/commercial_catalog_rules.json").read_text(encoding="utf-8"))

    @app.get("/api/forecast/commercial-catalog")
    def read_catalog(request: Request, authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise PreviewError("AUTH_REQUIRED", 401)
        token = authorization[7:]
        try:
            principal = identity.authenticate(token=token, actor_id=None, client_host=None)
            authorize(principal, READ)
        except AuthFailure as error:
            raise PreviewError("AUTH_REQUIRED" if error.status_code == 401 else "SCOPE_FORBIDDEN", error.status_code) from None
        request.state.user_id = principal.user_id
        if not limiter.allow("commercial-read:" + principal.user_id, 30):
            raise PreviewError("RATE_001", 429)
        try:
            if client is None:
                raise PreviewError("DATA_READ_FAILED", 503)
            return commercial_catalog(client.bind(token), principal, rules)
        except (ValueError, KeyError, TypeError):
            raise PreviewError("DATA_READ_FAILED", 503) from None
