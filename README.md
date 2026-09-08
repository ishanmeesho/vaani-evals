# vaani-evals

A daily quality measurement for **Vaani**, the Hindi voice assistant in the
Meesho app, built on real production conversations rather than a fixed test set.

Each run pulls a fresh random 1% of a day's Vaani conversations from Metabase,
screens all of them with deterministic checks, has **Sonnet judge the long ones**
against a rubric, scores a composite, and looks for failure shapes the rubric
does not yet cover — which is how the rubric grows.

## Layout

| path | what |
|---|---|
| `rubric.yaml` | The 27 dimensions. Single source of truth: weights, severities, real production examples, and how each is checked. |
| `prompts/judge_system.md` | Judge system prompt — Vaani's shipping contract, grading discipline, and calibration on real cases. |
| `prompts/judge_batch.md` | Whole-conversation judge prompt, all dimensions at once, with the verdict JSON schema. |
| `prompts/langfuse/` | One single-criterion prompt per dimension, mustache-templated for Langfuse, plus `prompts.json` for the API. Generated — do not hand-edit. |
| `eval_set/cases.yaml` | 20 real production conversations with adjudicated verdicts. Regression set, and judge calibration set. |
| `autochecks.py` | Deterministic checks over 100% of the sample. Counts countable things; never judges. |
| `daily_pass.py` | The orchestrator: `sample` → `select` → (judge) → `aggregate` → `report`. |
| `gen_langfuse_prompts.py` | Regenerates `prompts/langfuse/` from `rubric.yaml`. |
| `push_langfuse.py` | Pushes prompts and per-dimension scores to Langfuse. |
| `runbook.md` | **The daily hour, step by step.** Start here. |
| `reports/<date>/` | Per-run sample, autochecks, judge batches, verdicts, aggregate, report, Slack text. |
| `reports/history.jsonl` | One row per run — what the dashboard plots. |

## Run it

```bash
# Unattended? Set MB_AUTH_MODE=api_key and MB_API_KEY instead — a Metabase
# Personal API Key does not expire, and a session cookie does, daily.
export MB_COOKIE='<fresh metabase.SESSION>'
RUN=$(date +%F)
python3 daily_pass.py sample --cookie "$MB_COOKIE" --run $RUN
python3 daily_pass.py select --run $RUN
#   ... judge reports/$RUN/batches/*.md -> reports/$RUN/verdicts.json
python3 daily_pass.py aggregate --run $RUN
python3 daily_pass.py report    --run $RUN
```

`runbook.md` has the full procedure, including how to check the judge against
the golden set before believing a change in the score.

## Two things this harness deliberately does not do

**It does not use a trained classifier for anything requiring judgment.** The
deterministic layer counts a `?`, a repeated query string, a forbidden English
word — facts, not opinions. Everything about grounding, carryover, persona and
intent goes to a model. A classifier standing in for a judge on these
dimensions produces a number that moves for reasons nobody can explain.

**It does not sample the judge set randomly.** 44% of a random 1% is
single-turn, and a single-turn conversation cannot exhibit the failures that
matter. The judge set is long-conversation-weighted on purpose, and the report
labels those numbers as such. Prevalence claims come from the deterministic
layer, which does cover 100% of the sample.

## Where the numbers come from

`gold.va_user_session_activity`, column `bk_turns_json`, one row per app session.
`toolkit/` handles auth and pulls; `AGENTS.md` at the repo root is the operating
guide for that, including the platform limits worth planning around.
