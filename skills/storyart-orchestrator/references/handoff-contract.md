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
- `ESCALATION_ORCHESTRATOR` uses the registry's Sol 6.1 High profile for a
  bounded exceptional diagnosis. It has no write path and may not use tools,
  generate, test, edit, QA, approve, or spawn. Its only output is one bounded
  Sol work order; root dispatches it. Require evidence of a complex fault or
  substantive repair failure; an earlier Luna attempt is not a prerequisite.
- `GENERATOR_OPERATOR` and `REGISTRAR` are root-held logical responsibilities,
  not dispatchable subagent roles in ordinary production. The root applies their
  active-request, `GENERATION_RESULTS`, and approved-destination write limits.
- No role may edit `tools`, `tests`, `docs`, `skills`, `scripts`, `.agents`, or project policy
  during an image request.
- A handoff cannot start until every listed dependency is `DONE`.
- A subagent never edits `ORCHESTRATION_STATE.json` or its handoff file.

## Native subagent use

Explicit Web execution is the scoped exception to root-only generator roles:
`WEB_IMAGE` uses the project `web_image` agent. Create it with
`chatgpt_web_image_handoff create` from the prepared production journal and
complete final-spec file. The manager verifies the immutable packet, current
attempt, stage, project and every listed input before dispatch. Its write scope
is only the active request folder. It executes/downloads; root keeps source
authority, QA, delivery and COMPLETE. See `docs/CHATGPT_WEB_IMAGE.md`.

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

`DONE` requires at least one existing evidence file relevant to the handoff;
the manager records its project-relative path and SHA-256. An empty list, folder,
missing path, or unsupported verbal-only claim cannot satisfy completion.

Use `REJECTED` for a completed assessment that rejects a candidate or output.
Use `BLOCKED` only for a concrete condition the assigned role cannot resolve.
On packet budget exhaustion, return the collected evidence to root and do not
claim the whole task is complete. The packet ceiling is not a cap on the user's
task; root follows `REQUEST_FIDELITY.md` and continues or escalates diagnosis.

## Routing precedence

Use `config/model_routes.json` as the role authority and
`docs/MODEL_PROFILES.md` for activation. Root retains its selected model and the
user contract. Ordinary discovery, implementation, planning and result checks
use Luna 6 High in default/worker/explorer with fork_turns="none". Mandatory
read-only code review after machine checks uses sol_review, Sol 6.1 Medium;
evidenced Luna failure permits sol_escalation, Sol 6.1 High. Record SOL_GATE
before either Sol dispatch. Specialized Web roles retain their authorized
contracts and never replace ordinary workers. Normally zero workers,
one when useful, at most two independent workers. No full-history forks,
nesting, or agents merely for generator/archive/CLI operations.
Reuse source evidence only when hash, role, view, applicability and limitations
match; inspect changed or uncovered sources. Packet ceilings return evidence
to root and never terminate the user's image goal. This changes orchestration,
not artistic, identity, safety or QA requirements.
