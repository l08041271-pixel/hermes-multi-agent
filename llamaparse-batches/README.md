# LlamaParse Batches — runnable starter

Three equivalent ways to run the
[Getting Started with Batches](https://developers.llamaindex.ai/llamaparse/batches/getting_started/)
flow against the LlamaCloud API:

```
create ephemeral directory → upload files → create batch
    → poll to a terminal state → inspect per-file results
    → fetch underlying Parse/Extract job statuses
```

| Interface | Entry point | Notes |
| --- | --- | --- |
| Python SDK | `run_batch.py` | Official `llama-cloud` SDK (`AsyncLlamaCloud`). |
| TypeScript SDK | `typescript/run-batch.ts` | Official `@llamaindex/llama-cloud` SDK. |
| CLI (`llp`) | `run_batch.sh` | Wraps the `llp` CLI + `jq`. |

## What's here

| File | Purpose |
| --- | --- |
| `run_batch.py` | Python driver (create → upload → batch → poll → report). |
| `typescript/run-batch.ts` | TypeScript port of the same flow. |
| `run_batch.sh` | `llp` CLI + `jq` equivalent. |
| `make_sample_pdfs.py` | Generates tiny valid PDFs so you can test without real data. |
| `tests/test_run_batch.py` | Offline smoke tests (no network / no key). |
| `requirements.txt`, `requirements-dev.txt` | Python deps (`llama-cloud`, `pytest`). |
| `.env.example` | Template for your API key. |

## Setup (Python)

```bash
cd llamaparse-batches
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # add -r requirements-dev.txt for tests

cp .env.example .env                   # then set LLAMA_CLOUD_API_KEY=llx-...
```

## Quickstart

```bash
# 1. Create throwaway PDFs in ./invoices
python make_sample_pdfs.py --count 3

# 2. Validate inputs without hitting the API
python run_batch.py --source-dir ./invoices --dry-run

# 3. Run the batch (parse every PDF with the agentic preset)
python run_batch.py --source-dir ./invoices --config-id cfg-PARSE_AGENTIC
```

Exit codes: `0` success, `1` one or more files failed, `130` interrupted.

## TypeScript

```bash
cd llamaparse-batches/typescript
npm install
npm run typecheck
npm start -- --source-dir ../invoices --config-id cfg-PARSE_AGENTIC
```

Reads `LLAMA_CLOUD_API_KEY` from the environment or a `.env` file.

## CLI (`llp`)

Requires `llp` and `jq` on `PATH`.

```bash
export LLAMA_CLOUD_API_KEY="llx-..."
./run_batch.sh --source-dir ./invoices --config-id cfg-PARSE_AGENTIC
./run_batch.sh --source-dir ./invoices --dry-run   # print the llp commands only
```

## Configuration IDs

- **`parse_v2`** — use a built-in preset (`cfg-PARSE_AGENTIC`) or a saved Parse
  configuration ID.
- **`extract_v2`** — must be a **saved** Extract configuration ID; there is no
  built-in preset. Create one in the Extract playground or via the Product
  Configurations API.

```bash
python run_batch.py --source-dir ./invoices \
  --job-type extract_v2 --config-id cfg-YOUR-EXTRACT-CONFIG
```

## Common options (all three interfaces)

| Flag | Default | Notes |
| --- | --- | --- |
| `--source-dir` | `./invoices` | Local directory to upload. |
| `--pattern` | `*.pdf` | Glob selecting files within `--source-dir`. |
| `--job-type` | `parse_v2` | `parse_v2` or `extract_v2`. |
| `--config-id` | `cfg-PARSE_AGENTIC` | Product configuration ID. |
| `--keep-directory` | off | Create a `user` dir instead of `ephemeral`. |
| `--poll-interval` | `10` | Seconds between status polls. |
| `--timeout` | `3600` | Max seconds to wait for completion. |
| `--inspect BATCH_ID` | — | Report on an existing batch; no upload. |
| `--dry-run` | off | Validate inputs only; no API calls. |

## Behaviour & semantics

- **Ephemeral directories** (the default) are auto-eligible for cleanup and are
  exempt from storage billing and per-project storage limits. Use
  `--keep-directory` if you want the uploads to persist.
- **Terminal states** are `COMPLETED`, `FAILED`, and `CANCELLED`.
- **Batch-level `FAILED`** means orchestration failed and no reliable per-file
  result set is available.
- **Per-file failures** appear in `results[*].error_message`. Successful files
  carry a `job_reference`; each interface then queries the referenced
  Parse/Extract job endpoint for the underlying status.
- Batch creation is capped at **10,000 source files** (enforced client-side).

## Inspecting an existing batch

```bash
python run_batch.py --inspect bat-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
./run_batch.sh   --inspect bat-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
```

## Tests & CI

```bash
cd llamaparse-batches
pip install -r requirements-dev.txt
python -m pytest -q
```

The suite is fully offline (a fake client stands in for the SDK); a live
end-to-end test auto-skips unless `LLAMA_CLOUD_API_KEY` is set.

A ready-to-use GitHub Actions workflow is provided at
`ci/llamaparse-batches-smoke.yml`. It runs the Python suite on 3.11/3.12 and
the TypeScript typecheck + dry run, and executes a live batch only when the
`LLAMA_CLOUD_API_KEY` repository secret is configured.

To enable it, copy the file into place (this requires a token/app with
`workflows` permission):

```bash
mkdir -p .github/workflows
cp llamaparse-batches/ci/llamaparse-batches-smoke.yml .github/workflows/
```

## REST equivalents

| Operation | Endpoint |
| --- | --- |
| Create batch | `POST /api/v2/batches` |
| List batches | `GET /api/v2/batches` |
| Get batch | `GET /api/v2/batches/{batch_id}` |

## Troubleshooting

- **`SSL: CERTIFICATE_VERIFY_FAILED` on `pip install`** — happens behind a
  TLS-inspecting proxy. Point pip at the proxy CA, e.g.
  `pip install --cert /etc/mitm/ca.crt -r requirements.txt`, or set
  `PIP_CERT=/path/to/ca.crt`.
- **`LLAMA_CLOUD_API_KEY is not set`** — export it or put it in `.env`.
- **`extract_v2 requires a saved Extract configuration ID`** — you passed a
  Parse preset to an extraction job; supply a saved `extract_v2` config ID.
- **`required tool not found on PATH: llp`** — install the LlamaCloud CLI, or
  use the Python/TypeScript drivers instead.
