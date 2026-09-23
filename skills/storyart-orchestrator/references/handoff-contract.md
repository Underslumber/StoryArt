# Handoff contract

Every handoff is created by `tools/storyart_orchestrator.py` and stored under
`<request-dir>/ORCHESTRATION_HANDOFFS/`.

## Required inputs

- one role;
- one bounded objective;
- explicit input paths;
- dependencies on earlier handoffs when applicable;
- a stage for generation-related work;
- explicit allowed write paths for any dispatched writer role; in ordinary
  production, `GENERATOR_OPERATOR` and `REGISTRAR` are root-only and never
  receive a handoff.
- acceptance criteria, baseline, forbidden scope, and a finite call/time/word
  budget from `docs/EFFICIENT_WORKFLOW.md`.

## Isolation rules

- Read-only roles receive no write path.
- `ESCALATION_ORCHESTRATOR` is a one-shot `gpt-6-astra` Low exceptional error
  handler. It has no write path and may not use tools, generate, test, edit,
  QA, approve, or spawn. Its only output is one bounded Luna/Sol work order;
  the root dispatches it under the existing no-nesting invariant. Sol High
  requires evidence of a substantive Luna failure for either complex
  implementation or repair; complexity alone does not qualify.
- `GENERATOR_OPERATOR` and `REGISTRAR` are root-held logical responsibilities,
  not dispatchable subagent roles in ordinary production. The root applies their
  active-request, `GENERATION_RESULTS`, and approved-destination write limits.
- No role may edit `tools`, `tests`, `docs`, `skills`, `scripts`, `.agents`, or project policy
  during an image request.
- A handoff cannot start until every listed dependency is `DONE`.
- A subagent never edits `ORCHESTRATION_STATE.json` or its handoff file.

## Native subagent use

Pass the generated handoff object or its `agent_prompt` to one native subagent.
Request an explicit model, effort, and `fork_turns="none"`; this document does
not switch a model itself. Give only task-local context and listed files, never
the user conversation or an intended conclusion. No nesting. The configured
normal cap is two active workers; more requires an explicit bounded config
change and confirmed runtime capacity.

When it returns, record the result:

```powershell
python tools\storyart_orchestrator.py complete-handoff `
  --state "<request-dir>\ORCHESTRATION_STATE.json" `
  --handoff-id "<id>" `
  --status DONE `
  --result "<concise result>" `
  --evidence "<path>"
```

Use `REJECTED` for a completed assessment that rejects a candidate or output.
Use `BLOCKED` only for a concrete condition the assigned role cannot resolve.
On budget exhaustion, return the collected evidence to root and do not claim the
whole task is complete. The root retains the two-repair-loop limit and decides
whether a recovery is justified.

## Routing precedence

Use `docs/EFFICIENT_WORKFLOW.md` routing: Luna Medium for ordinary image work;
Sol Low for planning/integration and fresh objective review; Luna High for
discovery and ordinary code implementation. Sol High requires evidence of a
substantive Luna failure for either complex implementation or repair; complexity
alone does not qualify. Astra Low remains
exceptional, read-only escalation only. Zero workers normally, one if helpful, two only
for independent preparation. No agents for generator/archive/CLI operations.
Complete hash/role/view/applicability-backed review evidence can span tasks;
selected originals and changed or uncovered sources still require inspection.
This overrides older same-task-only reuse wording, not the art/QA requirements.
