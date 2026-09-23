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

## Prepare and record image calls

- Before the first style/profile question, check for a complete confirmation
  in the current chat. If unresolved, make a bounded local metadata inventory
  before asking: enumerate `*_PROJECT_PACK` and `*_GENERATIONS` directories,
  including git-ignored data, and inspect relevant `CHARACTER_REGISTRY.csv`
  files for a character named in the request. Do not broadly search image
  files or treat `rg --files` as proof that ignored data is absent. Present
  unambiguous local style/approved-character candidates and any
  `REVIEW_REQUIRED` status; inventory does not select or finalize them and is
  not consent. Then actually ask the contextual style/profile question with
  the identified style plus recommended 90% fidelity and
  BODY_REFERENCE_LIBRARY first, two task-fit alternatives, and a
  custom/different-style choice. Wait for the answer, ask only for a missing
  field in a partial profile, and do not request references already present
  locally. A reply such as `1` selects every parameter bundled into option 1;
  treat that menu choice as complete profile confirmation and never ask the
  user to confirm its fidelity or BODY_REFERENCE_LIBRARY decision again.
  Persist the selected option and its exact resolved parameters. Never infer
  the user's style, character, or profile choice.
- Before preparing a plan, check whether the current chat explicitly confirms
  both fidelity and BODY_REFERENCE_LIBRARY use. A "yes" to the style name is
  insufficient. If the profile is missing, actually present the startup
  question specified in `docs/GENERATION_RISK_SYSTEM.md`: recommended
  90% plus BODY_REFERENCE_LIBRARY first, two contextual alternatives and a
  custom answer. Use an available permitted question UI or a visible numbered
  text fallback, then wait for the answer. Never merely report that you asked.
  Once a numbered option is selected, reuse its complete same-chat profile;
  do not reconfirm the fidelity or library decision included in that option.
  Ask only the missing field for a partially confirmed profile. Do not infer a
  library decision from NOT_SELECTED.
  Before asking about a missing mutable scene detail such as clothing, inspect
  the relevant approved profile and local pending/approved storyline plan or
  prompt for a concrete candidate. Name its approval state and offer it as a
  one-scene option; pending material requires the user to choose it and must
  never be treated as canonical wardrobe or attached as approved continuity.
  Do not ask the user to restate details already recorded in those sources.
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
