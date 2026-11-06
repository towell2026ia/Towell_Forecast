"""Synthetic identity tests; no production catalog or inventory is changed."""

import copy
import unittest

from .automatic_reconciliation import reconcile


def fixture():
    row = {"id": "A|123", "account": "A", "chain": "CHAIN", "format": "BD", "barcode": "123",
           "description": "FENDI AZUL", "model": "FENDI", "size": "MB", "color": "AZUL",
           "commercial_code": "MB71581000", "candidates": [{"product_id": "P1", "chain_id": "C1",
           "scope": "CHAIN::BD", "format_hint": True}]}
    review = {"identity": row["id"], "account": row["account"], "upc": row["barcode"],
              "chain": row["chain"], "format": row["format"], "model": row["model"],
              "presentation": row["size"], "color": row["color"],
              "commercial_code": row["commercial_code"]}
    current = {"products": [["P1", "C1", "123", "ACTIVE"]]}
    evidence = {"account_scopes": [{"account": "A", "chain": "CHAIN", "chain_id": "C1", "scope": "CHAIN::BD",
                                    "format": "BD", "status": "ACCREDITED", "evidence": "signed-account-map"}],
                "product_attributes": [{"product_id": "P1", "chain_id": "C1", "model": "FENDI",
                                        "presentation": "MB", "color": "AZUL", "commercial_code": "MB71581000",
                                        "status": "ACCREDITED", "evidence": "signed-product-catalog"}]}
    return row, review, current, evidence


class AutomaticReconciliationTests(unittest.TestCase):
    def test_exact_match_needs_structured_evidence(self):
        row, review, current, evidence = fixture()
        result = reconcile([row], [review], current, evidence)[0]
        self.assertEqual(result["status"], "COINCIDENCIA_VERIFICADA")
        self.assertEqual(result["verified_product_id"], "P1")
        self.assertEqual(len(result["evidence"]), 2)
        self.assertEqual(reconcile([row], [review], current)[0]["status"], "REVISION_REQUERIDA")

    def test_each_attribute_and_scope_conflict_is_named(self):
        row, review, current, evidence = fixture()
        evidence["product_attributes"][0]["color"] = "OXFORD"
        evidence["product_attributes"][0]["presentation"] = "BAÑO"
        evidence["account_scopes"][0]["format"] = "SC"
        result = reconcile([row], [review], current, evidence)[0]
        self.assertEqual(result["status"], "REVISION_REQUERIDA")
        self.assertIn("COLOR_DIFIERE", result["differences"])
        self.assertIn("PRESENTATION_DIFIERE", result["differences"])
        self.assertIn("CUENTA_CADENA_FORMATO_NO_ACREDITADOS", result["differences"])

    def test_name_alone_never_links_missing_upc(self):
        row, review, current, evidence = fixture()
        row["candidates"] = []
        result = reconcile([row], [review], current, evidence)[0]
        self.assertEqual(result["status"], "SIN_CORRESPONDENCIA")
        self.assertIsNone(result["verified_product_id"])

    def test_format_conflict_requires_explicit_scope_equivalence(self):
        row, review, current, evidence = fixture()
        row["candidates"][0]["format_hint"] = False
        result = reconcile([row], [review], current)[0]
        self.assertIn("FORMATO_SCOPE_TEXTUAL_DIFIERE", result["differences"])
        self.assertEqual(reconcile([row], [review], current, evidence)[0]["status"], "REVISION_REQUERIDA")
        evidence["account_scopes"][0]["scope_equivalence"] = True
        self.assertEqual(reconcile([row], [review], current, evidence)[0]["status"], "COINCIDENCIA_VERIFICADA")

    def test_multiple_candidates_and_repeated_target_are_ambiguous(self):
        row, review, current, evidence = fixture()
        other = copy.deepcopy(row)
        other["id"] = "B|123"
        other["account"] = "B"
        other_review = copy.deepcopy(review)
        other_review.update(identity="B|123", account="B")
        evidence["account_scopes"].append({**evidence["account_scopes"][0], "account": "B"})
        results = reconcile([row, other], [review, other_review], current, evidence)
        self.assertEqual([r["status"] for r in results], ["AMBIGUO", "AMBIGUO"])
        row["candidates"].append({"product_id": "P2", "chain_id": "C1", "scope": "CHAIN::BD", "format_hint": True})
        self.assertEqual(reconcile([row], [review], current, evidence)[0]["status"], "AMBIGUO")

    def test_source_change_and_duplicate_id_are_rejected(self):
        row, review, current, evidence = fixture()
        review["upc"] = "OTHER"
        with self.assertRaisesRegex(ValueError, "REVIEW_SOURCE_CHANGED"):
            reconcile([row], [review], current, evidence)
        review["upc"] = "123"
        with self.assertRaisesRegex(ValueError, "DUPLICATE_PRIVATE_IDENTITY"):
            reconcile([row, row], [review, review], current, evidence)


if __name__ == "__main__":
    unittest.main()
