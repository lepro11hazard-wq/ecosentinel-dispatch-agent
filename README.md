# EcoSentinel Dispatch Agent

Plans daily household waste-collection routes in Buea, Cameroon for EcoSentinel Technologies (EcoLoop).
Open Agent Hackathon 2026, Track 4: Reasoning Architecture.

> **Data notice:** `SAMPLE_MODE=true` (default) uses **simulated data only**. Zone names (Molyko, Bonduma,
> Great Soppo) are real; households, volumes and dump reports are invented. No live production data is used.

## Run

```
python run.py
```

Standard library only, so there is nothing to install. It finishes in seconds in sample mode, and the run budget is capped at 8 minutes in live mode.
Output: `outputs/last_run.json` (inputs, every plan version, evaluator critiques, risk decisions, full reasoning trace).

## Architecture

```
Planner (sequences + routes feedback, never approves)
  -> Verification   photo check (vision model) + location/time duplicate check
  -> Dispatch       zone-first draft, straight-line nearest-neighbour ordering
  <-> Evaluator     rejects over-capacity / unverified reports / ignored zones; up to 3 revisions
  -> Risk           only fully-supported assignments auto-approved; the rest go to human review
```

| Agent | Decides | File |
|---|---|---|
| Planner | Which subtasks run, where feedback goes | `agents/planner.py` |
| Verification | verified / duplicate / needs_review / rejected per report | `agents/verification.py` |
| Dispatch | Who collects what, stop order | `agents/dispatch.py` |
| Evaluator | Whether a draft is acceptable, and why not | `agents/evaluator.py` |
| Risk | auto_approved vs human_review | `agents/risk.py` |

Thresholds are in `config.py`.

## Meterless engine: World Model

**Engine:** [Meterless World Model](https://github.com/Meterless/Meterless/tree/main/engines/world-model), ported to Python (standard library only).

**What it does here:** it gives the five agents one shared, event-sourced state per run, instead of each agent passing dicts to the next.
Every write is an appended event with provenance (which agent wrote it, and when), and the current state is a fold over that log.

- **Planner** seeds the world with zones, collectors and pickups. Dump reports go in through the engine's idempotent `ingest`.
- **Verification** records one `verification` fact per report.
- **Dispatch** writes an `assigned-to` edge for every stop in every plan version. When a revision moves a stop, the old edge is closed
  (`validTo` and `supersededBy`) rather than deleted, so the world holds the full reassignment history.
- **Evaluator** reads the verified reports from the world rather than receiving them, and records its verdict per version.
- **Risk** reads held reports and each stop's current collector from the world. If the plan and the world disagree, that stop
  goes to human review instead of being auto-approved. It then records each approval decision.

At the end of a run the world is replayed from its event log and checked against the live state. The result is the `world_model`
block in `outputs/last_run.json`, and the full event log and audit trail are saved to `outputs/world_model.json`.

**Where it is:**

| Path | What |
|---|---|
| `world_model/core.py` | The engine: event log, stable IDs, merge bands, provenance, bitemporal edges, idempotent ingest, audit, replay |
| `shared_world.py` | EcoLoop's use of it: what each agent writes and reads |
| `world_model/conformance/index.ts` | Thin adapter so the Meterless TypeScript conformance runner can drive the Python engine (no logic in it) |
| `world_model/bridge.py` | The JSON bridge that adapter calls |

Not ported, because this pipeline doesn't need them: story clustering and velocity scoring, operator merge approval, and the control-plane UI.

**How to test it:**

```
# Meterless conformance suite (needs Node.js 18+; 8/8 checks pass)
git clone https://github.com/Meterless/Meterless
cd Meterless/engines/world-model
WORLD_MODEL_IMPL=/abs/path/to/ecosentinel-dispatch-agent/world_model/conformance/index.ts npx tsx conformance/runner.ts
```

On Windows PowerShell, set the variable first with `$env:WORLD_MODEL_IMPL="E:/path/to/ecosentinel-dispatch-agent/world_model/conformance/index.ts"`,
using forward slashes. The adapter runs `python`, or `python3` on macOS/Linux; set `WORLD_MODEL_PYTHON` to choose another interpreter.

```
# Engine contracts and pipeline integration (needs pytest: pip install pytest)
python -m pytest tests
```

## Live mode

Set `SAMPLE_MODE=false` in `.env` with `NVIDIA_API_KEY`, and put report photos at `data/photos/DR-1.jpg` and so on, each under ~130 KB.
Each photo goes to NVIDIA NIM `meta/llama-3.2-11b-vision-instruct` and the reply is parsed into
`{confidence, duplicate, reason}`. If a call fails or a photo is missing, the report is held for human review rather than rejected.

When the Evaluator rejects a draft, it also logs an `explain vN` trace entry. That entry is a plain-language reason with a suggested
fix, written by the same NIM model whenever `NVIDIA_API_KEY` is set (turn it off with `EVALUATOR_LLM_EXPLAIN=false`). Without a key, or if the call
fails, a rule-based template is used. The explanation is generated **after** the rule-based verdict and never changes pass/fail.

## Stated scope limits

- **Routing uses straight-line (haversine) distance** with a greedy nearest-neighbour order, not road distance or
  travel time. Buea's hills and roads are not modelled. This is a deliberate scope decision, not an error.
- Capacity is checked by weight only (no volume, time windows or shift length).
- Duplicates are detected by location (75 m) and time (48 h) plus the model's flag, not pixel comparison.
