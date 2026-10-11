"""Offline smoke tests for run_batch.py — no network or API key required.

Run:  pytest -q
A live end-to-end test is included but auto-skips unless LLAMA_CLOUD_API_KEY
is set (and the account is on a Pro/Enterprise plan).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_batch  # noqa: E402
from make_sample_pdfs import build_pdf  # noqa: E402


# --------------------------------------------------------------------------- #
# Fake that mimics the AsyncLlamaCloud surface used by run_batch.py
# --------------------------------------------------------------------------- #
class FakeClient:
    def __init__(self, results):
        self.uploaded: list[str] = []
        self._results = results
        self.dir_type = None
        self.config = None
        directories = SimpleNamespace(
            create=self._dir_create,
            files=SimpleNamespace(upload=self._upload),
        )
        self.beta = SimpleNamespace(directories=directories)
        self.batches = SimpleNamespace(create=self._batch_create, get=self._batch_get)
        self.parsing = SimpleNamespace(get=self._parsing_get)
        self.extract = SimpleNamespace(get=self._extract_get)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def _dir_create(self, *, name, type="user", **kw):
        self.dir_type = type
        return SimpleNamespace(id="dir-1", name=name, type=type)

    async def _upload(self, directory_id, *, upload_file, display_name=None, **kw):
        self.uploaded.append(display_name)
        return SimpleNamespace(id=f"dfl-{display_name}")

    async def _batch_create(self, *, source_directory_id, config):
        self.config = config
        return SimpleNamespace(id="bat-1", status="PENDING")

    async def _batch_get(self, batch_id, *, expand=None, **kw):
        results = self._results if expand else None
        return SimpleNamespace(id=batch_id, status="COMPLETED", results=results)

    async def _parsing_get(self, job_id, **kw):
        return SimpleNamespace(job=SimpleNamespace(status="COMPLETED"))

    async def _extract_get(self, job_id, **kw):
        return SimpleNamespace(status="COMPLETED")


def _ok_results():
    return [
        SimpleNamespace(
            source_directory_file_id="dfl-1",
            error_message=None,
            job_reference=SimpleNamespace(type="parse_v2", id="pjb-1"),
        )
    ]


def _args(directory, *extra):
    return run_batch.parse_args(["--source-dir", str(directory), *extra])


@pytest.fixture()
def pdf_dir(tmp_path):
    for i in range(2):
        (tmp_path / f"inv_{i}.pdf").write_bytes(build_pdf([f"Invoice {i}"]))
    return tmp_path


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_build_pdf_is_wellformed():
    data = build_pdf(["hello world"])
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")
    assert b"/Type /Catalog" in data


def test_resolve_files(pdf_dir):
    files = run_batch.resolve_files(pdf_dir, "*.pdf")
    assert [f.name for f in files] == ["inv_0.pdf", "inv_1.pdf"]


def test_dry_run_exits_zero(pdf_dir, capsys):
    assert run_batch.main(["--source-dir", str(pdf_dir), "--dry-run"]) == 0
    assert "would upload 2 file(s)" in capsys.readouterr().out


def test_create_and_run_happy_path(pdf_dir):
    fake = FakeClient(_ok_results())
    rc = asyncio.run(run_batch.create_and_run(fake, _args(pdf_dir)))
    assert rc == 0
    assert fake.uploaded == ["inv_0.pdf", "inv_1.pdf"]
    assert fake.dir_type == "ephemeral"
    assert fake.config["job"] == {
        "type": "parse_v2",
        "configuration_id": "cfg-PARSE_AGENTIC",
    }


def test_keep_directory_uses_user_type(pdf_dir):
    fake = FakeClient(_ok_results())
    asyncio.run(run_batch.create_and_run(fake, _args(pdf_dir, "--keep-directory")))
    assert fake.dir_type == "user"


def test_per_file_failure_returns_1():
    results = [
        SimpleNamespace(
            source_directory_file_id="dfl-x",
            error_message="Unable to process source file.",
            job_reference=None,
        )
    ]
    fake = FakeClient(results)
    assert asyncio.run(run_batch.report_results(fake, "bat-1")) == 1


def test_no_results_returns_0():
    fake = FakeClient(None)
    assert asyncio.run(run_batch.report_results(fake, "bat-1")) == 0


def test_missing_directory_exits(capsys):
    with pytest.raises(SystemExit):
        run_batch.main(["--source-dir", "/definitely/not/here", "--dry-run"])


@pytest.mark.skipif(
    not os.environ.get("LLAMA_CLOUD_API_KEY"),
    reason="requires LLAMA_CLOUD_API_KEY (Pro/Enterprise plan)",
)
def test_live_smoke(pdf_dir):
    rc = run_batch.main(
        [
            "--source-dir",
            str(pdf_dir),
            "--config-id",
            "cfg-PARSE_AGENTIC",
            "--poll-interval",
            "5",
            "--timeout",
            "600",
        ]
    )
    assert rc == 0


def test_shell_script_dry_run(pdf_dir):
    if shutil.which("bash") is None:
        pytest.skip("bash not available")
    script = Path(__file__).resolve().parent.parent / "run_batch.sh"
    res = subprocess.run(
        ["bash", str(script), "--dry-run", "--source-dir", str(pdf_dir)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert "llp batches create" in res.stderr
    assert "llp beta:directories create" in res.stderr
