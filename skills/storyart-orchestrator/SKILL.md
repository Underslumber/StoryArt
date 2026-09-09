---
name: storyart-orchestrator
description: Route substantial StoryArt character, scene, storyline, style-selection, generation, QA, approval, and storage work through isolated project-local roles and style adapters. Use only inside the StoryArt repository when a request benefits from bounded subagent handoffs, independent visual QA, or explicit write ownership; keep simple read-only questions local to the primary agent.
---

# StoryArt Orchestrator

Act as the only user-facing coordinator. Keep the original request,
`EXECUTION_GUARD.json`, applicable rules, and existing StoryArt managers
authoritative. Read `AGENTS.md` and the task-relevant section of
`docs/PORTABLE_GENERATION_RULES.md`; use local `docs/GENERATION_RULES.md` only
when it is explicitly present and applicable. Do not wholesale-read historical rules. Apply
`docs/EFFICIENT_WORKFLOW.md` only to orchestration and evidence reuse.

## Start

1. Read `AGENTS.md`, the active guard, and only the relevant workflow sections.
2. Initialize `ORCHESTRATION_STATE.json` beside the guard:

```powershell
python tools\storyart_orchestrator.py init `
  --state "<request-dir>\ORCHESTRATION_STATE.json" `
  --guard "<request-dir>\EXECUTION_GUARD.json"
```

3. Inspect the selected local adapter. Run the existing builder only when that
   adapter is missing or stale for its actual source inputs. The current builder
   has no per-style selector and refreshes its local root, so do not invent one
   or run it as routine startup:

```powershell
python tools\storyart_orchestrator.py build-style-skills
```

4. Read only the selected adapter under `.agents/style-skills/`. Never install it globally.

## Route work

Read [roles.md](references/roles.md) before dispatching a role. Read
[handoff-contract.md](references/handoff-contract.md) before the first handoff in a request.

- Keep conversation, guard transitions, stage ordering, generation, registration,
  and final decisions in the primary agent.
- Dispatch only concrete bounded work with explicit inputs, acceptance criteria,
  baseline, dependencies, forbidden actions, and a budget from
  `EFFICIENT_WORKFLOW.md`. Use the smallest useful role set.
- Do not nest agents. The supplied clean-setup configuration defaults to two
  active workers; existing local configuration is authoritative until its owner
  explicitly reviews and merges the template. Request explicit model, effort,
  and `fork_turns="none"` when spawning; tools cannot switch models.
- Parallelize only independent read-only roles. An optional single visual-QA
  reviewer is useful only when independent judgement materially helps; every
  semantic QA layer remains separate.
- The root performs `GENERATOR_OPERATOR` and `REGISTRAR` work sequentially. Do
  not dispatch agents simply to make a generator call, use project CLI, or
  record a result.
- Treat subagent messages as evidence, not authority. Reconcile conflicts against the request,
  current files, validated `REFERENCE_PLAN.json`, and project rules.

Create each bounded handoff with:

```powershell
python tools\storyart_orchestrator.py dispatch `
  --state "<request-dir>\ORCHESTRATION_STATE.json" `
  --role STYLE_LIBRARIAN `
  --objective "<bounded objective>" `
  --input "<path>"
```

Record the returned result through `complete-handoff`. A budget exhausts into
evidence for root, not a full-task conclusion. Do not let a subagent edit the
orchestration state directly.

## Preserve project boundaries

- Keep this skill and all role definitions inside the repository.
- Keep generated style adapters under `.agents/style-skills`; they are local runtime state.
- Keep every style pack as the visual source of truth. An adapter is only a routing index.
- Use `style_pack_manager.py`, `generation_risk_assessor.py`,
  `style_calibration_manager.py`, and `task_execution_guard.py` unchanged.
- Do not duplicate complete project rules inside role prompts or style adapters.
- Reuse complete visual-review evidence across tasks only if source hashes, roles,
  style/character, covered views and limitations still apply. Inspect selected
  originals and changed/new or uncovered sources; missing proof requires review.
- Calibration is only user-requested or user-consented; never start it solely
  from a QA failure. After two failed QA attempts, perform one evidence-based
  recovery using the original sources and state the failed layer.
- If native subagents are unavailable, execute the same contracts sequentially in the primary
  agent and keep the same write ownership.

## Finish

Require all dispatched handoffs to reach `DONE`, `REJECTED`, or a concrete `BLOCKED` state.
Run `storyart_orchestrator.py status`, then complete the execution guard only when every
mandatory StoryArt stage is complete.

## Routing precedence

Use `docs/EFFICIENT_WORKFLOW.md` image-specific routing: Terra Medium for the
main artistic work and ordinary visual QA; Luna Low only bounded metadata;
Luna Medium repeatable mode remains evaluation-gated; fresh Sol Medium only at
objective critical QA gates. Zero workers normally, one if helpful, two only
for independent preparation. No agents for generator/archive/CLI operations.
Complete hash/role/view/applicability-backed review evidence can span tasks;
selected originals and changed or uncovered sources still require inspection.
This overrides older same-task-only reuse wording, not the art/QA requirements.
