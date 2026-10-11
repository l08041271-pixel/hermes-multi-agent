#!/usr/bin/env node
/**
 * TypeScript port of run_batch.py — run a Parse/Extract batch over a directory.
 *
 *   create ephemeral directory -> upload files -> create batch
 *       -> poll to a terminal state -> inspect per-file results
 *       -> fetch underlying Parse/Extract job statuses
 *
 * Docs: https://developers.llamaindex.ai/llamaparse/batches/getting_started/
 *
 * Usage:
 *   npm install
 *   export LLAMA_CLOUD_API_KEY="llx-..."
 *   npm start -- --source-dir ../invoices --config-id cfg-PARSE_AGENTIC
 */

import fs from "fs";
import path from "path";
import { parseArgs } from "node:util";
import LlamaCloud from "@llamaindex/llama-cloud";

const TERMINAL_STATUSES = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
const PARSE_PRESETS = new Set(["cfg-PARSE_AGENTIC"]);
const DEFAULT_CONFIG_ID = "cfg-PARSE_AGENTIC";

interface Args {
  sourceDir: string;
  pattern: string;
  jobType: "parse_v2" | "extract_v2";
  configId: string;
  directoryName: string;
  keepDirectory: boolean;
  pollInterval: number;
  timeout: number;
  inspect?: string;
  dryRun: boolean;
}

