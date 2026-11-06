import unittest
from .reconciliation import reconcile


class ReconciliationTests(unittest.TestCase):
    def test_exact_upc_never_autoapproves_or_matches_item(self):
        p = {"id": "A|001", "account": "A", "chain": "Company", "format": "BD", "barcode": "001", "description": "M BLUE", "model": "M", "size": "MB", "color": "BLUE", "commercial_code": "CODE"}
        db = {"id": "P", "chain_id": "C", "product_code": "001", "description": "M BLUE", "identifier_mappings": [["99", "001"]]}
        snapshot = {"profiles": [{"id": "PROFILE", "mapping_json": {"scope": {"code": "Walmart::BD", "scope_type": "COMMERCIAL_UNIT"}, "products": [db]}}]}
        live = [["P", "C", "001", "ACTIVE"]]
        r = reconcile([p], snapshot, live)[0]
        self.assertEqual(r["candidate_status"], "ONE_UPC_CANDIDATE")
        self.assertEqual(r["format_hint_count"], 1)
        self.assertEqual(r["integration_status"], "BLOCKED")
        db["identifier_mappings"] = [["001", "99"]]
        self.assertEqual(reconcile([p], snapshot, live)[0]["candidate_status"], "NO_EXACT_UPC")
        with self.assertRaises(ValueError): reconcile([p, p], snapshot, live)

    def test_two_scopes_and_current_catalog_gate(self):
        p = {"id": "A|001", "account": "A", "chain": "Company", "format": "X", "barcode": "001", "description": "M", "model": "M", "size": "", "color": "", "commercial_code": None}
        profiles = [{"id": c, "mapping_json": {"scope": {"code": c, "scope_type": "COMMERCIAL_UNIT"}, "products": [{"id": c, "chain_id": c, "product_code": "001", "description": "M", "identifier_mappings": [["ITEM", "001"]]}]}} for c in ("C", "D")]
        live = [[c, c, "001", "ACTIVE"] for c in ("C", "D")]
        self.assertEqual(reconcile([p], {"profiles": profiles}, live)[0]["candidate_count"], 2)
        self.assertEqual(reconcile([p], {"profiles": profiles}, live[:1])[0]["candidate_count"], 1)
