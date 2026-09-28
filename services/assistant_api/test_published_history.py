"""PH17/33/34/35 source boundaries; actual SQL and remote facts tested separately."""
import hashlib
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from services.assistant_api.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "202609250001_contract_core.sql": "ebdeb7a79c9cc3e38cb3d9aa536a5ac756e913a90ba17cd17cbf63e1f02cc8c9",
    "202609250002_contract_forecast.sql": "59b2f28619aae95161a8316dfadfc2de63117358f23b481731ce3286cce0cf10",
    "202609250003_contract_governance.sql": "d87ef1dd0d9c4b53229b6b2234daff0e29aabc375631798906210170adfc89f0",
    "202609250004_contract_integrity.sql": "47fec71b007f006cec8986cb4ec9272c7a0bd51a57837b55b80f44dee5d36362",
    "202609250005_auth_rls.sql": "011678f373fd7a2c1071b5db44bf93ed207c465ca51af69059001563888620cf",
    "202609250006_private_storage.sql": "d73c0146c4da28852fc83abb3376bdbc69688098454fe529407e4ab2add780ae",
    "202609250007_approval_probe_guard.sql": "e0558245551efc296135bb0b35a017e17d2eefddb5533f5684f473c8bb83468f",
}


class PublishedHistoryBoundaries(unittest.TestCase):
    def test_PH17_original_migrations_content_frozen(self):
        # Pins from approved base bb5d9f6; normalize checkout-only CRLF.
        for name, expected in PINS.items():
            data = (ROOT / "supabase/migrations" / name).read_text(encoding="utf-8").encode()
            self.assertEqual(hashlib.sha256(data).hexdigest(), expected, name)

    def test_PH35_schema_only_no_historical_writes(self):
        sql = (ROOT / "supabase/migrations/202609280001_portal_published_history.sql").read_text()
        sql = re.sub(r"--[^\n]*", "", sql).lower()
        self.assertNotRegex(sql, r"\b(insert|update|delete|truncate|drop|alter|create\s+table)\b")
        self.assertNotRegex(sql, r"available_at\s+is\s+not\s+null")
        self.assertNotRegex(sql, r"(?:create|replace).*\bpublic\.monthly_observations_current\b")
        self.assertIn("security_invoker=true", sql)
        self.assertIn("b.chain_id=o.chain_id", sql)

    def test_PH34_python_runtime_stays_sqlite_models_disabled(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = Settings.from_env()
        self.assertEqual((settings.persistence_provider, settings.data_provider), ("sqlite", "normalized"))
        self.assertFalse(any((settings.supabase_enabled, settings.openai_enabled,
                              settings.deep_research_enabled, settings.voice_enabled)))
        self.assertNotIn("audit_published_history", (ROOT / "services/assistant_api/api.py").read_text())


if __name__ == "__main__":
    unittest.main()
