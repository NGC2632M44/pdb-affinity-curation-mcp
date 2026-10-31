from __future__ import annotations

import csv
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
SPEC = importlib.util.spec_from_file_location("pdb_curation_server", SERVER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot import {SERVER_PATH}")
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class ProtocolTests(unittest.TestCase):
    def test_initialize(self) -> None:
        response = server.process_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        )
        self.assertIsNotNone(response)
        self.assertEqual(response["id"], 1)
        result = response["result"]
        self.assertEqual(result["serverInfo"]["name"], "pdb-curation")
        self.assertIn("tools", result["capabilities"])
        self.assertIn("resources", result["capabilities"])

    def test_initialize_negotiates_supported_version(self) -> None:
        response = server.process_request(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "initialize",
                "params": {"protocolVersion": "2099-01-01"},
            }
        )
        self.assertEqual(
            response["result"]["protocolVersion"],
            server.PROTOCOL_VERSION,
        )

    def test_tools_are_unique_and_object_schemas(self) -> None:
        response = server.process_request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        tools = response["result"]["tools"]
        names = [tool["name"] for tool in tools]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("record_missing_disposition", names)
        self.assertIn("audit_missing_traceability", names)
        tools_by_name = {tool["name"]: tool for tool in tools}
        self.assertIn("oneOf", tools_by_name["fetch_pdb_evidence"]["inputSchema"])
        self.assertIn("oneOf", tools_by_name["resolve_identity"]["inputSchema"])
        self.assertIn(
            "anyOf", tools_by_name["prepare_database_queries"]["inputSchema"]
        )
        reason_schema = tools_by_name["record_missing_disposition"][
            "inputSchema"
        ]["properties"]["reason_code"]
        self.assertIn("no_quantitative_value", reason_schema["enum"])
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertIsInstance(tool["inputSchema"]["properties"], dict)
            self.assertIsInstance(tool["inputSchema"]["required"], list)

    def test_resources_exist(self) -> None:
        response = server.process_request(
            {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}}
        )
        resources = response["result"]["resources"]
        self.assertGreaterEqual(len(resources), 6)
        for resource in resources:
            read = server.resources_read({"uri": resource["uri"]})
            self.assertTrue(read["contents"][0]["text"])

    def test_subprocess_stdio(self) -> None:
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "classify_missing_reason",
                    "arguments": {"code": "no_quantitative_value"},
                },
            },
        ]
        process = subprocess.run(
            [sys.executable, str(SERVER_PATH)],
            input="\n".join(json.dumps(item) for item in requests) + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        responses = [
            json.loads(line)
            for line in process.stdout.splitlines()
            if line.strip()
        ]
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["id"], 1)
        self.assertEqual(responses[1]["id"], 2)
        self.assertFalse(responses[1]["result"]["isError"])

    def test_subprocess_content_length_framing(self) -> None:
        request = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "ping",
            "params": {},
        }
        body = json.dumps(request).encode("utf-8")
        framed = (
            f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        )
        process = subprocess.run(
            [sys.executable, str(SERVER_PATH)],
            input=framed,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        header, response_body = process.stdout.split(b"\r\n\r\n", 1)
        self.assertTrue(header.startswith(b"Content-Length:"))
        self.assertEqual(
            int(header.split(b":", 1)[1].strip()), len(response_body)
        )
        response = json.loads(response_body.decode("utf-8"))
        self.assertEqual(response["id"], 7)
        self.assertEqual(response["result"], {})

    def test_cancellation_stops_wrapped_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "pdb-affinity-analysis"
            scripts = skill / "scripts"
            scripts.mkdir(parents=True)
            slow_script = scripts / "extract_document_text.py"
            slow_script.write_text(
                "import time\ntime.sleep(10)\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PDB_SKILL_ROOT"] = str(root)
            env["PYTHONUTF8"] = "1"
            process = subprocess.Popen(
                [sys.executable, str(SERVER_PATH)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
            )
            try:
                assert process.stdin is not None
                assert process.stdout is not None
                initialize = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05"},
                }
                process.stdin.write(json.dumps(initialize) + "\n")
                process.stdin.flush()
                self.assertEqual(json.loads(process.stdout.readline())["id"], 1)
                call = {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "extract_document_text",
                        "arguments": {
                            "documents": [str(root / "fake.pdf")],
                            "output_dir": str(root / "out"),
                        },
                    },
                }
                process.stdin.write(json.dumps(call) + "\n")
                process.stdin.flush()
                time.sleep(0.5)
                cancel = {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": 2},
                }
                process.stdin.write(json.dumps(cancel) + "\n")
                process.stdin.flush()
                started = time.monotonic()
                response = json.loads(process.stdout.readline())
                self.assertLess(time.monotonic() - started, 3)
                self.assertEqual(response["id"], 2)
                self.assertTrue(response["result"]["isError"])
                payload = response["result"].get("structuredContent", {})
                self.assertIn(
                    payload.get("error"),
                    {"cancelled", "cancelled_before_start"},
                    response,
                )
            finally:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
                if process.poll() is None:
                    process.wait(timeout=5)
                if process.stdout and not process.stdout.closed:
                    process.stdout.close()
                if process.stderr and not process.stderr.closed:
                    process.stderr.close()


