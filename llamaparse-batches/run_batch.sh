#!/usr/bin/env bash
#
# LlamaCloud CLI (`llp`) equivalent of run_batch.py.
#
#   create ephemeral directory -> upload files -> create batch
#       -> poll to a terminal state -> inspect per-file results
#
# Requires `llp` and `jq` on PATH, plus LLAMA_CLOUD_API_KEY in the environment.
# Docs: https://developers.llamaindex.ai/llamaparse/batches/getting_started/
#
# Usage:
#   export LLAMA_CLOUD_API_KEY="llx-..."
#   ./run_batch.sh --source-dir ./invoices --config-id cfg-PARSE_AGENTIC
#   ./run_batch.sh --source-dir ./invoices --dry-run   # print commands only
#
set -euo pipefail

SOURCE_DIR="./invoices"
PATTERN="*.pdf"
JOB_TYPE="parse_v2"
CONFIG_ID="cfg-PARSE_AGENTIC"
DIRECTORY_NAME="llamaparse-batch"
DIR_TYPE="ephemeral"
POLL_INTERVAL=10
TIMEOUT=3600
DRY_RUN=0
INSPECT=""

usage() {
  cat <<'EOF'
Usage: run_batch.sh [options]

  --source-dir <dir>      local directory to upload (default ./invoices)
  --pattern <glob>        file selection glob (default *.pdf)
  --job-type <type>       parse_v2 | extract_v2 (default parse_v2)
  --config-id <id>        product configuration ID (default cfg-PARSE_AGENTIC)
  --directory-name <n>    source directory name
  --keep-directory        create a 'user' dir instead of 'ephemeral'
  --poll-interval <s>     seconds between polls (default 10)
  --timeout <s>           max seconds to wait (default 3600)
  --inspect <batch-id>    report on an existing batch
  --dry-run               print the llp commands without running them
  -h, --help              show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-dir)      SOURCE_DIR="$2"; shift 2;;
    --pattern)         PATTERN="$2"; shift 2;;
    --job-type)        JOB_TYPE="$2"; shift 2;;
    --config-id)       CONFIG_ID="$2"; shift 2;;
    --directory-name)  DIRECTORY_NAME="$2"; shift 2;;
    --keep-directory)  DIR_TYPE="user"; shift;;
    --poll-interval)   POLL_INTERVAL="$2"; shift 2;;
    --timeout)         TIMEOUT="$2"; shift 2;;
    --inspect)         INSPECT="$2"; shift 2;;
    --dry-run)         DRY_RUN=1; shift;;
    -h|--help)         usage; exit 0;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2;;
  esac
done

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

# Echo + optionally execute. In dry-run mode commands are printed only.
# The echo goes to stderr so it never pollutes captured stdout (e.g. `| jq`).
run() {
  echo "+ $*" >&2
  [[ "$DRY_RUN" -eq 1 ]] || "$@"
}

require_tools() {
  # In dry-run mode nothing is executed, so tools need not be present.
  [[ "$DRY_RUN" -eq 1 ]] && return 0
  local missing=0
  for tool in llp jq; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      echo "required tool not found on PATH: $tool" >&2
      missing=1
    fi
  done
  [[ "$missing" -eq 0 ]] || exit 127
}

report_results() {
  local batch_id="$1" json failures
  json=$(llp batches get --batch-id "$batch_id" --expand '[results]')

  log "Per-file results:"
  echo "$json" | jq -r '
      .results[]? |
      if .error_message then "  FAIL \(.source_directory_file_id): \(.error_message)"
      elif .job_reference == null then "  PENDING \(.source_directory_file_id): no job yet"
      else "  OK   \(.source_directory_file_id) -> \(.job_reference.type) \(.job_reference.id)"
      end'

  log "Underlying job statuses:"
  echo "$json"     | jq -r '.results[]? | select(.job_reference != null) | "\(.job_reference.type) \(.job_reference.id)"'     | while read -r TYPE ID; do
        if [[ "$TYPE" == "parse_v2" ]]; then
          echo "  $ID ($TYPE): $(llp parsing get --job-id "$ID" | jq -r '.job.status')"
        else
          echo "  $ID ($TYPE): $(llp extract get --job-id "$ID" | jq -r '.status')"
        fi
      done

  failures=$(echo "$json" | jq '[.results[]? | select(.error_message != null)] | length')
  if [[ "${failures:-0}" -gt 0 ]]; then
    log "$failures file(s) failed -- see error_message above."
    return 1
  fi
  return 0
}

main() {
  require_tools

  if [[ "$JOB_TYPE" == "extract_v2" && "$CONFIG_ID" == "cfg-PARSE_AGENTIC" ]]; then
    echo "extract_v2 requires a saved Extract configuration ID, not the preset '$CONFIG_ID'." >&2
    exit 2
  fi

  if [[ "$DRY_RUN" -eq 0 && -z "${LLAMA_CLOUD_API_KEY:-}" ]]; then
    echo "LLAMA_CLOUD_API_KEY is not set." >&2
    exit 2
  fi

  if [[ -n "$INSPECT" ]]; then
    log "Batch $INSPECT: $(llp batches get --batch-id "$INSPECT" | jq -r '.status')"
    report_results "$INSPECT"
    return
  fi

  if [[ ! -d "$SOURCE_DIR" ]]; then
    echo "source directory does not exist: $SOURCE_DIR" >&2
    exit 2
  fi
  shopt -s nullglob
  local files=("$SOURCE_DIR"/$PATTERN)
  shopt -u nullglob
  if [[ ${#files[@]} -eq 0 ]]; then
    echo "no files matching $PATTERN in $SOURCE_DIR" >&2
    exit 2
  fi
  if [[ ${#files[@]} -gt 10000 ]]; then
    echo "batch creation is limited to 10,000 source files (found ${#files[@]})." >&2
    exit 2
  fi
  log "Found ${#files[@]} file(s) in $SOURCE_DIR matching $PATTERN"

  local directory_id
  directory_id=$(run llp beta:directories create \
    --name "$DIRECTORY_NAME" --type "$DIR_TYPE" | jq -r '.id')
  [[ "$DRY_RUN" -eq 1 ]] && directory_id='<directory-id>'
  log "Directory: $directory_id"

  for file in "${files[@]}"; do
    run llp beta:directories:files upload \
      --directory-id "$directory_id" \
      --upload-file "$file" \
      --display-name "$(basename "$file")"
  done

  local batch_id
  batch_id=$(run llp batches create \
    --source-directory-id "$directory_id" \
    --config "{job: {type: $JOB_TYPE, configuration_id: $CONFIG_ID}}" | jq -r '.id')
  [[ "$DRY_RUN" -eq 1 ]] && batch_id='<batch-id>'
  log "Batch: $batch_id"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "Dry run complete; skipping polling and result inspection."
    return
  fi

  local deadline=$(( $(date +%s) + TIMEOUT )) status
  while true; do
    status=$(llp batches get --batch-id "$batch_id" | jq -r '.status')
    case "$status" in
      COMPLETED|FAILED|CANCELLED) break;;
    esac
    if [[ $(date +%s) -ge $deadline ]]; then
      log "Timed out after ${TIMEOUT}s; last status=$status"
      return
    fi
    log "  status=$status; sleeping ${POLL_INTERVAL}s"
    sleep "$POLL_INTERVAL"
  done
  log "Batch reached terminal state: $status"

  report_results "$batch_id"
}

main "$@"
