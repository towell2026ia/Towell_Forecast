"""Scoped RS reads over synthetic responses, without an engine or remote service."""
import unittest

from .auth import Principal, READ
from .rs_assistant_data import ARCHIVE_VERSION_ID, load_rs_scope
from .supabase_access import PreviewError


CHAIN = "00000000-0000-0000-0000-000000000001"
FOREIGN = "00000000-0000-0000-0000-000000000002"
BLUE = "00000000-0000-0000-0000-000000000011"
OXFORD = "00000000-0000-0000-0000-000000000012"
OID = "00000000-0000-0000-0000-000000000021"
CAT = "00000000-0000-0000-0000-000000000031"


class Client:
    def __init__(self):
        self.calls = []
        self.version = ARCHIVE_VERSION_ID
        self.format = "BD"
        self.second_description = "TOALLA MB FENDI OXFORD"
        self.observations = [
            self.observation(BLUE, "SALES", "10.1234567890123456"),
            self.observation(BLUE, "ORDER", "0"),
            self.observation(BLUE, "DELIVERY", "4"),
            self.observation(OXFORD, "ORDER", "9"),
            self.observation(OXFORD, "DELIVERY", "2", source=None),
            self.observation(OXFORD, "SALES", "100", source=None),
            self.observation(BLUE, "FCST_TOWELL_HISTORY", "11"),
            self.observation(BLUE, "FCST_CLIENTE_HISTORY", "12"),
        ]
        self.inventory = [{"chain_id": CHAIN, "product_id": BLUE, "period": "2026-10-01", "format": "BD",
                           "purchase_type": "RS", "quantity_text": "5.0000000000000001", "source_reference": "BAASE!A7:Z7"}]

    @staticmethod
    def observation(product, metric, amount, *, source="BAASE!A7:Z7"):
        return {"chain_id": CHAIN, "product_id": product, "id": OID, "period": "2026-10-01", "metric_code": metric,
                "availability_source": "UNKNOWN", "available_at": None, "value_text": amount, "version_no": 1,
                "source_batch_id": OID, "source_reference": source, "source_value_literal": amount if source else None}

    def operational_context(self):
        self.calls.append(("pin", {}))
        return {"version_id": self.version, "manifest_hash": "a" * 64, "generation": 2}

    def rows(self, table, params):
        self.calls.append((table, params))
        products = [
            {"id": BLUE, "chain_id": CHAIN, "category_id": CAT, "product_code": "blue", "variant_code": None,
             "description": "TOALLA MB FENDI AZUL"},
            {"id": OXFORD, "chain_id": CHAIN, "category_id": CAT, "product_code": "oxford", "variant_code": None,
             "description": self.second_description},
        ]
        profiles = [{"id": OID, "chain_id": CHAIN, "version": 1, "mapping_json": {
            "scope": {"level": "COMMERCIAL_UNIT", "code": "WALMART::" + self.format, "actual_bearing": True},
            "products": [{**row, "identifier_mappings": [[str(i), "000" + str(i)]]} for i, row in enumerate(products)],
        }}]
        return {"chains": [{"id": CHAIN, "name": "Walmart::BD"}], "products": products, "import_profile_versions": profiles,
                "categories": [{"id": CAT, "chain_id": CHAIN, "name": "Toalla"}]}.get(table, [])

    def observation_rows(self, params, *, chain_id):
        self.calls.append(("observations", {**params, "chain_id": chain_id}))
        return self.observations

    def inventory_rows(self, params):
        self.calls.append(("inventory", params))
        return self.inventory