/** Minimal .env loader (no dotenv dependency). Does not override. */
function loadDotenv(file: string): void {
  if (!fs.existsSync(file)) return;
  for (const raw of fs.readFileSync(file, "utf8").split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const idx = line.indexOf("=");
    const key = line.slice(0, idx).trim();
    const value = line.slice(idx + 1).trim().replace(/^["']|["']$/g, "");
    if (!(key in process.env)) process.env[key] = value;
  }
}

function log(msg: string): void {
  console.log(`[${new Date().toLocaleTimeString()}] ${msg}`);
}

function listFiles(dir: string, pattern: string): string[] {
  if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) {
    throw new Error(`Source directory does not exist: ${dir}`);
  }
  const suffix = pattern.startsWith("*") ? pattern.slice(1) : null;
  const files = fs
    .readdirSync(dir)
    .map((f) => path.join(dir, f))
    .filter((full) => fs.statSync(full).isFile())
    .filter((full) => (suffix ? full.endsWith(suffix) : true))
    .sort();
  if (files.length === 0) {
    throw new Error(`No files matching ${pattern} found in ${dir}`);
  }
  if (files.length > 10_000) {
    throw new Error("Batch creation is limited to 10,000 source files.");
  }
  return files;
}

// --------------------------------------------------------------------------- //
// Core steps
// --------------------------------------------------------------------------- //
// The SDK's generated types are wide; keep the client loosely typed here so the
// example stays readable across SDK minor versions.
type Client = any;

async function createAndRun(client: Client, args: Args): Promise<number> {
  const files = listFiles(args.sourceDir, args.pattern);
  log(`Found ${files.length} file(s) in ${args.sourceDir} matching ${args.pattern}`);

  const dirType = args.keepDirectory ? "user" : "ephemeral";
  const directory = await client.beta.directories.create({
    name: args.directoryName,
    type: dirType,
  });
  log(`Created directory ${directory.id} (type=${dirType})`);

  for (const file of files) {
    await client.beta.directories.files.upload(directory.id, {
      upload_file: fs.createReadStream(file),
      display_name: path.basename(file),
    });
    log(`  uploaded ${path.basename(file)}`);
  }

  let batch = await client.batches.create({
    source_directory_id: directory.id,
    config: {
      job: { type: args.jobType, configuration_id: args.configId },
    },
  });
  log(`Created batch ${batch.id} (status=${batch.status})`);

  batch = await waitForBatch(client, batch.id, args.pollInterval, args.timeout);
  log(`Batch reached terminal state: ${batch.status}`);
  if (batch.status === "FAILED") {
    log("Batch orchestration FAILED -- no reliable per-file results available.");
  }
  return reportResults(client, batch.id);
}

async function waitForBatch(
  client: Client,
  batchId: string,
  pollInterval: number,
  timeout: number,
): Promise<any> {
  const deadline = Date.now() + timeout * 1000;
  let batch = await client.batches.get(batchId);
  while (!TERMINAL_STATUSES.has(batch.status)) {
    if (Date.now() > deadline) {
      log(`Timed out; last status=${batch.status}`);
      return batch;
    }
    log(`  status=${batch.status}; sleeping ${pollInterval}s`);
    await new Promise((r) => setTimeout(r, pollInterval * 1000));
    batch = await client.batches.get(batchId);
  }
  return batch;
}

async function jobStatus(client: Client, refType: string, refId: string): Promise<string> {
  try {
    if (refType === "parse_v2") return (await client.parsing.get(refId)).job.status;
    return (await client.extract.get(refId)).status;
  } catch (err) {
    return `<unavailable: ${err}>`;
  }
}

async function reportResults(client: Client, batchId: string): Promise<number> {
  const detail = await client.batches.get(batchId, { expand: ["results"] });
  const results = detail.results ?? [];
  if (results.length === 0) {
    log("No per-file results yet (results may be null while running).");
    return 0;
  }

  log(`Per-file results (${results.length}):`);
  let failures = 0;
  const refs: Array<[string, string]> = [];
  for (const result of results) {
    if (result.error_message) {
      failures += 1;
      console.log(`  FAIL ${result.source_directory_file_id}: ${result.error_message}`);
      continue;
    }
    const ref = result.job_reference;
    if (!ref) {
      console.log(`  PENDING ${result.source_directory_file_id}: no job yet`);
      continue;
    }
    refs.push([ref.type, ref.id]);
    console.log(`  OK   ${result.source_directory_file_id} -> ${ref.type} ${ref.id}`);
  }

  if (refs.length > 0) {
    log("Underlying job statuses:");
    for (const [refType, refId] of refs) {
      console.log(`  ${refId} (${refType}): ${await jobStatus(client, refType, refId)}`);
    }
  }
  if (failures > 0) log(`${failures} file(s) failed -- see error_message above.`);
  return failures ? 1 : 0;
}


// --------------------------------------------------------------------------- //
// CLI
// --------------------------------------------------------------------------- //
function parseCliArgs(argv: string[]): Args {
  const { values } = parseArgs({
    args: argv,
    options: {
      "source-dir": { type: "string", default: "../invoices" },
      pattern: { type: "string", default: "*.pdf" },
      "job-type": { type: "string", default: "parse_v2" },
      "config-id": { type: "string", default: DEFAULT_CONFIG_ID },
      "directory-name": { type: "string", default: "llamaparse-batch" },
      "keep-directory": { type: "boolean", default: false },
      "poll-interval": { type: "string", default: "10" },
      timeout: { type: "string", default: "3600" },
      inspect: { type: "string" },
      "dry-run": { type: "boolean", default: false },
      "env-file": { type: "string", default: path.join(process.cwd(), ".env") },
      help: { type: "boolean", default: false },
    },
    allowPositionals: false,
  });

  if (values.help) {
    console.log(
      `Usage: run-batch.ts [options]\n\n` +
        `  --source-dir <dir>     local directory to upload (default ../invoices)\n` +
        `  --pattern <glob>       file selection glob (default *.pdf)\n` +
        `  --job-type <type>      parse_v2 | extract_v2 (default parse_v2)\n` +
        `  --config-id <id>       product configuration ID (default cfg-PARSE_AGENTIC)\n` +
        `  --directory-name <n>   source directory name\n` +
        `  --keep-directory       create a 'user' dir instead of 'ephemeral'\n` +
        `  --poll-interval <s>    seconds between polls (default 10)\n` +
        `  --timeout <s>          max seconds to wait (default 3600)\n` +
        `  --inspect <batch-id>   report on an existing batch\n` +
        `  --dry-run              validate inputs only; no API calls\n` +
        `  --env-file <path>      .env file with LLAMA_CLOUD_API_KEY\n`,
    );
    process.exit(0);
  }

  loadDotenv(values["env-file"] as string);

  return {
    sourceDir: values["source-dir"] as string,
    pattern: values.pattern as string,
    jobType: values["job-type"] as Args["jobType"],
    configId: values["config-id"] as string,
    directoryName: values["directory-name"] as string,
    keepDirectory: values["keep-directory"] as boolean,
    pollInterval: Number(values["poll-interval"]),
    timeout: Number(values.timeout),
    inspect: values.inspect as string | undefined,
    dryRun: values["dry-run"] as boolean,
  };
}

async function main(): Promise<void> {
  const args = parseCliArgs(process.argv.slice(2));

  if (args.jobType === "extract_v2" && PARSE_PRESETS.has(args.configId)) {
    throw new Error(
      `extract_v2 requires a saved Extract configuration ID, not the preset '${args.configId}'.`,
    );
  }

  if (args.dryRun) {
    if (args.inspect) {
      console.log(`[dry-run] would inspect batch ${args.inspect}`);
    } else {
      const files = listFiles(args.sourceDir, args.pattern);
      console.log(`[dry-run] would upload ${files.length} file(s) from ${args.sourceDir}:`);
      for (const f of files) console.log(`  - ${path.basename(f)}`);
      console.log(`[dry-run] job_type=${args.jobType} config_id=${args.configId}`);
    }
    return;
  }

  if (!process.env.LLAMA_CLOUD_API_KEY) {
    throw new Error(
      "LLAMA_CLOUD_API_KEY is not set. Export it or add it to .env (see .env.example).",
    );
  }

  const client = new LlamaCloud(); // reads LLAMA_CLOUD_API_KEY from the environment
  const rc = args.inspect
    ? await (async () => {
        const batch = await client.batches.get(args.inspect!);
        log(`Batch ${batch.id}: ${batch.status}`);
        return reportResults(client, args.inspect!);
      })()
    : await createAndRun(client, args);
  process.exitCode = rc;
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});

