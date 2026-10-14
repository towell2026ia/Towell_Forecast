import json
from pathlib import Path
import unittest

from services.assistant_api.commercial_catalog import analyze, digest, homologate_size, key, prepare, product_type_evidence
from scripts.prepare_commercial_mapping import prepare as prepare_load, render_sql

RULES = json.loads((Path(__file__).resolve().parents[2] / "config/commercial_catalog_rules.json").read_text(encoding="utf-8"))


def fixture(descriptions, chain="a"):
    products = [{"id": str(i), "chain_id": chain, "category_id": "cat-" + chain,
                 "product_code": "code-" + str(i), "description": text,
                 "variant_code": None, "variant_description": None} for i, text in enumerate(descriptions)]
    metadata = [{**p, "description_aliases": [p["description"]], "identifier_mappings": [["item-" + p["id"], "00" + p["id"]]],
                 "historical_category_names": ["Toalla"]} for p in products]
    return {"captured_at": "2026-10-14T00:00:00Z", "source": "ISOLATED_UNIT_FIXTURE", "actor": {"status": "ACTIVE"},
            "products": products, "chains": [{"id": chain, "name": chain}],
            "categories": [{"id": "cat-" + chain, "chain_id": chain, "name": "Toalla"}],
            "read_counts": {"products": len(products), "chains": 1, "categories": 1},
            "profile_versions": [{"id": "v1", "version": 1, "mapping_json": {"products": metadata}}], "integrity": {"observations": 0}}


