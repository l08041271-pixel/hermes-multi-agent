#!/usr/bin/env python3
"""Unit tests for scripts/ceo/memos-hub-mcp/server.py (v1 backend).

Spins up the same in-process fake MemOS HTTP server used by the bash tests,
configures the MCP module's env to point at it, imports the module, and
calls each tool function directly. Skips cleanly if the `mcp` package
isn't installed.

The MCP server lives at the v2-era path (memos-hub-mcp/) but talks to v1
endpoints — same MCP server name and tool signatures so existing
claude.json registrations don't need to be re-pointed.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CEO_DIR = ROOT.parent
MCP_DIR = CEO_DIR / "memos-hub-mcp"

try:
    from mcp.server.fastmcp import FastMCP  # noqa: F401
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False


def _wait_for_port(port_file: Path, deadline: float) -> int:
    while time.time() < deadline:
        if port_file.exists() and port_file.stat().st_size:
            return int(port_file.read_text().strip())
        time.sleep(0.05)
    raise RuntimeError("fake server did not start in time")


def _wait_for_http(port: int, deadline: float) -> None:
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"fake server port {port} not accepting connections")


@unittest.skipUnless(
    MCP_AVAILABLE,
    "mcp package not installed; install scripts/ceo/memos-hub-mcp deps to run",
)
class MCPServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="mcp-test-"))
        cls.log_path = cls.tmpdir / "req.log"
        cls.port_file = cls.tmpdir / "port"
        cls.skills_dir = cls.tmpdir / "badass-skills"
        cls.skills_dir.mkdir()

        # Two minimal skills the test can introspect.
        for name, desc in (
            ("notebooklm", "Build NotebookLM notebooks programmatically."),
            ("pdf", "Extract text from PDFs with OCR fallback."),
        ):
            skill_dir = cls.skills_dir / name
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n"
            )

        # Spin up the fake MemOS server.
        cls.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "_fake_memos.py"),
             "--log", str(cls.log_path),
             "--port-file", str(cls.port_file)],
        )
        try:
            cls.port = _wait_for_port(cls.port_file, time.time() + 5)
            _wait_for_http(cls.port, time.time() + 5)
        except Exception:
            cls.proc.kill()
            raise

        # Configure the MCP module BEFORE importing it: the module exits at
        # import time if MEMOS_API_KEY is unset.
        os.environ["MEMOS_ENDPOINT"] = f"http://127.0.0.1:{cls.port}"
        os.environ["MEMOS_API_KEY"] = "test-mcp-key"
        os.environ["MEMOS_USER_ID"] = "ceo"
        os.environ["MEMOS_WRITABLE_CUBE_IDS"] = "ceo-cube"
        os.environ["MEMOS_READABLE_CUBE_IDS"] = "ceo-cube,research-cube"
        os.environ["BADASS_SKILLS_DIR"] = str(cls.skills_dir)

        sys.path.insert(0, str(MCP_DIR))
        import server as _server  # noqa: E402  (import after env setup)
        cls.server = _server

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    # ── helpers ────────────────────────────────────────────────────────────
    def _last_request(self) -> dict:
        with self.log_path.open() as f:
            lines = [json.loads(line) for line in f if line.strip()]
        self.assertTrue(lines, "no requests captured by fake server")
        return lines[-1]

    # ── memos_search ───────────────────────────────────────────────────────
    def test_search_payload_and_adapter(self):
        result = self.server.memos_search("hydrazine", max_results=7)

        req = self._last_request()
        self.assertEqual(req["path"], "/product/search")
        self.assertEqual(req["body"]["query"], "hydrazine")
        self.assertEqual(req["body"]["user_id"], "ceo")
        self.assertEqual(req["body"]["top_k"], 7)
        self.assertEqual(
            sorted(req["body"]["readable_cube_ids"]),
            ["ceo-cube", "research-cube"],
        )
        self.assertEqual(req["headers"].get("Authorization"), "Bearer test-mcp-key")

        self.assertEqual(result["totalHits"], 2)
        # The in-place server projects v1 onto v2 hits[]; cubeId surfaces as ownerName.
        owners = sorted(h["ownerName"] for h in result["hits"])
        self.assertEqual(owners, ["ceo-cube", "research-cube"])

    def test_search_clamps_max_results(self):
        self.server.memos_search("anything", max_results=999)
        req = self._last_request()
        # The in-place server caps at 40, not 50, on memos_search.
        self.assertEqual(req["body"]["top_k"], 40)

    # ── memos_store ────────────────────────────────────────────────────────
    def test_store_payload_and_tags(self):
        result = self.server.memos_store(
            content="Body of the memory",
            summary="short tldr",
            chunk_id="stable-id-7",
        )

        req = self._last_request()
        self.assertEqual(req["path"], "/product/add")
        self.assertEqual(req["body"]["user_id"], "ceo")
        self.assertEqual(req["body"]["writable_cube_ids"], ["ceo-cube"])
        self.assertEqual(req["body"]["mode"], "fine")
        self.assertEqual(req["body"]["async_mode"], "sync")
        self.assertEqual(req["body"]["messages"][0]["role"], "assistant")
        self.assertEqual(req["body"]["messages"][0]["content"], "Body of the memory")

        tags = req["body"]["custom_tags"]
        self.assertIn("agent:ceo", tags)
        self.assertIn("chunk_id:stable-id-7", tags)
        self.assertIn("summary:short tldr", tags)

        self.assertEqual(result["status"], "stored")
        self.assertEqual(result["chunk_id"], "stable-id-7")

    def test_store_rejects_empty_content(self):
        result = self.server.memos_store(content="   ")
        self.assertEqual(result["status"], "error")

    def test_store_rejects_bad_mode(self):
        result = self.server.memos_store(content="x", mode="bogus")
        self.assertEqual(result["status"], "error")

    # ── memos_recent ───────────────────────────────────────────────────────
    def test_recent_uses_search_with_empty_query(self):
        result = self.server.memos_recent(limit=10)
        req = self._last_request()
        self.assertEqual(req["path"], "/product/search")
        self.assertEqual(req["body"]["query"], "")
        self.assertEqual(req["body"]["top_k"], 10)
        # 2 memories in CANNED_SEARCH (one per bucket) → flattened to memories[].
        self.assertEqual(len(result["memories"]), 2)
        self.assertEqual(result["tasks"], [])

    # ── memos_list_skills ──────────────────────────────────────────────────
    def test_list_skills_reads_frontmatter(self):
        result = self.server.memos_list_skills()
        names = sorted(s["name"] for s in result["skills"])
        self.assertEqual(names, ["notebooklm", "pdf"])
        descs = {s["name"]: s["description"] for s in result["skills"]}
        self.assertIn("NotebookLM", descs["notebooklm"])
        self.assertEqual(result["repo"], "https://github.com/sergiocoding96/badass-skills")

    def test_list_skills_filters_by_query(self):
        result = self.server.memos_list_skills(query="pdf")
        self.assertEqual(result["totalSkills"], 1)
        self.assertEqual(result["skills"][0]["name"], "pdf")

    def test_list_skills_handles_missing_dir(self):
        original = os.environ["BADASS_SKILLS_DIR"]
        try:
            self.server.BADASS_SKILLS_DIR = Path("/nonexistent-skills-dir")
            result = self.server.memos_list_skills()
            self.assertEqual(result["totalSkills"], 0)
            self.assertIn("warning", result)
        finally:
            self.server.BADASS_SKILLS_DIR = Path(original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
