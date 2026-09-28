# Role contracts

Use the smallest set of roles needed. The primary agent remains the coordinator
and never delegates user communication, scope changes, guard completion,
generation, or registration. This is a role policy, not a scheduler: the root
requests explicit model/effort when spawning, and tools cannot change models.

The configured normal hard cap is two active workers. Use more only after an
explicit bounded configuration change and confirmed runtime capacity. No
nesting. Every worker gets a bounded packet with objective, inputs, baseline,
dependencies, acceptance criteria, forbidden scope, and the matching
call/time/word budget from `docs/EFFICIENT_WORKFLOW.md`.

## STYLE_LIBRARIAN

- Start from the written style profile and existing matrix. Resolve only missing
  source locations; reuse the request's existing context.
- Inspect selected originals for required roles, or reuse equivalent recorded
  evidence. Stop searching once the required roles have suitable sources.
- Return the minimal compatible selected set; no rejected-alternatives report
  is needed unless a concrete conflict explains the choice.
- Remain read-only. Do not create prompts, call the generator, or modify manifests.

## IDENTITY_CURATOR

- Use the approved profile and registry; inspect the selected assembly, face,
  and applicable body views, reusing equivalent source evidence. The registry
  validates the complete identity set; do not visually reopen unused views.
- Select the smallest authoritative identity subset for the requested shot.
- Report continuity risks and missing identity evidence.
- Remain read-only. Do not redesign the character or approve a permanent identity change.

## CALL_PLANNER

- Combine the locked goal, selected style, identity sources, requested composition, attachment
  limit, current risk report, and active `REFERENCE_PLAN.json`.
- Return one exact-call prompt and one exact physical attachment list.
- Remain read-only. Do not call the generator or broaden the request.

## GENERATOR_OPERATOR

This is a sequential root responsibility, not a normally dispatched subagent
role. Receive one already validated prompt and exact attachment list. Confirm
the matching `EXECUTION_STARTED` transition immediately before one real call.
Do not research, change the prompt, select references, edit infrastructure, or
retry independently.

Execute through the available built-in `image_gen` / `image_gen__imagegen`
with the prepared prompt and physical reference paths. StoryArt supplies the
scenario and validated inputs; no separate StoryArt generator is required.

## VISUAL_QA

- Inspect the full-resolution result and authoritative comparison sources.
- Score every required semantic QA layer independently.
- Return `PASS` or `FAIL` per layer with short visual evidence.
- Remain read-only. Do not repair, regenerate, archive, approve, or reinterpret a failed layer.
- Use at most one bounded independent reviewer and only when it materially
  improves judgement. A single reviewer still reports every required layer.

## REGISTRAR

This is a sequential root responsibility, not a normally dispatched subagent
role. Receive the generator output plus finalized QA verdict and use existing
managers to record, archive, reject, approve, or store it. Do not choose
references, change QA, call the generator, or modify infrastructure.

## ESCALATION_ORCHESTRATOR

- Available once only when the guard marks a same-stage, same-layer failure
  that persists after an explicit correction addressed the first.
- Requested profile is `gpt-6-astra` at Low effort, strictly as exceptional
  top-level error handling, not routine review or a per-frame worker.
- Consume only listed evidence and return one bounded, evidence-based Luna or
  Sol work order. The root alone dispatches that executor; no nesting. Sol High
  requires evidence of a substantive Luna failure for either complex
  implementation or repair; complexity alone does not qualify.
- Remain read-only and do not use tools, generate, test, edit, perform QA,
  approve, or spawn agents.

## Routing precedence

Use `docs/EFFICIENT_WORKFLOW.md` routing: Luna Low for ordinary image work,
deterministic discovery and straightforward implementation; Luna High only for
materially ambiguous or difficult work. Sol Low handles planning/integration
and fresh objective review. Sol High requires evidence of a
substantive Luna failure for either complex implementation or repair; complexity
alone does not qualify. Astra Low remains
exceptional, read-only escalation only. Zero workers normally, one if helpful, two only
for independent preparation. No agents for generator/archive/CLI operations.
Complete hash/role/view/applicability-backed review evidence can span tasks;
selected originals and changed or uncovered sources still require inspection.
This overrides older same-task-only reuse wording, not the art/QA requirements.