class CommercialCatalogTests(unittest.TestCase):
    def packet(self, texts):
        return analyze(fixture(texts), RULES)

    def test_fendi_oxford_is_color_not_model(self):
        row = self.packet(["TOALLA MB FENDI OXFORD"])["rows"][0]
        self.assertEqual((row["proposed_model"], row["proposed_color"], row["proposed_size"]), ("FENDI", "OXFORD", "MEDIO BAÑO"))
        self.assertEqual(row["original_size"], "MB")

    def test_fendi_blue(self):
        self.assertEqual(self.packet(["TOALLA MB FENDI AZUL"])["rows"][0]["proposed_color"], "AZUL")

    def test_multiword_model_without_closed_list(self):
        for model in ("GOOD CRISTY", "SOFT DRY", "UNLISTED DESIGN", "LEGADO"):
            with self.subTest(model=model):
                self.assertEqual(self.packet([f"TOALLA MB {model} GRIS"])["rows"][0]["proposed_model"], model)

    def test_compound_color_learned_from_corpus(self):
        rows = self.packet(["Toalla MB Nuevo Gris", "Toalla MB Nuevo Azul Marino", "Toalla MB Otro Azul Marino"])["rows"]
        self.assertEqual({r["proposed_color"] for r in rows}, {"Gris", "Azul Marino"})
        self.assertIn("Otro", {r["proposed_model"] for r in rows})
        self.assertTrue(all(r["classification_status"] == "PENDING_CLASSIFICATION" for r in rows))

    def test_four_presentations_for_both_examples_and_another_model(self):
        for model in ("Fiore", "Granada", "Unlisted"):
            rows = self.packet([f"Toalla {presentation} {model} Gris" for presentation in ("Facial", "Medio Baño", "Baño", "Pullman")])["rows"]
            self.assertEqual({r["presentation_group"] for r in rows}, {"FACIAL", "MEDIO_BANO", "BANO", "PULLMAN"})
            self.assertEqual({r["proposed_model"] for r in rows}, {model})

    def test_original_presentation_aliases_preserved(self):
        rows = self.packet(["Toalla Fiore-Mb Gris", "Toalla Fiore-Pull Gris"])["rows"]
        self.assertEqual({r["proposed_size"] for r in rows}, {"MEDIO BAÑO", "PULLMAN"})
        self.assertEqual({r["original_size"] for r in rows}, {"Mb", "Pull"})

    def test_no_size_is_unknown_not_not_applicable(self):
        row = self.packet(["Toalla Nuevo Gris"])["rows"][0]
        self.assertIsNone(row["proposed_size"])
        self.assertEqual(row["size_state"], "UNKNOWN")

    def test_unknown_color_and_ambiguous_assortment(self):
        for text in ("Toalla Nuevo", "Toalla MB Nuevo Navidad", "Toalla MB Nuevo Varios Colores"):
            row = self.packet([text])["rows"][0]
            self.assertIsNone(row["proposed_color"])
            self.assertEqual(row["color_state"], "UNKNOWN")

    def test_generic_type_and_brand_whole_words_only(self):
        self.assertEqual(prepare("TOALLA TOWELL MB Nuevo Gris", RULES)["cleaned"], "Nuevo Gris")
        self.assertEqual(prepare("TOALLALUZ MB Nuevo Gris", RULES)["cleaned"], "TOALLALUZ Nuevo Gris")
        self.assertEqual(prepare("Bata", RULES)["cleaned"], "")

    def test_towel_not_assigned_as_model(self):
        row = self.packet(["Toalla Towel Fiore Facial Gris"])["rows"][0]
        self.assertEqual(row["proposed_model"], "Fiore")
        self.assertIn("TOWEL_FUNCTION_UNVALIDATED", row["ambiguity_reasons"])

    def test_opaque_prefixes_and_season_do_not_become_models(self):
        for text in ("MS FIORE MB GRIS", "SS25 TOALLA MB FIORE GRIS", "612 BATA C/BIES GRIS"):
            row = self.packet([text])["rows"][0]
            self.assertIsNone(row["proposed_model"])
            self.assertEqual(row["proposal_status"], "INSUFFICIENT_EVIDENCE")

    def test_explicit_units_only_no_code_inference(self):
        explicit = self.packet(["Toalla Nuevo Gris 90x150 cm"])["rows"][0]
        self.assertEqual(explicit["proposed_size"], "90x150 cm")
        opaque = self.packet(["Toalla Nuevo 612 Gris"])["rows"][0]
        self.assertIsNone(opaque["proposed_size"])

    def test_unusual_unicode_does_not_corrupt_source_positions(self):
        entry = prepare("Toalla Straße MB Gris", RULES)
        self.assertIn("UNICODE_BOUNDARY_REVIEW_REQUIRED", entry["problems"])

    def test_originals_and_identifiers_are_unchanged(self):
        catalog = fixture(["Toalla MB Nuevo Orquídea"])
        before = digest(catalog)
        row = analyze(catalog, RULES)["rows"][0]
        self.assertEqual(digest(catalog), before)
        self.assertEqual(row["upc"], ["000"])
        self.assertEqual(row["item"], ["item-0"])
        self.assertEqual(row["description"], catalog["products"][0]["description"])

    def test_alias_conflicts_remain_pending(self):
        catalog = fixture(["Toalla MB Fendi Gris"])
        catalog["profile_versions"][0]["mapping_json"]["products"][0]["description_aliases"].append("Toalla MB Fendi Azul")
        row = analyze(catalog, RULES)["rows"][0]
        self.assertIn("DESCRIPTION_ALIAS_CONFLICT", row["ambiguity_reasons"])
        self.assertEqual(len(row["evidence"]["description_proposals"]), 2)

    def test_multiple_presentations_are_not_collapsed(self):
        row = self.packet(["Toalla Facial MB Nuevo Gris"])["rows"][0]
        self.assertIsNone(row["proposed_size"])
        self.assertIn("MULTIPLE_PRESENTATION_LITERALS", row["ambiguity_reasons"])

    def test_empty_identifiers_are_missing_not_duplicates(self):
        catalog = fixture(["Toalla MB Nuevo Gris", "Toalla MB Nuevo Azul"])
        for p in catalog["profile_versions"][0]["mapping_json"]["products"]:
            p["identifier_mappings"] = [["", ""]]
        packet = analyze(catalog, RULES)
        self.assertEqual(packet["identifier_collisions"], [])
        self.assertTrue(all("EMPTY_IDENTIFIER_IN_SOURCE" in r["ambiguity_reasons"] for r in packet["rows"]))

    def test_duplicate_upc_item_scoped_without_merging(self):
        catalog = fixture(["Toalla MB Nuevo Gris", "Toalla MB Nuevo Azul"])
        catalog["profile_versions"][0]["mapping_json"]["products"][1]["identifier_mappings"] = [["item-0", "000"]]
        packet = analyze(catalog, RULES)
        self.assertEqual(len(packet["identifier_collisions"]), 2)
        self.assertEqual(len(packet["rows"]), 2)

    def test_multi_chain_shared_model_color_not_identity(self):
        catalog = fixture(["Toalla MB Nuevo Gris"])
        other = fixture(["Toalla MB Nuevo Gris"], chain="b")
        other["products"][0]["id"] = "second"
        other["profile_versions"][0]["mapping_json"]["products"][0]["id"] = "second"
        catalog["chains"] += other["chains"]
        catalog["categories"] += other["categories"]
        catalog["products"] += other["products"]
        catalog["profile_versions"] += other["profile_versions"]
        catalog["read_counts"] = {"products": 2, "chains": 2, "categories": 2}
        packet = analyze(catalog, RULES)
        self.assertEqual(len(packet["inventories"]["models"]), 1)
        self.assertEqual(packet["identifier_collisions"], [])
        self.assertEqual(len(packet["rows"]), 2)

    def test_null_category_and_unvalidated_variant_are_explained(self):
        catalog = fixture(["Toalla MB Nuevo Gris"])
        catalog["products"][0].update(category_id=None, variant_code="opaque")
        catalog["profile_versions"][0]["mapping_json"]["products"][0]["historical_category_names"].append("Other")
        row = analyze(catalog, RULES)["rows"][0]
        for reason in ("CATEGORY_UNRESOLVED", "MULTIPLE_HISTORICAL_CATEGORIES", "VARIANT_EQUIVALENCE_UNVALIDATED"):
            self.assertIn(reason, row["ambiguity_reasons"])

    def test_full_coverage_future_product_and_zero_product_scope(self):
        catalog = fixture(["Toalla MB Futuro Gris", "Producto sin evidencia"])
        catalog["chains"].append({"id": "parent", "name": "Parent"})
        catalog["read_counts"]["chains"] = 2
        packet = analyze(catalog, RULES)
        self.assertEqual(packet["metrics"]["analysis_coverage"], 1)
        self.assertEqual(packet["metrics"]["approved_products"], 0)
        self.assertEqual(packet["chains"][1]["products"], 0)

    def test_fail_closed_on_bad_or_incomplete_inputs(self):
        cases = []
        for field in ("products", "chains", "categories"):
            c = fixture(["Toalla MB Nuevo Gris"])
            c["read_counts"][field] += 1
            cases.append(c)
        c = fixture(["Toalla MB Nuevo Gris"])
        c["actor"]["status"] = "INACTIVE"
        cases.append(c)
        c = fixture(["Toalla MB Nuevo Gris"])
        c["products"][0]["chain_id"] = "other"
        cases.append(c)
        c = fixture(["Toalla MB Nuevo Gris"])
        c["categories"][0]["chain_id"] = "other"
        cases.append(c)
        c = fixture(["Toalla MB Nuevo Gris"])
        c["profile_versions"][0]["mapping_json"]["products"][0]["chain_id"] = "other"
        cases.append(c)
        c = fixture(["Toalla MB Nuevo Gris"])
        c["profile_versions"][0]["mapping_json"]["products"][0]["identifier_mappings"] = [[123, None]]
        cases.append(c)
        for c in cases:
            with self.assertRaises(ValueError):
                analyze(c, RULES)
        with self.assertRaises(ValueError):
            analyze(fixture([]), {**RULES, "approval_scope": "AUTO_APPROVE"})

    def test_no_metadata_and_no_description_never_omitted(self):
        catalog = fixture([""])
        catalog["profile_versions"] = []
        row = analyze(catalog, RULES)["rows"][0]
        self.assertIn("NO_DOCUMENTED_IDENTIFIER_MAPPING", row["ambiguity_reasons"])
        self.assertEqual(row["classification_status"], "PENDING_CLASSIFICATION")

    def test_reproducible_packet(self):
        catalog = fixture(["Toalla MB Nuevo Gris"])
        self.assertEqual(analyze(catalog, RULES)["mapping_hash"], analyze(catalog, RULES)["mapping_hash"])
        self.assertEqual(key("  Orquídea  "), "ORQUIDEA")

    def test_missing_version_or_changed_metadata_identity_is_not_silent(self):
        catalog = fixture(["Toalla MB Nuevo Gris"])
        catalog["read_counts"]["profiles"] = 2
        with self.assertRaises(ValueError):
            analyze(catalog, RULES)
        catalog["read_counts"]["profiles"] = 1
        catalog["profile_versions"][0]["mapping_json"]["products"][0]["product_code"] = "different"
        self.assertIn("METADATA_IDENTITY_REVIEW_REQUIRED", analyze(catalog, RULES)["rows"][0]["ambiguity_reasons"])

    def test_user_size_equivalences_are_display_names(self):
        for literal, expected in [("Mb", "MEDIO BAÑO"), ("MEDIO BANO", "MEDIO BAÑO"),
                                  ("BANO", "BAÑO"), ("baño", "BAÑO"), ("PULL", "PULLMAN"),
                                  ("Pullman", "PULLMAN"), ("FACIAL", "FACIAL")]:
            self.assertEqual(homologate_size(literal, RULES), expected)
        self.assertIsNone(homologate_size(None, RULES))
        self.assertIsNone(homologate_size("unverified", RULES))

    def test_duplicate_equivalent_literals_do_not_conflict(self):
        row = self.packet(["Toalla MB Medio Baño Fendi Gris"])["rows"][0]
        self.assertEqual(row["proposed_size"], "MEDIO BAÑO")
        self.assertNotIn("MULTIPLE_PRESENTATION_LITERALS", row["ambiguity_reasons"])

    def test_bata_de_bano_is_type_not_size(self):
        rows = self.packet(["Bata De Baño Kimono Gris", "Bata Bano Kimono Gris Unitalla", "Batas De Bano Kimono G Gris"])["rows"]
        by_description = {r["description"]: r for r in rows}
        self.assertIsNone(by_description["Bata De Baño Kimono Gris"]["proposed_size"])
        self.assertEqual(by_description["Bata Bano Kimono Gris Unitalla"]["proposed_size"], "Unitalla")
        self.assertEqual(by_description["Batas De Bano Kimono G Gris"]["proposed_size"], "G")
        self.assertTrue(all(r["product_family"] == "APPAREL" for r in rows))

    def test_garment_size_codes_are_not_expanded_or_dimensions(self):
        rows = self.packet([f"Bata Kimono -{s}- Gris" for s in ["Ch/M", "G/Eg", "Ch-M", "Ch", "Eg", "Uni", "Xg"]])["rows"]
        self.assertEqual({r["proposed_size"] for r in rows}, {"CH/M", "G/EG", "CH-M", "CH", "EG", "UNI", "XG"})
        self.assertTrue(all("APPAREL_SIZE_EQUIVALENCE_UNVALIDATED" in r["ambiguity_reasons"] for r in rows))
        self.assertIsNone(homologate_size("Baño", RULES, apparel=True))

    def test_robe_category_controls_literal_extraction_without_generic_name(self):
        catalog = fixture(["Kimono G Gris"])
        catalog["categories"][0]["name"] = "Bata Caballero"
        row = analyze(catalog, RULES)["rows"][0]
        self.assertEqual(row["proposed_size"], "G")
        self.assertEqual(row["proposed_model"], "Kimono")
        self.assertEqual(row["product_type_state"], "KNOWN")
        self.assertEqual(row["product_type_evidence"]["literal"], "Bata")
        self.assertEqual(row["product_type_evidence"]["source"], "CATEGORY_NAME")
        self.assertEqual(row["classification_status"], "PENDING_CLASSIFICATION")
        self.assertEqual(self.packet(["Batas Kimono Gris Unitalla"])["inventories"]["product_types"][0]["original_labels"], ["BATA"])

    def test_size_from_unambiguous_alias_has_traceable_origin(self):
        catalog = fixture(["Toalla Fendi Gris"])
        catalog["profile_versions"][0]["mapping_json"]["products"][0]["description_aliases"].append("Toalla MB Fendi Gris")
        row = analyze(catalog, RULES)["rows"][0]
        self.assertEqual(row["proposed_size"], "MEDIO BAÑO")
        self.assertEqual(row["size_source_description"], "Toalla MB Fendi Gris")

    def test_short_garment_tokens_do_not_remove_design_words(self):
        entry = prepare("Toalla MB GRANADA OXFORD", RULES)
        self.assertEqual(entry["cleaned"], "GRANADA OXFORD")
        self.assertEqual(len(entry["presentations"]), 1)

    def test_product_type_is_independent_and_plural_robes_are_homologated(self):
        row = self.packet(["Batas Kimono Gris Unitalla"])["rows"][0]
        self.assertEqual(row["original_product_type"], "Batas")
        self.assertEqual(row["proposed_product_type"], "BATA")
        self.assertEqual(row["proposed_size"], "Unitalla")
        self.assertEqual(row["proposed_model"], "Kimono")
        self.assertEqual(row["product_type_state"], "KNOWN")
        self.assertEqual(row["product_type_evidence"]["literal"], "Batas")
        self.assertEqual(row["product_type_evidence"]["source"], "PRODUCT_DESCRIPTION")
        self.assertEqual(row["classification_status"], "PENDING_CLASSIFICATION")

    def test_type_without_description_uses_category_or_stays_unknown(self):
        self.assertEqual(product_type_evidence("Facial Nuevo", "Toalla", RULES)["source"], "CATEGORY_NAME")
        self.assertIsNone(product_type_evidence("Producto Nuevo", None, RULES)["normalized"])
        self.assertIsNone(product_type_evidence("Straße", None, RULES)["normalized"])

    def test_multiple_generic_types_are_not_forced(self):
        row = self.packet(["Set Bata Toalla Nuevo Gris"])["rows"][0]
        self.assertIsNone(row["proposed_product_type"])
        self.assertIn("PRODUCT_TYPE_AMBIGUOUS", row["ambiguity_reasons"])
        self.assertEqual(row["product_type_state"], "UNKNOWN")
        self.assertTrue(row["product_type_evidence"]["ambiguous"])

    def test_load_package_is_complete_pending_only_and_keeps_unknowns(self):
        catalog = fixture(["TOALLA MB FENDI AZUL", "Producto sin evidencia"])
        catalog['actor'].update(id='00000000-0000-4000-8000-000000000001', global_role='ADMIN')
        catalog['integrity'].update(product_hash='a'*64, import_batch_hash='b'*64, import_profile_hash='c'*64)
        packet, payload = prepare_load(catalog, RULES)
        rows = payload['classifications']
        self.assertEqual({r['product_id'] for r in rows}, {p['id'] for p in catalog['products']})
        self.assertTrue(all(r['classification_status'] == 'PENDING_CLASSIFICATION' and
                            r['approved_by'] is None and r['approved_at'] is None for r in rows))
        self.assertEqual({r['product_id']:r['product_snapshot'] for r in rows}, {p['id']:p for p in catalog['products']})
        self.assertEqual(next(r for r in packet['rows'] if r['product_id']=='0')['upc'], ['000'])
        unknown = next(r for r in rows if r['product_id'] == '1')
        self.assertEqual(unknown['model_state'], 'UNKNOWN')
        self.assertIsNone(unknown['model_id'])
        sql = render_sql(payload)
        self.assertIn('independent mapping authorization missing', sql)
        self.assertNotIn('UPDATE public.', sql)
        self.assertNotIn('DELETE FROM public.', sql)
        self.assertEqual(payload['mapping_packet_hash'], prepare_load(catalog, RULES)[1]['mapping_packet_hash'])

    def test_load_dictionaries_preserve_distinct_accented_labels(self):
        catalog = fixture(["Toalla MB CAFE AZUL", "Toalla MB CAFÉ AZUL"])
        catalog['actor'].update(id='00000000-0000-4000-8000-000000000001', global_role='ADMIN')
        _, payload = prepare_load(catalog, RULES)
        self.assertEqual({r['name'] for r in payload['dictionaries']['commercial_models']}, {'CAFE', 'CAFÉ'})
        self.assertEqual(len({r['model_id'] for r in payload['classifications']}), 2)


if __name__ == "__main__":
    unittest.main()
