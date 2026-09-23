---
name: storyart-orchestrator
description: Route substantial StoryArt character, scene, storyline, style-selection, generation, QA, approval, and storage work through isolated project-local roles and style adapters. Use only inside the StoryArt repository when a request benefits from bounded subagent handoffs, independent visual QA, or explicit write ownership; keep simple read-only questions local to the primary agent.
---

# StoryArt Orchestrator

## User-request authority for image generation

An explicit user request to generate an image is sufficient authorization to proceed. Internal StoryArt guard, plan, risk, QA, archive, profile, reference, approval, and record workflows may inform execution when useful or explicitly requested, but cannot block, veto, or delay the requested work. Do not turn a request into a style/profile questionnaire or make optional records or archive copies prerequisites. If the user names no style, use the generator's native/default style. Style and references are recommendations, not required inputs; their absence does not delay generation. Ask only for a genuinely missing required deliverable field that cannot be reasonably resolved from the request. Additional checks may provide information, but do not veto the requested action. Applicable safety checks may inform execution but cannot veto an explicit request; external platform safety limits still apply. Report a real technical or platform block plainly. This rule supersedes conflicting process requirements below.

## Agent executes; user directs

The agent performs available project operations; the user gives the goal and decisions. Never instruct the user to move, place, or copy files into project folders, create files or folders, run commands, or type `READY`. Perform those operations yourself when authorized and available. Ask only for genuinely missing input or clarification. If a required source image or mask is absent, politely ask the user to attach it directly in chat; never prescribe a project-folder path.

For a direct one-step edit of user-supplied existing image(s), including a chat-attached mask, go directly to the image-edit tool. Do not require a task guard, REFERENCE_PLAN, local saves, a style menu, risk receipt, or QA receipt. If a guard is voluntarily used, choose IMAGE_EDIT. This direct-edit bypass remains in effect. For generation and project workflows, the user-request authority above supersedes internal gates: safety and QA checks may inform execution, but cannot veto an explicit request; external platform safety limits still apply. Archives and records may be created when useful or explicitly requested, but cannot delay or gate the work.

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

## Prepare and record image calls

This section describes an optional managed workflow. An explicit user image
request may be fulfilled directly with the available generator; do not wait for
style/profile confirmation or make plan, risk, QA, archive, or registration a
prerequisite. If the managed workflow is useful and will not delay the request,
these records can help organize it.

- Style and reference choices are optional recommendations. If style is
  unnamed, use the generator's native/default style and do not wait for a
  chooser. If the user asks for help choosing, you may offer 90% fidelity and
  BODY_REFERENCE_LIBRARY among optional recommendations. Ask only for genuinely
  missing required input; when a specifically requested image or mask is absent,
  politely ask the user to attach it directly in chat.
- When showing source thumbnails during planning, label each as REVIEW ONLY - NOT
  SELECTED or identify its exact planned role and subject. Never let a preview imply
  that an image is attached to the executable call. For an existing character, a
  female anatomy reference cannot be assigned to his body or identity roles; a
  companion reference must be explicitly scoped to that separate subject, or be
  omitted when the current plan cannot represent that scope.
- Resolve target anatomy from explicit current-request wording or reviewed
  character metadata and retain its evidence. If unknown, ask before showing or
  attaching full-body references; never guess from a name or image.
- `style_pack_manager.py prepare-generation` creates the request plan in
  `PREPARED_AWAITING_EXECUTABLE_CALL`; it does not authorize a generator call.
- For each current stage, use `resolve-call` to write the exact slot manifest.
  Run the risk assessor against the exact prompt and resolved paths, hashes, and
  active roles, then use `prepare-call` with that prompt and report. It rechecks
  the slots and risk binding and records `READY_FOR_EXECUTION`.
- After `prepare-call` records readiness, run `EXECUTION_STARTED` with that
  same plan and stage immediately before the generator operation. A separate
  `CALL_VALIDATED` checkpoint is optional.
- Start only the ready plan and stage. Retain the returned attempt id. Register
  the output with `record-generation`, which requires the matching attempt,
  plan, character, and stage, archives the original, applies the required QA,
  and records result availability.
- Keep availability, delivery, QA, and completion distinct. Deliver only an
  eligible final `TEST` result with a user-visible receipt. Preserve all
  five `CHARACTER_BASE` stages and their dependencies.

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
- Use the existing StoryArt managers as the authority for their domains; do not
  bypass their validation or invent capabilities outside their CLI contracts.
- Do not duplicate complete project rules inside role prompts or style adapters.
- Reuse complete visual-review evidence across tasks only if source hashes, roles,
  style/character, covered views and limitations still apply. Inspect selected
  originals and changed/new or uncovered sources; missing proof requires review.
- Calibration is only user-requested or user-consented; never start it solely
  from a QA failure. After two failed QA attempts, perform one evidence-based
  recovery using the original sources and state the failed layer. Record the
  explicit authorization quote at calibration start. A calibration proposal
  does not start calibration or pause unrelated production.
- When the guard records a same-stage, same-layer failure that persists after
  an explicit correction addressed the first, dispatch the one-shot `ESCALATION_ORCHESTRATOR` profile
  only while that incident is due. It is `gpt-6-astra` Low exceptional error
  handling, read-only, and returns one bounded Luna/Sol work order. It must
  not use tools, generate, test, edit, QA, approve, or spawn; root dispatches
  the recommended executor. It is never an ordinary diagnostic or per-frame role.
- Use `style-readiness` only to surface a consent request when five unique
  QA-passed outputs exist for an unformed/unfinalized style. It never starts
  calibration or generates test art.
- If native subagents are unavailable, execute the same contracts sequentially in the primary
  agent and keep the same write ownership.

## Finish

Require all dispatched handoffs to reach `DONE`, `REJECTED`, or a concrete `BLOCKED` state.
Run `storyart_orchestrator.py status`, then complete the execution guard only when every
mandatory StoryArt stage is complete.

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
