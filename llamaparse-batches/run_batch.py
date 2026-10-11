#!/usr/bin/env python3
"""Run a LlamaParse / LlamaExtract *batch* over every file in a directory.

This is a runnable implementation of the "Getting Started with Batches" flow:

    create ephemeral directory -> upload files -> create batch
        -> poll to a terminal state -> inspect per-file results
        -> fetch the underlying Parse/Extract job statuses

Docs: https://developers.llamaindex.ai/llamaparse/batches/getting_started/

Examples
--------
    # Generate some throwaway PDFs first
    python make_sample_pdfs.py

    # End-to-end: upload ./invoices and parse them with the agentic preset
    export LLAMA_CLOUD_API_KEY="llx-..."
    python run_batch.py --source-dir ./invoices --config-id cfg-PARSE_AGENTIC

    # Inspect an existing batch (no upload)
    python run_batch.py --inspect bat-1234...

    # Dry run: validate inputs without calling the API
    python run_batch.py --source-dir ./invoices --dry-run

A ``.env`` file next to this script (see ``.env.example``) is loaded automatically.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

try:
    from llama_cloud import AsyncLlamaCloud
except ImportError:  # pragma: no cover - guidance for a missing dependency
    sys.exit(
        "The 'llama-cloud' package is not installed.\n"
        "Install it with:  pip install -r requirements.txt"
    )

TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}
# Built-in Parse presets usable without creating a saved configuration.
PARSE_PRESETS = {"cfg-PARSE_AGENTIC"}
DEFAULT_CONFIG_ID = "cfg-PARSE_AGENTIC"


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def load_dotenv(path: Path) -> None:
    """Minimal .env loader (no python-dotenv dependency). Does not override."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def resolve_files(source_dir: Path, pattern: str) -> list[Path]:
    if not source_dir.is_dir():
        sys.exit(f"Source directory does not exist: {source_dir}")
    files = sorted(p for p in source_dir.glob(pattern) if p.is_file())
    if not files:
        sys.exit(f"No files matching {pattern!r} found in {source_dir}")
    if len(files) > 10_000:
        sys.exit(
            f"{len(files)} files found; batch creation is limited to 10,000 "
            "source files."
        )
    return files


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Core steps
# --------------------------------------------------------------------------- #
async def create_and_run(client: AsyncLlamaCloud, args: argparse.Namespace) -> int:
    source_dir = Path(args.source_dir)
    files = resolve_files(source_dir, args.pattern)
    log(f"Found {len(files)} file(s) in {source_dir} matching {args.pattern!r}")

    dir_type = "user" if args.keep_directory else "ephemeral"
    directory = await client.beta.directories.create(
        name=args.directory_name,
        type=dir_type,
    )
    log(f"Created directory {directory.id} (type={dir_type})")

    for path in files:
        await client.beta.directories.files.upload(
            directory.id,
            upload_file=path,
            display_name=path.name,
        )
        log(f"  uploaded {path.name}")

    batch = await client.batches.create(
        source_directory_id=directory.id,
        config={
            "job": {
                "type": args.job_type,
                "configuration_id": args.config_id,
            },
        },
    )
    log(f"Created batch {batch.id} (status={batch.status})")

    batch = await wait_for_batch(client, batch.id, args.poll_interval, args.timeout)
    log(f"Batch reached terminal state: {batch.status}")

    if batch.status == "FAILED":
        log("Batch orchestration FAILED -- no reliable per-file results available.")

    return await report_results(client, batch.id)


async def wait_for_batch(
    client: AsyncLlamaCloud, batch_id: str, poll_interval: float, timeout: float
):
    """Poll until the batch hits a terminal state or we time out."""
    deadline = time.monotonic() + timeout
    batch = await client.batches.get(batch_id)
    while batch.status not in TERMINAL_STATUSES:
        if time.monotonic() > deadline:
            log(f"Timed out after {timeout:.0f}s; last status={batch.status}")
            return batch
        log(f"  status={batch.status}; sleeping {poll_interval:.0f}s")
        await asyncio.sleep(poll_interval)
        batch = await client.batches.get(batch_id)
    return batch