class MissingReasonTests(unittest.TestCase):
    def test_classify_missing_reason(self) -> None:
        result = server.classify_missing_reason(
            {"code": "modeled_ligand_is_additive"}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["default_status"], "rejected_non_inhibitor"
        )
        qualitative = server.classify_missing_reason(
            {"code": "qualitative_inhibition_only"}
        )
        self.assertEqual(
            qualitative["default_status"],
            "accepted_non_numeric_context",
        )

    def test_unknown_missing_reason_fails(self) -> None:
        with self.assertRaises(server.ToolFailure):
            server.classify_missing_reason({"code": "not_a_real_code"})

    def test_audit_workbook_reasons_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["PDB_ID", "Ligand", "Value", "Reason"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "PDB_ID": "1ABC",
                        "Ligand": "",
                        "Value": "",
                        "Reason": "无配体：apo；无数值：原文未报告",
                    }
                )
                writer.writerow(
                    {
                        "PDB_ID": "2ABC",
                        "Ligand": "LIG",
                        "Value": "10",
                        "Reason": "",
                    }
                )
            result = server.audit_missing_reasons(
                {
                    "path": str(path),
                    "pdb_column": "PDB_ID",
                    "ligand_column": "Ligand",
                    "value_column": "Value",
                    "reason_column": "Reason",
                }
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["stats"]["missing_ligand"], 1)
            self.assertEqual(result["stats"]["missing_value"], 1)

    def test_structured_missing_traceability(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "source_register.csv"
            evidence.write_text("PDB_ID,Path\n7ABC,paper.pdf\n", encoding="utf-8")
            ledger = root / "missing_dispositions.csv"
            result = server.record_missing_disposition(
                {
                    "ledger_path": str(ledger),
                    "pdb_id": "7ABC",
                    "ligand_id": "",
                    "field_name": "ligand",
                    "reason_code": "no_modeled_ligand_apo",
                    "search_scope": "RCSB; paper; SI; patents",
                    "sources_checked": "RCSB; DOI paper; SI",
                    "source_locator": "Paper Results p.3; RCSB HET list",
                    "evidence_path": str(evidence),
                    "reviewer": "test",
                }
            )
            self.assertTrue(result["ok"])
            audit = server.audit_missing_traceability(
                {
                    "ledger_path": str(ledger),
                    "required_pdb_ids": ["7ABC"],
                }
            )
            self.assertTrue(audit["ok"], audit)
            self.assertEqual(audit["pdb_count"], 1)
            self.assertEqual(audit["stats"].get("evidence_hash_mismatch", 0), 0)

    def test_blocked_disposition_requires_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "missing_dispositions.csv"
            with self.assertRaises(server.ToolFailure):
                server.record_missing_disposition(
                    {
                        "ledger_path": str(ledger),
                        "pdb_id": "8ABC",
                        "field_name": "affinity_value",
                        "reason_code": "access_blocked",
                        "search_scope": "Publisher; Europe PMC",
                        "sources_checked": "Publisher; Europe PMC",
                    }
                )

    def test_blocker_reference_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blocker_ledger = root / "blockers.csv"
            missing_ledger = root / "missing_dispositions.csv"
            server.record_missing_disposition(
                {
                    "ledger_path": str(missing_ledger),
                    "pdb_id": "8ABC",
                    "field_name": "affinity_value",
                    "reason_code": "access_blocked",
                    "search_scope": "Publisher; Europe PMC",
                    "sources_checked": "Publisher; Europe PMC",
                    "blocker_id": "FAKE-NOT-IN-LEDGER",
                }
            )
            blocker = server.record_blocker(
                {
                    "ledger_path": str(blocker_ledger),
                    "pdb_id": "8ABC",
                    "blocker_type": "publisher_paywall",
                    "original_detail": "403",
                }
            )
            self.assertTrue(blocker["row"]["Blocker_ID"].startswith("BL-"))
            audit = server.audit_missing_traceability(
                {
                    "ledger_path": str(missing_ledger),
                    "blocker_ledger": str(blocker_ledger),
                }
            )
            self.assertFalse(audit["ok"])
            self.assertEqual(audit["stats"]["blocker_id_not_found"], 1)

    def test_reason_status_and_field_are_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "missing_dispositions.csv"
            with self.assertRaises(server.ToolFailure):
                server.record_missing_disposition(
                    {
                        "ledger_path": str(ledger),
                        "pdb_id": "9XYZ",
                        "field_name": "affinity_value",
                        "disposition": "totally_arbitrary",
                        "reason_code": "no_quantitative_value",
                        "search_scope": "Paper",
                        "sources_checked": "Paper",
                        "source_locator": "Table 1",
                    }
                )
            with self.assertRaises(server.ToolFailure):
                server.record_missing_disposition(
                    {
                        "ledger_path": str(ledger),
                        "pdb_id": "9XYZ",
                        "field_name": "affinity_value",
                        "reason_code": "no_quantitative_value",
                        "status": "accepted_something_else",
                        "search_scope": "Paper",
                        "sources_checked": "Paper",
                        "source_locator": "Table 1",
                    }
                )
            with self.assertRaises(server.ToolFailure):
                server.record_missing_disposition(
                    {
                        "ledger_path": str(ledger),
                        "pdb_id": "9XYZ",
                        "field_name": "affinity_value",
                        "reason_code": "no_modeled_ligand_apo",
                        "search_scope": "Paper",
                        "sources_checked": "Paper",
                        "source_locator": "Table 1",
                    }
                )

    def test_valid_blocker_reference_closes_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blocker_ledger = root / "blockers.csv"
            missing_ledger = root / "missing_dispositions.csv"
            blocker = server.record_blocker(
                {
                    "ledger_path": str(blocker_ledger),
                    "pdb_id": "8ABC",
                    "blocker_type": "publisher_paywall",
                    "original_detail": "403",
                }
            )
            server.record_missing_disposition(
                {
                    "ledger_path": str(missing_ledger),
                    "pdb_id": "8ABC",
                    "field_name": "affinity_value",
                    "reason_code": "access_blocked",
                    "search_scope": "Publisher; Europe PMC",
                    "sources_checked": "Publisher; Europe PMC",
                    "blocker_id": blocker["row"]["Blocker_ID"],
                }
            )
            audit = server.audit_missing_traceability(
                {
                    "ledger_path": str(missing_ledger),
                    "blocker_ledger": str(blocker_ledger),
                }
            )
            self.assertTrue(audit["ok"], audit)
            self.assertEqual(audit["blocked_rows"], 1)

    def test_blocker_must_match_disposition_pdb(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blocker_ledger = root / "blockers.csv"
            missing_ledger = root / "missing_dispositions.csv"
            blocker = server.record_blocker(
                {
                    "ledger_path": str(blocker_ledger),
                    "pdb_id": "1ABC",
                    "blocker_type": "publisher_paywall",
                    "original_detail": "403",
                }
            )
            server.record_missing_disposition(
                {
                    "ledger_path": str(missing_ledger),
                    "pdb_id": "2ABC",
                    "field_name": "affinity_value",
                    "reason_code": "access_blocked",
                    "search_scope": "Publisher",
                    "sources_checked": "Publisher",
                    "blocker_id": blocker["row"]["Blocker_ID"],
                }
            )
            audit = server.audit_missing_traceability(
                {
                    "ledger_path": str(missing_ledger),
                    "blocker_ledger": str(blocker_ledger),
                }
            )
            self.assertFalse(audit["ok"])
            self.assertEqual(audit["stats"]["blocker_pdb_mismatch"], 1)

    def test_accepted_disposition_requires_not_just_prose(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "missing_dispositions.csv"
            with self.assertRaises(server.ToolFailure):
                server.record_missing_disposition(
                    {
                        "ledger_path": str(ledger),
                        "pdb_id": "9ABC",
                        "field_name": "affinity_value",
                        "reason_code": "no_quantitative_value",
                        "search_scope": "Paper",
                        "sources_checked": "Paper",
                        "evidence_text": "trust me",
                    }
                )

    def test_database_lead_requires_query_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "missing_dispositions.csv"
            with self.assertRaises(server.ToolFailure):
                server.record_missing_disposition(
                    {
                        "ledger_path": str(ledger),
                        "pdb_id": "9ABC",
                        "field_name": "affinity_value",
                        "reason_code": "database_only_lead",
                        "search_scope": "Reaxys",
                        "sources_checked": "Reaxys",
                        "source_locator": "Reaxys result 1",
                    }
                )

    def test_database_query_reference_closes_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_ledger = root / "database.csv"
            missing_ledger = root / "missing_dispositions.csv"
            query = server.record_database_query(
                {
                    "ledger_path": str(database_ledger),
                    "database": "Reaxys",
                    "pdb_id": "9ABC",
                    "ligand": "LIG",
                    "query": "exact structure",
                    "result_count": 1,
                    "result_locator": "row 7",
                }
            )
            server.record_missing_disposition(
                {
                    "ledger_path": str(missing_ledger),
                    "pdb_id": "9ABC",
                    "ligand_id": "LIG",
                    "field_name": "affinity_value",
                    "reason_code": "database_only_lead",
                    "search_scope": "Reaxys exact structure",
                    "sources_checked": "Reaxys",
                    "source_locator": "Reaxys row 7",
                    "query_id": query["row"]["Query_ID"],
                }
            )
            audit = server.audit_missing_traceability(
                {
                    "ledger_path": str(missing_ledger),
                    "database_ledger": str(database_ledger),
                }
            )
            self.assertTrue(audit["ok"], audit)

    def test_database_query_must_match_disposition_pdb(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_ledger = root / "database.csv"
            missing_ledger = root / "missing_dispositions.csv"
            query = server.record_database_query(
                {
                    "ledger_path": str(database_ledger),
                    "database": "Reaxys",
                    "pdb_id": "1ABC",
                    "query": "exact structure",
                    "result_count": 1,
                }
            )
            server.record_missing_disposition(
                {
                    "ledger_path": str(missing_ledger),
                    "pdb_id": "2ABC",
                    "field_name": "affinity_value",
                    "reason_code": "database_only_lead",
                    "search_scope": "Reaxys",
                    "sources_checked": "Reaxys",
                    "source_locator": "Reaxys row 1",
                    "query_id": query["row"]["Query_ID"],
                }
            )
            audit = server.audit_missing_traceability(
                {
                    "ledger_path": str(missing_ledger),
                    "database_ledger": str(database_ledger),
                }
            )
            self.assertFalse(audit["ok"])
            self.assertEqual(audit["stats"]["query_pdb_mismatch"], 1)

    def test_missing_coverage_requires_each_blank_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            table = root / "table.csv"
            ledger = root / "missing_dispositions.csv"
            with table.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["PDB_ID", "Ligand", "Value"],
                )
                writer.writeheader()
                writer.writerow(
                    {"PDB_ID": "1ABC", "Ligand": "", "Value": ""}
                )
            server.record_missing_disposition(
                {
                    "ledger_path": str(ledger),
                    "pdb_id": "1ABC",
                    "field_name": "ligand",
                    "reason_code": "no_modeled_ligand_apo",
                    "search_scope": "RCSB; paper",
                    "sources_checked": "RCSB; paper",
                    "source_locator": "RCSB HET list; paper Table 1",
                }
            )
            partial = server.audit_missing_coverage(
                {
                    "table_path": str(table),
                    "pdb_column": "PDB_ID",
                    "ligand_column": "Ligand",
                    "value_column": "Value",
                    "disposition_ledger": str(ledger),
                }
            )
            self.assertFalse(partial["ok"])
            self.assertEqual(partial["stats"]["uncovered_missing_fields"], 1)
            server.record_missing_disposition(
                {
                    "ledger_path": str(ledger),
                    "pdb_id": "1ABC",
                    "field_name": "affinity_value",
                    "reason_code": "no_quantitative_value",
                    "search_scope": "paper; SI",
                    "sources_checked": "paper; SI",
                    "source_locator": "paper Results; SI Table S1",
                }
            )
            complete = server.audit_missing_coverage(
                {
                    "table_path": str(table),
                    "pdb_column": "PDB_ID",
                    "ligand_column": "Ligand",
                    "value_column": "Value",
                    "disposition_ledger": str(ledger),
                }
            )
            self.assertTrue(complete["ok"], complete)
            self.assertEqual(complete["stats"]["covered_missing_fields"], 2)

    def test_missing_coverage_distinguishes_contexts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            table = root / "table.csv"
            ledger = root / "missing_dispositions.csv"
            with table.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["PDB_ID", "Ligand", "Value", "Assay"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "PDB_ID": "1ABC",
                        "Ligand": "compound 1",
                        "Value": "",
                        "Assay": "single concentration",
                    }
                )
                writer.writerow(
                    {
                        "PDB_ID": "1ABC",
                        "Ligand": "compound 1",
                        "Value": "",
                        "Assay": "Tm",
                    }
                )
            server.record_missing_disposition(
                {
                    "ledger_path": str(ledger),
                    "pdb_id": "1ABC",
                    "ligand_id": "compound 1",
                    "context": "single concentration",
                    "field_name": "affinity_value",
                    "reason_code": "single_concentration_only",
                    "search_scope": "paper",
                    "sources_checked": "paper",
                    "source_locator": "Figure 1",
                }
            )
            partial = server.audit_missing_coverage(
                {
                    "table_path": str(table),
                    "pdb_column": "PDB_ID",
                    "ligand_column": "Ligand",
                    "value_column": "Value",
                    "context_column": "Assay",
                    "disposition_ledger": str(ledger),
                }
            )
            self.assertFalse(partial["ok"])
            self.assertEqual(partial["stats"]["uncovered_missing_fields"], 1)
            server.record_missing_disposition(
                {
                    "ledger_path": str(ledger),
                    "pdb_id": "1ABC",
                    "ligand_id": "compound 1",
                    "context": "Tm",
                    "field_name": "affinity_value",
                    "reason_code": "non_affinity_measurement_only",
                    "search_scope": "paper",
                    "sources_checked": "paper",
                    "source_locator": "Table 1",
                }
            )
            complete = server.audit_missing_coverage(
                {
                    "table_path": str(table),
                    "pdb_column": "PDB_ID",
                    "ligand_column": "Ligand",
                    "value_column": "Value",
                    "context_column": "Assay",
                    "disposition_ledger": str(ledger),
                }
            )
            self.assertTrue(complete["ok"], complete)

    def test_traceability_detects_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence.txt"
            evidence.write_text("original", encoding="utf-8")
            ledger = root / "missing_dispositions.csv"
            server.record_missing_disposition(
                {
                    "ledger_path": str(ledger),
                    "pdb_id": "9ABC",
                    "field_name": "affinity_value",
                    "reason_code": "no_quantitative_value",
                    "search_scope": "Paper and SI",
                    "sources_checked": "Paper; SI",
                    "source_locator": "Table 1",
                    "evidence_path": str(evidence),
                }
            )
            evidence.write_text("changed", encoding="utf-8")
            audit = server.audit_missing_traceability(
                {"ledger_path": str(ledger)}
            )
            self.assertFalse(audit["ok"])
            self.assertEqual(
                audit["stats"]["evidence_hash_mismatch"], 1
            )


class LedgerToolTests(unittest.TestCase):
    def test_infer_pdb_ids_from_curated_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "curated.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["PDB_ID", "Value"])
                writer.writeheader()
                writer.writerow({"PDB_ID": "4e5g", "Value": "1"})
                writer.writerow({"PDB_ID": "6QW5", "Value": "2"})
                writer.writerow({"PDB_ID": "4E5G", "Value": "3"})
            self.assertEqual(
                server.curated_pdb_ids(path), ["4E5G", "6QW5"]
            )
            pdbs, source = server.comparison_pdb_ids({}, path)
            self.assertEqual(pdbs, ["4E5G", "6QW5"])
            self.assertEqual(source, "inferred_from_curated_csv")
            self.assertEqual(
                server.comparison_pdb_ids(
                    {"use_all_human_rows": True}, path
                ),
                ([], "all_human_rows"),
            )

    def test_prepare_database_queries(self) -> None:
        result = server.prepare_database_queries(
            {
                "pdb_id": "4E5G",
                "ligand_id": "XI7",
                "inchi_key": "AAAA-BBBBBBBBBB-CCCCCCCCCC-N",
                "doi": "10.1128/aac.38.12.2827",
            }
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["identity_precedence"][0], "exact InChIKey")
        self.assertEqual(len(result["queries"]), 4)

    def test_database_query_ledger_hashes_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export = root / "export.csv"
            export.write_text("PDB,Ligand\n4E5G,XI7\n", encoding="utf-8")
            ledger = root / "database_query_ledger.csv"
            result = server.record_database_query(
                {
                    "ledger_path": str(ledger),
                    "database": "Reaxys",
                    "pdb_id": "4E5G",
                    "ligand": "XI7",
                    "query": "exact structure XI7",
                    "filters": "bioactivity",
                    "result_count": 1,
                    "export_path": str(export),
                    "checked_status": "inspected_archive",
                    "access_status": "live_access_unavailable",
                }
            )
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["row"]["Export_SHA256"]), 64)
            self.assertTrue(result["row"]["Query_ID"].startswith("DQ-"))

    def test_blocker_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "blockers.csv"
            result = server.record_blocker(
                {
                    "ledger_path": str(ledger),
                    "pdb_id": "4E5G",
                    "blocker_type": "supplement_missing",
                    "original_detail": "SI URL returned 404",
                    "resolution_status": "requires_authorized_session",
                    "resolution_route": "browser",
                    "remaining_action": "inspect authorized session",
                }
            )
            self.assertTrue(result["ok"])
            self.assertTrue(ledger.exists())
            self.assertTrue(result["row"]["Blocker_ID"].startswith("BL-"))

    def test_legacy_ledgers_are_migrated_without_column_shift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_ledger = root / "database.csv"
            database_ledger.write_text("PDB_ID,Query\n1ABC,old\n", encoding="utf-8")
            server.record_database_query(
                {
                    "ledger_path": str(database_ledger),
                    "database": "Reaxys",
                    "pdb_id": "4E5G",
                    "query": "exact structure",
                }
            )
            with database_ledger.open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["PDB_ID"], "1ABC")
            self.assertEqual(rows[1]["PDB_ID"], "4E5G")
            self.assertEqual(rows[1]["Query"], "exact structure")

            blocker_ledger = root / "blockers.csv"
            blocker_ledger.write_text("PDB_ID,Original_Detail\n", encoding="utf-8")
            server.record_blocker(
                {
                    "ledger_path": str(blocker_ledger),
                    "pdb_id": "4E5G",
                    "blocker_type": "missing_si",
                    "original_detail": "404",
                }
            )
            with blocker_ledger.open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["PDB_ID"], "4E5G")
            self.assertTrue(rows[0]["Blocker_ID"].startswith("BL-"))

    def test_concurrent_ledger_writes_keep_one_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "database.csv"

            def write(index: int) -> None:
                server.record_database_query(
                    {
                        "ledger_path": str(ledger),
                        "database": "Reaxys",
                        "pdb_id": "4E5G",
                        "query": f"query {index}",
                        "result_count": index,
                    }
                )

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(write, range(16)))
            raw_lines = ledger.read_text(encoding="utf-8-sig").splitlines()
            self.assertEqual(
                sum(line.startswith("Date,Database") for line in raw_lines),
                1,
            )
            with ledger.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 16)

    def test_run_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text("source", encoding="utf-8")
            output = root / "manifest.json"
            result = server.create_run_manifest(
                {"root": str(root), "output": str(output)}
            )
            self.assertTrue(output.exists())
            self.assertGreaterEqual(result["file_count"], 1)
            hashes = {
                Path(item["path"]).name: item["sha256"]
                for item in result["files"]
            }
            self.assertEqual(len(hashes["source.txt"]), 64)
            self.assertNotIn(output.name, hashes)


if __name__ == "__main__":
    unittest.main()

