from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "skills" / "pdb-evidence-harvester" / "scripts" / "query_orthogonal_sources.py"
SPEC = importlib.util.spec_from_file_location("query_orthogonal_sources", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot import {SCRIPT}")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class OrthogonalQueryTests(unittest.TestCase):
    def test_string_mapping_then_partner_query(self) -> None:
        responses = [
            {
                "status": "ok",
                "http_status": 200,
                "payload": [{"stringId": "9606.ENSP1", "queryItem": "TP53"}],
            },
            {
                "status": "ok",
                "http_status": 200,
                "payload": [{"preferredName_A": "TP53", "preferredName_B": "MDM2", "score": 0.99}],
            },
        ]
        with mock.patch.object(module, "fetch_json", side_effect=responses):
            result = module.query_string("TP53", "9606", 5, 10)
        self.assertEqual(result["result_count"], 1)
        self.assertEqual(len(result["partner_queries"]), 1)
        self.assertEqual(result["partner_queries"][0]["partners"][0]["preferred_name_b"], "MDM2")

    def test_biogrid_without_key_is_blocked(self) -> None:
        with mock.patch.dict(module.os.environ, {}, clear=True):
            result = module.query_biogrid("TP53", "9606", 5, 10)
        self.assertEqual(result["status"], "blocked_missing_api_key")


if __name__ == "__main__":
    unittest.main()