class RsAssistantDataTests(unittest.TestCase):
    def setUp(self):
        self.client = Client()
        self.principal = Principal("actor", "reader", frozenset({READ}), "session", frozenset({CHAIN}))

    def load(self, **overrides):
        return load_rs_scope(self.client, self.principal,
                             **{**dict(chain_id=CHAIN, purchase_type="RS", format="bd", model="fendi", period="2026-10"),
                                **overrides})

    def test_exact_separate_sources_coverage_and_retrospective_records(self):
        result = self.load()
        self.assertEqual(result["scope"]["version_id"], ARCHIVE_VERSION_ID)
        self.assertEqual(result["scope"]["format"], "BD")
        self.assertEqual(result["scope"]["presentation"], "MEDIO BAÑO")
        self.assertEqual(result["scope"]["public_product_type"], "TOALLA")
        self.assertEqual(result["eligible_variants"], 2)
        self.assertEqual(result["quality"]["excluded_without_rs_lineage"], {"DELIVERY": 1, "SALES": 1})
        record, = result["records"]
        self.assertEqual((record["objective"], record["source_id"], record["value"], record["evidence_mode"]),
                         ("Venta", OID, "10.1234567890123456", "RETROSPECTIVE_TRAINING"))
        self.assertIsNone(record["available_at"])
        month = result["metrics"]["by_month"][-1]
        self.assertEqual((month["SALES"]["pieces"], month["ORDER"]["pieces"], month["DELIVERY"]["pieces"]),
                         ("10.1234567890123456", "9", "4"))
        self.assertEqual((month["FCST_TOWELL_HISTORY"]["pieces"], month["FCST_CLIENTE_HISTORY"]["pieces"]), ("11", "12"))
        self.assertEqual(month["fill_rate"], {"ratio": None, "paired_products": 1, "products_expected": 2})
        self.assertIsNone(result["metrics"]["by_month"][0]["SALES"]["pieces"])
        self.assertEqual(result["metrics"]["inventory"]["pieces"], "5.0000000000000001")
        self.assertEqual(result["metrics"]["inventory"]["level"], "CHAIN_FORMAT_PRODUCT_MONTH")
        queries = dict(self.client.calls)
        self.assertEqual(queries["products"]["chain_id"], "eq." + CHAIN)
        self.assertEqual(queries["observations"]["p_product_ids"], "{" + BLUE + "," + OXFORD + "}")
        self.assertEqual(queries["inventory"]["purchase_type"], "eq.RS")

    def test_active_A_no_catalog_or_data_calls_and_denied_chain_no_pin(self):
        self.client.version = FOREIGN
        with self.assertRaisesRegex(PreviewError, "RS_CANDIDATE_NOT_ACTIVE"):
            self.load()
        self.assertEqual(self.client.calls, [("pin", {})])
        self.client.calls.clear()
        with self.assertRaisesRegex(PreviewError, "SCOPE_FORBIDDEN"):
            self.load(chain_id=FOREIGN)
        self.assertEqual(self.client.calls, [])

    def test_invalid_scope_and_unverified_format(self):
        for filters in (dict(purchase_type="CE"), dict(period="2026-11"), dict(model="")):
            with self.assertRaisesRegex(PreviewError, "RS_SCOPE_INVALID"):
                self.load(**filters)
        self.assertEqual(self.client.calls, [])
        with self.assertRaisesRegex(PreviewError, "RS_FORMAT_NOT_VERIFIED"):
            self.load(format="SC")
        self.assertNotIn("observations", dict(self.client.calls))
        with self.assertRaisesRegex(PreviewError, "RS_MODEL_NOT_VERIFIED"):
            self.load(model="Fendi A")

    def test_multiple_model_presentations_require_explicit_resolution(self):
        self.client.second_description = "TOALLA FACIAL FENDI OXFORD"
        with self.assertRaisesRegex(PreviewError, "RS_MODEL_PRESENTATION_AMBIGUOUS"):
            self.load()
        self.assertNotIn("observations", dict(self.client.calls))

    def test_bad_provenance_duplicate_and_cross_scope_fail_closed(self):
        original = self.client.observations[0]
        for bad in ({**original, "source_value_literal": "11"}, {**original, "chain_id": FOREIGN}, original):
            self.client.observations.append(bad)
            with self.assertRaisesRegex(PreviewError, "DATA_READ_FAILED"):
                self.load()
            self.client.observations.pop()
        self.client.observations[0] = {**original, "available_at": "2026-01-01"}
        with self.assertRaisesRegex(PreviewError, "DATA_READ_FAILED"):
            self.load()

    def test_only_archived_RS_lineage_can_train(self):
        self.client.observations = [self.client.observation(BLUE, "SALES", "100", source=None)]
        self.client.inventory = []
        result = self.load()
        self.assertEqual(result["records"], [])
        self.assertIsNone(result["metrics"]["by_month"][-1]["SALES"]["pieces"])
        self.assertEqual(result["quality"]["excluded_without_rs_lineage"], {"SALES": 1})
        self.assertIsNone(result["metrics"]["inventory"]["pieces"])


if __name__ == "__main__":
    unittest.main()
