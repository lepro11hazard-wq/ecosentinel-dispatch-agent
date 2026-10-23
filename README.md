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
