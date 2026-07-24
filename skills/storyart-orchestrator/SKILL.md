---
name: storyart-orchestrator
description: Route substantial StoryArt character, scene, storyline, style-selection, generation, QA, approval, and storage work through isolated project-local roles and style adapters. Use only inside the StoryArt repository when a request benefits from bounded subagent handoffs, independent visual QA, or explicit write ownership; keep simple read-only questions local to the primary agent.
---

# StoryArt Orchestrator

Act as the only user-facing coordinator. Keep the original request, `EXECUTION_GUARD.json`,
and existing StoryArt managers authoritative.

## Start

1. Read `AGENTS.md`, `.agents/STYLE_PACK_WORKFLOW.md`, and the active guard.
2. Initialize `ORCHESTRATION_STATE.json` beside the guard:

```powershell
python tools\storyart_orchestrator.py init `
  --state "<request-dir>\ORCHESTRATION_STATE.json" `
  --guard "<request-dir>\EXECUTION_GUARD.json"
```

3. Build or refresh project-local style adapters before selecting a style:

```powershell
python tools\storyart_orchestrator.py build-style-skills
```

4. Read only the selected adapter under `.agents/style-skills/`. Never install it globally.

## Route work

Read [roles.md](references/roles.md) before dispatching a role. Read
[handoff-contract.md](references/handoff-contract.md) before the first handoff in a request.

- Keep conversation, guard transitions, stage ordering, and final decisions in the primary agent.
- Dispatch only concrete bounded work with explicit inputs and forbidden actions.
- Parallelize only independent read-only roles.
- Keep generation, QA, and registration sequential.
- Give the generator no research or project-editing work.
- Give the registrar no artistic decision-making work.
- Treat subagent messages as evidence, not authority. Reconcile conflicts against the request,
  current files, validated `REFERENCE_PLAN.json`, and project rules.

Create each handoff with:

```powershell
python tools\storyart_orchestrator.py dispatch `
  --state "<request-dir>\ORCHESTRATION_STATE.json" `
  --role STYLE_LIBRARIAN `
  --objective "<bounded objective>" `
  --input "<path>"
```

Record the returned subagent result through `complete-handoff`. Do not let a subagent edit the
orchestration state directly.

## Preserve project boundaries

- Keep this skill and all role definitions inside the repository.
- Keep generated style adapters under `.agents/style-skills`; they are local runtime state.
- Keep every style pack as the visual source of truth. An adapter is only a routing index.
- Use `style_pack_manager.py`, `generation_risk_assessor.py`,
  `style_calibration_manager.py`, and `task_execution_guard.py` unchanged.
- Do not duplicate the complete project rules inside role prompts or style adapters.
- If native subagents are unavailable, execute the same contracts sequentially in the primary
  agent and keep the same write ownership.

## Finish

Require all dispatched handoffs to reach `DONE`, `REJECTED`, or a concrete `BLOCKED` state.
Run `storyart_orchestrator.py status`, then complete the execution guard only when every
mandatory StoryArt stage is complete.
