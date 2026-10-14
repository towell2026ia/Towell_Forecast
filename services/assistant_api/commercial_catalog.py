"""Offline commercial proposals. No database writes or forecasting dependencies."""

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import re
import unicodedata


def key(value):
    """Comparison only; every source label remains intact in the evidence."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).upper().split())


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def words(literal):
    return r"(?<!\w)" + re.escape(key(literal)).replace(r"\ ", r"\s+") + r"(?!\w)"


def prepare(description, rules):
    original = str(description or "")
    # Normalization is an index into the original string, not a replacement label.
    normalized = "".join(key(c) if not c.isspace() else c for c in original)
    if len(normalized) != len(original):
        return {"cleaned": original, "presentations": [], "problems": ["UNICODE_BOUNDARY_REVIEW_REQUIRED"]}
    problems = []
    presentations = []
    work = normalized
    for group in rules["presentations"]:
        for literal in sorted(group["literals"], key=len, reverse=True):
            for match in list(re.finditer(words(literal), work)):
                presentations.append({"group": group["group"],
                                      "original": original[match.start():match.end()]})
                work = work[:match.start()] + " " * (match.end() - match.start()) + work[match.end():]
    for literal in sorted(rules.get("apparel_presentations", []), key=len, reverse=True):
        for match in list(re.finditer(words(literal), work)):
            presentations.append({"group": None, "original": original[match.start():match.end()]})
            work = work[:match.start()] + " " * (match.end() - match.start()) + work[match.end():]
    dimensions = r"(?<!\w)\d+(?:[.,]\d+)?\s*[X×]\s*\d+(?:[.,]\d+)?\s*(?:CM|MM|M)(?!\w)"
    for match in list(re.finditer(dimensions, work)):
        presentations.append({"group": None, "original": original[match.start():match.end()]})
        work = work[:match.start()] + " " * (match.end() - match.start()) + work[match.end():]
    removable = rules["generic_prefixes"] + rules["brands"] + rules["ambiguous_descriptors"]
    # Strip only verified leading whole words, with literal presentation gaps.
    while True:
        match = re.match(r"^[\s-]*(" + "|".join(re.escape(key(x)) for x in removable) + r")(?!\w)", work)
        if not match:
            break
        if match.group(1) in {key(x) for x in rules["ambiguous_descriptors"]}:
            problems.append("TOWEL_FUNCTION_UNVALIDATED")
        work = " " * match.end() + work[match.end():]
    if any(re.search(words(token), work) for token in rules["unresolved_tokens"]):
        problems.append("UNRESOLVED_ABBREVIATION")
    if re.search(r"(?<!\w)(?:SS|FW|AW|SP)\d{2}(?!\w)|\b(?:20\d{2})\b", work):
        problems.append("SEASON_CODE_UNVALIDATED")
    if "(" in work or ")" in work:
        problems.append("LEGACY_OR_PARENTHETICAL_NAME")
    if len({(p["group"], key(p["original"])) for p in presentations}) > 1:
        problems.append("MULTIPLE_PRESENTATION_LITERALS")
    positions = [i for i, c in enumerate(work) if c.strip() and c != "-"]
    start, end = (positions[0], positions[-1] + 1) if positions else (0, 0)
    cleaned = original[start:end]
    # Removed interior presentation words are omitted only from the proposal.
    cleaned = "".join(original[i] if work[i] != " " else " " for i in range(start, end))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
    return {"cleaned": cleaned, "presentations": presentations, "problems": sorted(set(problems))}


def split_color(cleaned, colors):
    normalized = key(cleaned)
    for color in sorted(colors, key=len, reverse=True):
        if normalized.endswith(" " + color) or normalized.endswith("-" + color):
            # NFKD can change lengths for unusual glyphs. Never fabricate labels.
            tokens = re.split(r"[\s-]+", cleaned)
            color_words = len(re.split(r"[\s-]+", color))
            raw_color = " ".join(tokens[-color_words:])
            if key(raw_color) == color:
                model = " ".join(tokens[:-color_words]).strip(" -")
                if model:
                    return model, raw_color
    return None, None


def learn_colors(prepared, rules):
    colors = {key(x): {"source": "USER_COLOR_EXAMPLE", "labels": {x}}
              for x in rules["verified_color_examples"]}
    # Contrastive suffixes are candidate colors, never approved equivalences.
    for _ in range(2):
        models = {}
        for entry in prepared:
            model, _ = split_color(entry["cleaned"], colors)
            if model and not re.search(r"\d|[()/]", model) and not any(
                    re.search(words(t), key(model)) for t in rules["generic_prefixes"]
                    + rules["unresolved_suffix_terms"] + rules["ambiguous_descriptors"]
            ) and not any(p in entry["problems"] for p in
                                 ("UNRESOLVED_ABBREVIATION", "SEASON_CODE_UNVALIDATED",
                                  "LEGACY_OR_PARENTHETICAL_NAME")):
                models[key(model)] = model
        additions = defaultdict(set)
        for entry in prepared:
            cleaned = entry["cleaned"]
            norm = key(cleaned)
            for model in sorted(models, key=len, reverse=True):
                if norm.startswith(model + " ") or norm.startswith(model + "-"):
                    count = len(re.split(r"[\s-]+", model))
                    suffix = " ".join(re.split(r"[\s-]+", cleaned)[count:]).strip()
                    if (suffix and len(suffix.split()) <= 4
                            and not re.search(r"\d|[()/]", suffix)
                            and not any(re.search(words(t), key(suffix)) for t in
                                        rules["unresolved_suffix_terms"] + rules["unresolved_tokens"])
                            and not entry["problems"]):
                        additions[key(suffix)].add(suffix)
                    break
        for candidate, labels in additions.items():
            colors.setdefault(candidate, {"source": "CORPUS_CONTRAST_PROPOSAL", "labels": set()})["labels"].update(labels)
    return colors


def analyze(catalog, rules):
    catalog = deepcopy(catalog)
    if rules.get("approval_scope") != "PROPOSALS_ONLY":
        raise ValueError("Only proposal rules are accepted")
    if catalog.get("actor", {}).get("status") != "ACTIVE":
        raise ValueError("Catalog must come from an active RLS identity")
    products = catalog["products"]
    ids = {p["id"] for p in products}
    if len(ids) != len(products) or len(products) != catalog["read_counts"]["products"]:
        raise ValueError("Incomplete or duplicate product inventory")
    for field in ("chains", "categories"):
        if len(catalog[field]) != catalog["read_counts"][field]:
            raise ValueError("Incomplete " + field + " inventory")
    if catalog["read_counts"].get("profiles", len(catalog["profile_versions"])) != len(catalog["profile_versions"]):
        raise ValueError("Incomplete import metadata versions")
    chains = {c["id"]: c for c in catalog["chains"]}
    categories = {c["id"]: c for c in catalog["categories"]}
    metadata = defaultdict(list)
    for version in catalog["profile_versions"]:
        for product in version.get("mapping_json", {}).get("products", []):
            if product.get("id") in ids:
                metadata[product["id"]].append({"profile_version_id": version["id"],
                                                "profile_version": version["version"], "product": product})
    prepared = {}
    evidence = {}
    identifiers = defaultdict(set)
    for product in products:
        if product["chain_id"] not in chains:
            raise ValueError("Product references unauthorized chain")
        category = categories.get(product.get("category_id"))
        if product.get("category_id") and (not category or category["chain_id"] != product["chain_id"]):
            raise ValueError("Incompatible category and product scope")
        aliases = {product["description"]}
        pairs = set()
        historical_categories = set()
        problems = []
        for source in metadata[product["id"]]:
            item = source["product"]
            if item.get("chain_id") != product["chain_id"]:
                raise ValueError("Metadata crosses product scope")
            if item.get("product_code") != product["product_code"] or item.get("variant_code") != product.get("variant_code"):
                problems.append("METADATA_IDENTITY_REVIEW_REQUIRED")
            aliases.update(item.get("description_aliases", []))
            historical_categories.update(item.get("historical_category_names", []))
            for pair in item.get("identifier_mappings", []):
                if len(pair) != 2 or any(v is not None and not isinstance(v, str) for v in pair):
                    raise ValueError("ITEM/UPC must be opaque strings or null")
                pairs.add(tuple(pair))
        for item, upc in pairs:
            for kind, value in (("ITEM", item), ("UPC", upc)):
                if value is not None and value.strip():
                    identifiers[(product["chain_id"], kind, value)].add(product["id"])
        if not pairs:
            problems.append("NO_DOCUMENTED_IDENTIFIER_MAPPING")
        if any(v is not None and not v.strip() for pair in pairs for v in pair):
            problems.append("EMPTY_IDENTIFIER_IN_SOURCE")
        if product.get("category_id") is None:
            problems.append("CATEGORY_UNRESOLVED")
        if len(historical_categories) > 1:
            problems.append("MULTIPLE_HISTORICAL_CATEGORIES")
        if product.get("variant_code") or product.get("variant_description"):
            problems.append("VARIANT_EQUIVALENCE_UNVALIDATED")
        prepared[product["id"]] = {a: prepare(a, rules) for a in sorted(aliases) if a}
        evidence[product["id"]] = {"aliases": sorted(aliases), "identifier_mappings":
            [list(p) for p in sorted(pairs, key=lambda p: (p[0] or "", p[1] or ""))],
            "historical_category_names": sorted(historical_categories), "problems": problems,
            "profile_version_ids": sorted({s["profile_version_id"] for s in metadata[product["id"]]})}
    colors = learn_colors([v for entries in prepared.values() for v in entries.values()], rules)
    collisions = [{"chain_id": chain, "kind": kind, "identifier": value,
                   "product_ids": sorted(pids)} for (chain, kind, value), pids in identifiers.items() if len(pids) > 1]
    collision_products = {pid for c in collisions for pid in c["product_ids"]}
    rows = []
    for product in sorted(products, key=lambda p: (chains[p["chain_id"]]["name"], p["description"], p["id"])):
        proofs = []
        ev = evidence[product["id"]]
        problems = set(ev["problems"])
        for alias, entry in prepared[product["id"]].items():
            model, color = split_color(entry["cleaned"], colors)
            presentations = entry["presentations"]
            proof = {"description": alias, "model": model, "color": color,
                     "presentations": presentations, "problems": entry["problems"],
                     "color_source": colors.get(key(color), {}).get("source")}
            if model and (re.search(r"\d|[()/]", model) or any(re.search(words(t), key(model)) for t in
                    rules["unresolved_suffix_terms"] + rules["generic_prefixes"] + rules["ambiguous_descriptors"])):
                proof["problems"] = sorted(set(proof["problems"] + ["MODEL_BOUNDARY_UNVALIDATED"]))
            proofs.append(proof)
            problems.update(proof["problems"])
        candidate_pairs = {(key(p["model"]), key(p["color"])) for p in proofs if p["model"] and p["color"]}
        if len(candidate_pairs) > 1:
            problems.add("DESCRIPTION_ALIAS_CONFLICT")
        current = next((p for p in proofs if p["description"] == product["description"]), None)
        chosen = current if current and current["model"] else next((p for p in proofs if p["model"]), current)
        chosen = chosen or {"model": None, "color": None, "presentations": [], "color_source": None}
        model_proposal = chosen["model"]
        if any(x in chosen.get("problems", []) for x in ("UNRESOLVED_ABBREVIATION", "SEASON_CODE_UNVALIDATED",
                    "MODEL_BOUNDARY_UNVALIDATED", "UNICODE_BOUNDARY_REVIEW_REQUIRED")):
            model_proposal = None
        if not model_proposal:
            problems.add("INSUFFICIENT_MODEL_COLOR_BOUNDARY_EVIDENCE")
        if not chosen["color"]:
            problems.add("COLOR_UNKNOWN_NOT_NOT_APPLICABLE")
        size_options = {(s["group"], key(s["original"])) for p in proofs for s in p["presentations"]}
        size_groups = {group or label for group, label in size_options}
        if len(size_groups) > 1:
            problems.add("PRESENTATION_ALIAS_CONFLICT")
        size = chosen["presentations"][0] if len(chosen["presentations"]) == 1 else None
        if not size:
            problems.add("SIZE_APPLICABILITY_UNDOCUMENTED")
        if product["id"] in collision_products:
            problems.add("SAME_SCOPE_IDENTIFIER_COLLISION")
        if chosen.get("color_source") == "CORPUS_CONTRAST_PROPOSAL":
            problems.add("COLOR_CONTRAST_REQUIRES_COMMERCIAL_APPROVAL")
        problems.add("PRODUCT_MAPPING_APPROVAL_REQUIRED")
        rows.append({"chain_id": product["chain_id"], "chain": chains[product["chain_id"]]["name"],
            "category_id": product.get("category_id"), "category": categories.get(product.get("category_id"), {}).get("name"),
            "product_id": product["id"], "product_code": product["product_code"], "description": product["description"],
            "variant_code": product.get("variant_code"), "variant_description": product.get("variant_description"),
            "upc": sorted({p[1] for p in ev["identifier_mappings"] if p[1] is not None and p[1].strip()}),
            "item": sorted({p[0] for p in ev["identifier_mappings"] if p[0] is not None and p[0].strip()}),
            "proposed_model": model_proposal, "tentative_model_boundary": chosen["model"], "proposed_color": chosen["color"],
            "proposed_size": size["original"] if size else None,
            "presentation_group": size["group"] if size else None,
            "model_state": "KNOWN" if model_proposal else "UNKNOWN",
            "color_state": "KNOWN" if chosen["color"] else "UNKNOWN",
            "size_state": "KNOWN" if size else "UNKNOWN",
            "classification_status": "PENDING_CLASSIFICATION", "proposal_status": "PROPOSED" if model_proposal else "INSUFFICIENT_EVIDENCE",
            "classification_source": "SUPABASE_PRODUCT_AND_VERSIONED_IMPORT_METADATA",
            "classification_version": 1, "rules_version": rules["version"], "approved_by": None, "approved_at": None,
            "ambiguity_reasons": sorted(problems), "analyzed": True,
            "evidence": {**ev, "description_proposals": proofs}, "source_product_hash": digest(product)})
    if {r["product_id"] for r in rows} != ids:
        raise ValueError("Analysis coverage is not 100 percent")
    inventory = {}
    for field, label in (("proposed_model", "models"), ("proposed_color", "colors"), ("proposed_size", "sizes")):
        groups = defaultdict(list)
        for row in rows:
            if row[field]:
                groups[key(row[field])].append(row)
        inventory[label] = [{"comparison_key": k, "original_labels": sorted({r[field] for r in members}),
                             "product_ids": sorted(r["product_id"] for r in members),
                             "chains": sorted({r["chain"] for r in members}), "products": len(members),
                             "status": "PROPOSED_NOT_APPROVED"} for k, members in sorted(groups.items())]
    counts = Counter(r["chain_id"] for r in rows)
    reasons = Counter(reason for r in rows for reason in r["ambiguity_reasons"])
    return {"verdict": "READY_FOR_MAPPING_APPROVAL", "rules": rules, "rules_hash": digest(rules),
        "captured_at": catalog["captured_at"], "source": catalog["source"], "integrity_before": catalog["integrity"],
        "source_catalog_hash": digest(catalog), "mapping_hash": digest(rows), "rows": rows, "inventories": inventory,
        "chains": [{**c, "products": counts[c["id"]]} for c in catalog["chains"]], "categories": catalog["categories"],
        "identifier_collisions": collisions,
        "metrics": {"products_analyzed": len(rows), "source_products": len(products), "analysis_coverage": 1.0,
                    "approved_products": 0, "pending_products": len(rows), "approval_coverage": 0.0,
                    "products_with_proposals": sum(r["proposal_status"] == "PROPOSED" for r in rows),
                    "insufficient_evidence": sum(r["proposal_status"] == "INSUFFICIENT_EVIDENCE" for r in rows),
                    "ambiguous_products": sum(any(x != "PRODUCT_MAPPING_APPROVAL_REQUIRED" for x in r["ambiguity_reasons"]) for r in rows),
                    "models_identified_as_candidates": len(inventory["models"]),
                    "colors_identified_as_candidates": len(inventory["colors"]),
                    "sizes_identified_as_candidates": len(inventory["sizes"]),
                    "reason_counts": dict(sorted(reasons.items()))}}
