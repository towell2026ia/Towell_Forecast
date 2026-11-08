"""Synthetic parser compatibility; database acceptance lives in the guarded lab."""
import unittest
from services.assistant_api.monthly_imports import ImportProfileVersion, parse_monthly


class HistoricalContractTests(unittest.TestCase):
    def test_explicit_five_metric_profile_preserves_independent_sources_and_absence(self):
        profile = ImportProfileVersion('synthetic', 'chain', 1,
            {k: k for k in ('product_code', 'description', 'period', 'sales', 'order', 'delivery',
                           'fcst_towell_history', 'fcst_cliente_history')}, include_historical_forecasts=True)
        content = b'product_code,description,period,sales,order,delivery,fcst_towell_history,fcst_cliente_history\n001,Synthetic,2026-10,,0,,1.1234567890123456,2.125\n'
        rows, rejected, count = parse_monthly(content, 'synthetic.csv', profile)
        self.assertEqual(count, 1)
        self.assertEqual(rejected, [])
        self.assertEqual(rows[0].metrics, {'ORDER': '0', 'FCST_TOWELL_HISTORY': '1.1234567890123456', 'FCST_CLIENTE_HISTORY': '2.125'})
        legacy = ImportProfileVersion('synthetic', 'chain', 1, profile.columns)
        self.assertEqual(parse_monthly(content, 'synthetic.csv', legacy)[0][0].metrics, {'ORDER': '0'})

    def test_forecast_only_requires_explicit_opt_in(self):
        columns = {k: k for k in ('product_code', 'description', 'period', 'fcst_towell_history')}
        with self.assertRaisesRegex(ValueError, 'profile_requires_metric'):
            ImportProfileVersion('synthetic', 'chain', 1, columns).validate()
        ImportProfileVersion('synthetic', 'chain', 1, columns, include_historical_forecasts=True).validate()

    def test_normalization_does_not_round_at_default_decimal_precision(self):
        from services.assistant_api.historical_corpus import number
        maximum='999999999999999.9999999999999999'
        self.assertEqual(number(maximum),('VALUE',maximum))
        self.assertEqual(number('1.23000'),('VALUE','1.23'))
        with self.assertRaisesRegex(ValueError,'invalid_metric_contract'):
            ImportProfileVersion('p','c',1,{},include_historical_forecasts='false').validate()