async def report_results(client: AsyncLlamaCloud, batch_id: str) -> int:
    """Fetch per-file results and the underlying job statuses. Returns exit code."""
    detail = await client.batches.get(batch_id, expand=["results"])
    results = detail.results or []

    if not results:
        log("No per-file results yet (results may be null while running).")
        return 0

    log(f"Per-file results ({len(results)}):")
    failures = 0
    refs: list[tuple[str, str]] = []
    for result in results:
        if result.error_message:
            failures += 1
            print(f"  FAIL {result.source_directory_file_id}: {result.error_message}")
            continue
        ref = result.job_reference
        if ref is None:
            print(f"  PENDING {result.source_directory_file_id}: no job yet")
            continue
        refs.append((ref.type, ref.id))
        print(f"  OK   {result.source_directory_file_id} -> {ref.type} {ref.id}")

    if refs:
        log("Underlying job statuses:")
        for ref_type, ref_id in refs:
            status = await _job_status(client, ref_type, ref_id)
            print(f"  {ref_id} ({ref_type}): {status}")

    if failures:
        log(f"{failures} file(s) failed -- see error_message above.")
    return 1 if failures else 0


async def _job_status(client: AsyncLlamaCloud, ref_type: str, ref_id: str) -> str:
    try:
        if ref_type == "parse_v2":
            job = await client.parsing.get(ref_id)
            return job.job.status
        job = await client.extract.get(ref_id)
        return job.status
    except Exception as exc:  # noqa: BLE001 - best-effort reporting
        return f"<unavailable: {exc}>"


async def inspect_batch(client: AsyncLlamaCloud, batch_id: str) -> int:
    batch = await client.batches.get(batch_id)
    log(f"Batch {batch.id}: {batch.status}")
    return await report_results(client, batch_id)



# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a Parse/Extract batch over a directory.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--source-dir", default="./invoices",
                        help="local directory whose files will be uploaded")
    parser.add_argument("--pattern", default="*.pdf",
                        help="glob used to select files in --source-dir")
    parser.add_argument("--job-type", default="parse_v2",
                        choices=["parse_v2", "extract_v2"])
    parser.add_argument("--config-id", default=DEFAULT_CONFIG_ID,
                        help="product configuration ID for the job")
    parser.add_argument("--directory-name", default="llamaparse-batch",
                        help="name for the created source directory")
    parser.add_argument("--keep-directory", action="store_true",
                        help="create a 'user' directory instead of 'ephemeral'")
    parser.add_argument("--poll-interval", type=float, default=10.0,
                        help="seconds between status polls")
    parser.add_argument("--timeout", type=float, default=3600.0,
                        help="max seconds to wait for the batch to finish")
    parser.add_argument("--inspect", metavar="BATCH_ID",
                        help="inspect an existing batch instead of creating one")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate inputs and exit without calling the API")
    parser.add_argument("--env-file", default=str(Path(__file__).with_name(".env")),
                        help="path to a .env file with LLAMA_CLOUD_API_KEY")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv(Path(args.env_file))

    if args.job_type == "parse_v2" and args.config_id not in PARSE_PRESETS:
        log(f"Note: {args.config_id!r} is not a known built-in preset; "
            "assuming it is a saved Parse configuration ID.")
    if args.job_type == "extract_v2" and args.config_id in PARSE_PRESETS:
        sys.exit("extract_v2 requires a saved Extract configuration ID, "
                 f"not the preset {args.config_id!r}.")

    if args.dry_run:
        if args.inspect:
            print(f"[dry-run] would inspect batch {args.inspect}")
        else:
            files = resolve_files(Path(args.source_dir), args.pattern)
            print(f"[dry-run] would upload {len(files)} file(s) from {args.source_dir}:")
            for f in files:
                print(f"  - {f.name}")
            print(f"[dry-run] job_type={args.job_type} config_id={args.config_id}")
        return 0

    if not os.environ.get("LLAMA_CLOUD_API_KEY"):
        sys.exit(
            "LLAMA_CLOUD_API_KEY is not set.\n"
            "Export it or copy .env.example to .env and fill it in."
        )

    async def _run() -> int:
        # Reads LLAMA_CLOUD_API_KEY from the environment.
        async with AsyncLlamaCloud() as client:
            if args.inspect:
                return await inspect_batch(client, args.inspect)
            return await create_and_run(client, args)

    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        log("Interrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

