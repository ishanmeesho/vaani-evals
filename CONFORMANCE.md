# Conformance to the commerce-agents `commerce-evals` skill

Read from `anthropics/commerce-agents`,
`plugins/commerce-builder/skills/commerce-evals/SKILL.md`, on 2026-09-08 and
applied to this harness the same day. This file records what was adopted, what
diverges on purpose, and what is still a gap — so the next person does not have
to re-derive which is which.

## Adopted

| Rule | Where it landed |
|---|---|
| The case shape — `id: <flow>-<nnn>-<behavior>`, `priority`, `difficulty`, `tags`, `skip`, `state`, `turns`, `expected`, `notes` | `eval_set/cases.yaml` rewritten to it. `state` carries the screen, products already seen, and `established` (the constraints stated so far), which is Vaani's equivalent of injected session state. |
| Preconditions go in injected state, not in earlier turns | `state.established` holds the constraint set; `turns` carries only what the behaviour itself needs across turns. |
| A rubric is one PASS and one FAIL condition no response satisfies both of, naming the fact that decides it, silent on tone, length and ordering | Every one of the 28 dimensions in `rubric.yaml` has a `rubric:` line in that shape, and every case has one in `expected.rubric`. The generated Langfuse prompts lead with it. |
| One judge call per dimension, structured output, transcript as quoted material | `prompts/langfuse/<DIM>.md` — 28 single-criterion prompts, each returning `{score, verdict, reasoning, evidence}`. |
| Pin the judge model at temperature zero | `config.model` and `config.temperature: 0` on every generated prompt payload. |
| A rubric or judge-model change invalidates stored verdicts, so the recording carries a fingerprint of both | `judge_fingerprint()` in `daily_pass.py` — sha256 over the judge model, the rubric version, and the exact bytes of `rubric.yaml`, `judge_system.md` and `judge_batch.md`. Stamped on every aggregate and every history row. The failure-set diff refuses to compare across a fingerprint change. |
| A judge reply that does not parse is a judge failure, kept apart from an agent failure | `judge_error` is its own bucket in `cmd_aggregate`, excluded from pass rates entirely. Folding it into `fail` would make a broken judge look like a worse Vaani. |
| Code graders read the events; every key except `rubric` is a code grader | `autochecks.py` reads `action_type`, `search_keyword` and the reply text. The `expected` keys in the case set are code-gradable: `calls_action`, `never_calls`, `max_searches`, `search_query_includes`/`_omits`/`_max_tokens`, `no_question`, `reply_includes`/`_omits`. |
| Grade the final tool arguments, not the route | `SEARCH_TRIGGER`, `QUERY_QUALITY`, `ATTRIBUTE_CARRYOVER` and `FALSE_ACTION_CLAIM` grade `search_keyword` and the action that fired, never the sentence describing it. `FALSE_ACTION_CLAIM` exists precisely because the sentence and the action disagreed. |
| `max_tool_calls` set from what a well-behaved agent needs | `max_searches` on the cases; `duplicate_consecutive_queries` and `over_stuffed_queries` in the deterministic layer. |
| Every positive has a negative; a refusal case has a should-serve counterpart | 10 pairs, listed in `cases.yaml` under `coverage.positive_negative_pairs`. The load-bearing ones: `safety-002` (a product-function question answered normally, so a fix for the medical failure cannot be "refuse all product questions") and `deflect-004` (pointing at Buy Now is legitimate, so a fix for deflection cannot stop Vaani naming buttons). |
| A case that cannot run yet carries `skip` with its reason rather than being deleted | `injection-001`. |
| Poisoned fixtures: listings and reviews carrying instructions | Half adopted — `INJECTION_RESISTANCE` (rubric v3) grades the detection half on live traffic. The driven half is a gap, below. |
| In production, judge a sample of live traffic against the same rubrics and trend per dimension | The whole harness. |
| Diff failure sets; a topline moving a point between runs is noise | `failure_diff()` in `daily_pass.py`, and it leads the report ahead of the score. |

## Deliberate divergences

**Wording is graded as product, not packaging.** The skill says the reply's
wording is graded only for strings that must or must not appear, because the
cart is the deliverable. Vaani is a *voice* assistant for a shopper who cannot
read the screen well: the spoken reply is the only thing most of these shoppers
receive, so it *is* the deliverable. `PERSONA`, `LANGUAGE_DISCIPLINE`,
`RESPONSE_LENGTH`, `AFFIRMATION_POLARITY` and `REASSURANCE_CORRECTNESS` grade
wording directly. `AFFIRMATION_POLARITY` is the clearest case for this:
"हाँ, ... नहीं है" has correct facts, correct tool arguments and correct
resulting state, and tells a listening shopper the opposite of the truth.

**No per-case diff across daily runs.** The skill's run pattern assumes a fixed
regression set. Ours is a fresh random 1% of a different day each run, so a
session that failed yesterday does not exist today. The daily diff is therefore
at the dimension level, and says so in the report. Per-case diffing is what
`eval_set/cases.yaml` is for — that set is fixed and replayable, and it is the
surface to diff when a prompt or config changes.

**Judge selection is not random.** 44% of a random 1% is single-turn, and a
single-turn conversation cannot exhibit carryover, looping, persona drift or a
dropped ask. The judge set is long-conversation weighted; the deterministic
layer covers 100% and is what prevalence claims come from. Both are labelled
in the report.

## Gaps, honestly

1. **Driven poisoned fixtures.** The skill wants eval-only listings under a
   third-party seller, merged into the backend for the run, whose text carries
   an instruction — plus a benign counterpart in the same niche so an agent
   that refuses everything also fails. Production sampling cannot manufacture
   these. They belong in `vaani-lab` (a `mocks.yaml` fixture plus cases), and
   `injection-001` is `skip`ped with that reason. Until then only the detection
   half is measured, and `INJECTION_RESISTANCE` will read mostly `n/a`.
2. **Several cases per trial.** The skill runs each case over several trials
   with a pass threshold per set. Every dimension here is scored once per
   conversation. Variance is therefore unmeasured, which matters most for the
   judged dimensions on a 25-conversation subset — a 4-percentage-point move on
   one dimension is one conversation. This is why the report leads with the
   failure-set diff and not the score.
3. **Positive counterparts missing** for `SELF_CONSISTENCY`,
   `FALSE_ACTION_CLAIM`, `UNACTIONABLE_ASK`, `FRUSTRATION_UNHEEDED` and
   `MODALITY_LEAK` — they have failing cases only. One each from the next pass.
4. **No unit tests for the gate behaviour** that needs no model. The skill
   unit-tests provenance, caps and guardrails with a fake client. Vaani's
   equivalents (the three filter gates: `suppress_filters`, a missing filter
   dictionary, sscat confidence below threshold) live in the service, not here,
   and are not covered by this harness at all.
