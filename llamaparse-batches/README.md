# LlamaParse Batches — runnable starter

A small, dependency-light project that implements the
[Getting Started with Batches](https://developers.llamaindex.ai/llamaparse/batches/getting_started/)
flow against the LlamaCloud API:

```
create ephemeral directory → upload files → create batch
    → poll to a terminal state → inspect per-file results
    → fetch underlying Parse/Extract job statuses
```

It uses the official [`llama-cloud`](https://pypi.org/project/llama-cloud/) Python
SDK (`AsyncLlamaCloud`).

## What's here

| File | Purpose |
| --- | --- |
| `run_batch.py` | End-to-end driver (create → upload → batch → poll → report). |
| `make_sample_pdfs.py` | Generates tiny valid PDFs so you can test without real data. |
| `requirements.txt` | Python dependency (`llama-cloud`). |
| `.env.example` | Template for your API key. |

## Setup

```bash
cd llamaparse-batches
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Provide your API key (Pro/Enterprise plan required)
cp .env.example .env
#   then edit .env and set LLAMA_CLOUD_API_KEY=...
```

## Quickstart

```bash
# 1. Create some throwaway PDFs in ./invoices
python make_sample_pdfs.py --count 3

# 2. Validate inputs without hitting the API
python run_batch.py --source-dir ./invoices --dry-run

# 3. Run the batch (parse every PDF with the agentic preset)
python run_batch.py --source-dir ./invoices --config-id cfg-PARSE_AGENTIC
```

Exit codes: `0` success, `1` one or more files failed, `130` interrupted.

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

## Common options

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
  carry a `job_reference`; the script then queries the referenced Parse/Extract
  job endpoint for the underlying status.
- Batch creation is capped at **10,000 source files** (enforced client-side).

## Inspecting an existing batch

```bash
python run_batch.py --inspect bat-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
```

## REST equivalents

| Operation | Endpoint |
| --- | --- |
| Create batch | `POST /api/v2/batches` |
| List batches | `GET /api/v2/batches` |
| Get batch | `GET /api/v2/batches/{batch_id}` |

## Troubleshooting

- **`SSL: CERTIFICATE_VERIFY_FAILED` on `pip install`** — this happens behind a
  TLS-inspecting proxy. Point pip at the proxy CA, e.g.
  `pip install --cert /etc/mitm/ca.crt -r requirements.txt`, or add the CA via
  `PIP_CERT=/path/to/ca.crt`.
- **`LLAMA_CLOUD_API_KEY is not set`** — export it or put it in `.env`.
- **`extract_v2 requires a saved Extract configuration ID`** — you passed a
  Parse preset to an extraction job; supply a saved `extract_v2` config ID.
