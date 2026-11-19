import json
import unittest
from uuid import UUID

from prepare import literal, record, stage_sql


class SealedReleaseTransportTest(unittest.TestCase):
    chain = str(UUID(int=1))
    product = str(UUID(int=2))
    run_id = str(UUID(int=3))

    def test_numeric_literals_and_nulls_survive_transport(self):
        source = ('{"chain_id":"%s","product_id":"%s","id":"%s","period":"2026-01-01",'
                  '"metric_code":"ORDER","value":123.00000000000000001,"available_at":null}'
                  % (self.chain, self.product, self.run_id))
        periods = set()
        result = record("observations", source, periods)
        self.assertIn(source, result)
        self.assertEqual(periods, {(self.chain, "2026-01-01")})

    def test_stocks_are_scoped_by_period_and_commercial_identity(self):
        row = dict(chain_id=self.chain,product_id=self.product,period="2025-11-01",format="B",upc="0002")
        versions = [dict(row,**change) for change in ({},{"period":"2025-12-01"},{"format":"A"},{"upc":"2"})]
        self.assertEqual(len({json.loads(record("inventory",json.dumps(v),set()))["record_key"] for v in versions}),4)

    def test_untrusted_text_cannot_close_a_sql_literal(self):
        raw = "fin $body$ ' $g1234$ $$; \n"
        quoted = literal(raw)
        delimiter = quoted[:quoted.index("$",1)+1]
        self.assertEqual(quoted,delimiter+raw+delimiter)
        self.assertNotIn(delimiter,raw)

    def test_stage_is_pinned_and_resumable(self):
        chunk = stage_sql(self.run_id, "sealed_products", 2, ['{"example":"x"}'])
        self.assertIn("private.ft_g6_receipt",chunk)
        self.assertIn("for share",chunk)
        self.assertIn("Release is missing or already published",chunk)
        self.assertNotIn("set active=true",chunk)
        self.assertNotIn("monthly_observations",chunk)
        self.assertEqual(chunk,stage_sql(self.run_id, "sealed_products", 2, ['{"example":"x"}']))


if __name__ == "__main__":
    unittest.main()
