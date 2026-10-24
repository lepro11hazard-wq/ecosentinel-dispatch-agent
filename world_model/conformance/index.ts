// Thin TypeScript adapter over the Python World Model (world_model/core.py),
// so the Meterless conformance runner can test it:
//
//   WORLD_MODEL_IMPL=<abs path to this file> npx tsx conformance/runner.ts
//
// No logic lives here. Every call runs `python -m world_model.bridge` once and
// passes one JSON request. Each WorldModel instance keeps its event log in its
// own temp file, which Python replays on every call. Set WORLD_MODEL_PYTHON to
// choose the interpreter (default: "python" on Windows, "python3" elsewhere).

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const python = process.env.WORLD_MODEL_PYTHON ?? (process.platform === "win32" ? "python" : "python3");
const stateFiles: string[] = [];
process.on("exit", () => stateFiles.forEach((f) => fs.rmSync(f, { force: true })));

function call(request: Record<string, unknown>): any {
  const proc = spawnSync(python, ["-m", "world_model.bridge"], {
    cwd: repoRoot,
    input: JSON.stringify(request),
    encoding: "utf-8",
    env: { ...process.env, PYTHONIOENCODING: "utf-8" },
  });
  if (proc.error) throw new Error(`could not start ${python}: ${proc.error.message}`);
  let reply: { ok: boolean; result?: unknown; error?: string };
  try {
    reply = JSON.parse(proc.stdout);
  } catch {
    throw new Error(`world_model.bridge gave no JSON reply (exit ${proc.status}): ${proc.stderr}`);
  }
  if (!reply.ok) throw new Error(reply.error);
  return reply.result;
}

// Dates in provenance become ISO strings, the form Python stores.
function jsonable(input: unknown): unknown {
  return JSON.parse(JSON.stringify(input ?? {}, (_k, v) => (v instanceof Date ? v.toISOString() : v)));
}

export const normalizeName = (name: string): string => call({ op: "normalizeName", args: [name] });
export const entityIdFromName = (type: string, name: string): string => call({ op: "entityIdFromName", args: [type, name] });
export const decide = (similarity: number): "auto-merge" | "review" | "new" => call({ op: "decide", args: [similarity] });

export class WorldModel {
  private namespace: string;
  private state: string;

  constructor(opts: { namespace: string; storage?: string; clock?: () => Date }) {
    if (!opts?.namespace) throw new Error("WorldModel: namespace is required");
    this.namespace = opts.namespace;
    this.state = path.join(os.tmpdir(), `world-model-${opts.namespace}-${process.pid}-${stateFiles.length}.json`);
    stateFiles.push(this.state);
  }

  private op(op: string, args?: unknown): any {
    return call({ op, args: jsonable(args), namespace: this.namespace, state: this.state });
  }

  private async write(op: string, args: unknown): Promise<any> {
    return this.op(op, args);
  }

  upsertEntity(input: unknown) { return this.write("upsertEntity", input); }
  upsertContext(input: unknown) { return this.write("upsertContext", input); }
  relate(input: unknown) { return this.write("relate", input); }
  assertFact(input: unknown) { return this.write("assertFact", input); }
  ingest(observations: unknown[]) { return this.write("ingest", { observations }); }

  view(name: string) {
    if (name !== "graph") throw new Error(`World Model adapter: view "${name}" is not exposed`);
    return {
      neighborsOf: (id: string, opts?: { via?: string; at?: string }) => this.op("neighborsOf", { id, ...opts }),
    };
  }

  audit(): { seq: number }[] { return this.op("audit"); }
  stateShapeCanonical(): string { return this.op("stateShapeCanonical"); }
}
