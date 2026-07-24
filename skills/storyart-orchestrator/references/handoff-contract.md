# Handoff contract

Every handoff is created by `tools/storyart_orchestrator.py` and stored under
`<request-dir>/ORCHESTRATION_HANDOFFS/`.

## Required inputs

- one role;
- one bounded objective;
- explicit input paths;
- dependencies on earlier handoffs when applicable;
- a stage for generation-related work;
- explicit allowed write paths for writer roles.

## Isolation rules

- Read-only roles receive no write path.
- `GENERATOR_OPERATOR` may write only inside the active request and `GENERATION_RESULTS`.
- `REGISTRAR` may write only inside the active request, `GENERATION_RESULTS`, or the explicit
  approved destination.
- No role may edit `tools`, `tests`, `docs`, `skills`, `scripts`, `.agents`, or project policy
  during an image request.
- A handoff cannot start until every listed dependency is `DONE`.
- A subagent never edits `ORCHESTRATION_STATE.json` or its handoff file.

## Native subagent use

Pass the generated handoff object or its `agent_prompt` to one native subagent. Give it only the
task-local context and files listed in the handoff. Do not pass intended conclusions.

When it returns, record the result:

```powershell
python tools\storyart_orchestrator.py complete-handoff `
  --state "<request-dir>\ORCHESTRATION_STATE.json" `
  --handoff-id "<id>" `
  --status DONE `
  --result "<concise result>" `
  --evidence "<path>"
```

Use `REJECTED` for a completed assessment that rejects a candidate or output. Use `BLOCKED`
only for a concrete condition the assigned role cannot resolve within its contract.
