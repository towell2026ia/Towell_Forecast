"""Exact-barcode candidate audit; labels supply hints, never an approved mapping."""
import unicodedata


def label(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value).upper() if c.isalnum())


def reconcile(products, snapshot, current):
    ids = [p["id"] for p in products]
    if len(ids) != len(set(ids)):
        raise ValueError("DUPLICATE_PRIVATE_IDENTITY")
    live = {(p[0], p[1]): p for p in current}
    candidates = {}
    for profile in snapshot["profiles"]:
        scope = profile["mapping_json"]["scope"]
        for p in profile["mapping_json"]["products"]:
            if (p["id"], p["chain_id"]) not in live:
                continue
            # UPC is the second element. ITEM must never masquerade as a UPC.
            upcs = {str(pair[1]) for pair in p.get("identifier_mappings", []) if len(pair) == 2 and pair[1]}
            for upc in upcs:
                candidates.setdefault(upc, {})[(p["id"], p["chain_id"])] = {
                    "product_id": p["id"], "chain_id": p["chain_id"], "scope": scope["code"],
                    "scope_type": scope["scope_type"], "description": p["description"],
                    "description_aliases": p.get("description_aliases", []),
                    "product_code": p["product_code"], "items": [pair[0] for pair in p.get("identifier_mappings", []) if len(pair) == 2 and str(pair[1]) == upc],
                    "status": live[(p["id"], p["chain_id"])][3], "profile_id": profile["id"],
                }
    result = []
    for p in products:
        if p["id"] != p["account"] + "|" + p["barcode"]:
            raise ValueError("PRIVATE_IDENTITY_MISMATCH")
        found = list(candidates.get(p["barcode"], {}).values())
        enriched = []
        for c in found:
            descriptions = [c["description"], *c["description_aliases"]]
            enriched.append({**c,
                "format_hint": label(p["format"]) == label(c["scope"].split("::")[-1]),
                "description_exact_normalized": any(label(p["description"]) == label(d) for d in descriptions),
                "model_text_hint": any(label(p["model"]) in label(d) for d in descriptions),
                "color_text_hint": any(label(p["color"]) in label(d) for d in descriptions),
                "presentation_text_hint": any(label(p["size"]) in label(d) for d in descriptions) if p["size"] else None})
        hinted = sum(c["format_hint"] for c in enriched)
        status = "NO_EXACT_UPC" if not found else "MULTIPLE_CANDIDATES" if len(found) > 1 else "ONE_UPC_CANDIDATE"
        result.append({**{k: p[k] for k in ("id", "account", "chain", "format", "barcode", "description", "model", "size", "color", "commercial_code")},
                       "candidate_status": status, "candidate_count": len(found), "format_hint_count": hinted,
                       "integration_status": "BLOCKED", "mapping_status": "PENDING_CLASSIFICATION",
                       "reason": "No exact UPC in current profile/catalog" if not found else "Account/scope and commercial attributes require documented approval; text hints are not identity evidence",
                       "candidates": enriched})
    return result
