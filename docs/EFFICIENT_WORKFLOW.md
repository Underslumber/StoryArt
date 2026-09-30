# Efficient StoryArt workflow

This policy reduces repeated preparation. It does not change artistic
acceptance, safety, approval, archive, guard, stage, provenance, or per-layer
QA requirements in `PORTABLE_GENERATION_RULES.md` and applicable user instructions.
A local `GENERATION_RULES.md` is consulted only when it is explicitly present
and applicable to the task.
It cannot itself change the model of an already running session.

## Root and workers

| Work | Default assignment | Contract |
| --- | --- | --- |
| Ordinary image production and integration | Sol 6.1 Medium root (`ROOT`) | Root retains the full user contract, choices, execution, recovery and acceptance. |
| Deterministic lookups, CLI execution, cache reuse, registration | Root uses project tools directly | Reuse unchanged request-scoped results; retry only after a concrete error or changed input. |
| Bounded source discovery and ordinary code implementation | Sol 6.1 Low (`DEFAULT_WORKER`) | Optional narrow packet; no user conversation or decisions that replace the contract. |
| Complex call planning and independent review | Sol 6.1 Medium (`CALL_PLANNER` / `CODE_REVIEW` / `VISUAL_QA`) | Only when separate work or judgement materially helps; fresh review for substantial code changes. |
| Exceptional diagnosis | Sol 6.1 High (`ESCALATION_ORCHESTRATOR`) | Evidence of a complex fault or substantive repair failure; bounded diagnosis only. |

`config/model_routes.json` is the role authority; `MODEL_PROFILES.md` explains
activation and explicit experiments. This project route overrides generic model
defaults. Sol 6.1 Low root and Luna 5.6 Low mechanical workers are opt-in trials,
not automatic fallback assignments. Luna 6 is outside normal production routing.

Model and effort are requested when an agent is spawned; prose and tools cannot
switch the current model. On a clean setup, the provided configuration template
sets a capacity of two concurrent workers; normally use zero or one. Existing users review and merge that template
into their local configuration explicitly; it does not override their settings.
Do not nest agents. Spawn with explicit model, effort, and `fork_turns="none"`.

Every packet states: objective; allowed paths/actions; inputs; acceptance
criteria; baseline; dependencies; forbidden scope; and its budget. It contains
no user-conversation transcript or assumed conclusion. Reaching a budget means
returning evidence to the root, never declaring the entire user task complete.

| Packet | Default ceiling |
| --- | --- |
| Discovery | 8 calls, 6 active minutes, 700 words |
| Implementation | 12 calls, 10 active minutes, 700 words |
| Review | 6 calls, 5 active minutes, 500 words |

Retain the overall limit of two ordinary repair loops. Reset neither the count
nor the scope after a failed check; escalate the concrete evidence to root.

Project TOML defaults apply to new sessions and are not a per-task scheduler.
Use the registry's `CODE_IMPLEMENTER` for bounded implementation, `CODE_REVIEW`
for fresh substantial-code review, and the same root contract for integration.
An existing chat keeps its actual model; never claim a config edit switched it.

## Image-production route

### Minimal execution path

### Current-chat choices and active folder

The approved profile fixes the STYLE NAME only, never fidelity or BODY_REFERENCE_LIBRARY. In a new chat, show the standard Стиль и референсы chooser before source selection/preparation unless both choices were explicitly made in this chat. Do not inherit choices from another chat.

For image assets, access only approved project folders and ONE active request folder bound to this chat. Never enumerate, search, open or inspect other pending/unapproved folders or historical TEST, DRAFT and REJECTED outputs. Similar scene wording is not continuation authority. A new chat uses a fresh request; previous attempts, failures and budgets never transfer. Resolve reusable assets only from approved registries.


The scenario's generation backend is the built-in `image_gen` tool, exposed as
`image_gen__imagegen` in code mode. Project CLIs prepare and validate its call;
they do not need their own generation endpoint. Execute the exact prepared
prompt and references through this tool after `EXECUTION_STARTED`. That is
scenario execution, not a bypass. Do not search for an additional StoryArt
generator or declare a blocker because no tool has that name.
Do not add an executor-discovery or availability-check step before that call.

Keep one request state and advance it through these phases. A phase is not a
reason to rediscover information already resolved for the unchanged request.

1. Resolve the approved character once and keep the returned canonical identity,
   confirmed profile revision, active assets and bound style. Record the actual
   menu response and provenance when it arrives. Ask only for genuinely missing
   choices; internal command errors never invalidate an accepted user answer.
2. Read the selected style's verbal profile and existing matrix, shortlist the
   few relevant sources, then inspect selected originals only. Reuse valid
   hash/role/applicability-backed evidence. Do not search optional libraries or
   create extra references unless requested. Read a changed source again only
   for the affected role.
3. Save one structured preparation request with source observations and the
   prompt. Assemble deterministic plan, reference, risk and exact-call data in
   one invocation when their inputs are available. Repair the reported field
   in that request; do not restart source selection or reconstruct the menu.
   After a plan is prepared, a call-only correction must resume call preparation
   rather than rebuild the plan.
4. Use the prepared exact prompt and attachments. After READY, perform only the
   permitted exact-call validation/start and invoke the generator. Keep the
   same pending provider operation/tool handle until it resolves. Elapsed time
   or a yielded tool result never authorizes a duplicate generation. Resolve
   an unknown outcome before another attempt.
5. Inspect the returned image in one coordinated pass and record separate
   outcomes for all required layers. Reopen or zoom only an uncertain region;
   do not reopen the entire image once per checklist label. Batch deterministic
   registration and guard updates after evidence is ready. Deliver promptly;
   optional archiving and reporting must not delay the visible result.

Fresh byte checks at a later execution boundary remain necessary. Repeating
the same scan or hash within one unchanged validation operation does not add
evidence. Keep these two cases distinct: remove duplicate work inside the
operation, but invalidate saved evidence when its source, profile revision,
roles, prompt or user choice changes. Do not hide a failure by reusing an old
PASS. Additional agents are never needed merely to run these commands.

### One preparation request

Keep the current request's resolved character/profile, selected sources, exact
menu answer and its provenance together in one UTF-8 JSON request file. Use
`python tools/generation_request.py --request-file <request.json>` to invoke
the existing `prepare-generation` handler. Keys are the manager's argument
names with underscores; values keep their native JSON types. Repeated options
are arrays, and JSON evidence is stored as objects rather than shell-escaped
strings. The wrapper checks basic input errors before expensive preparation
and returns them together. Correct those fields in the same file.

When the prompt and reference risk observations are ready, add
`--call-file <call.json>` to that same invocation. This runs reference
resolution, exact-prompt risk assessment and `prepare-call` in process, then
returns the exact executable call. Do not separately run those three commands.
The call file contains exactly one of `prompt_text` / `prompt_text_file`, an
optional `stage_id`, and `reference_ratings`. Each rating records the resolved
`path`, its `active_roles`, `content_and_reference_risk` (D1–D10), `use_impact`
(-2D through +2D), and the observed `reason_ru`. Collect these observations
during the selected-source inspection; do not add a second viewing pass for
the risk form. An empty rating array applies only to a call with no physical
references. The tool binds confirmed profile facts before assessing the exact
prompt and never invents visual ratings.

For a call-only correction after successful plan preparation, use
`--finalize-only --call-file <call.json>` with the existing request file. This
continues from the saved plan. A request that is already READY must resume its
existing guard/call or follow the recorded correction path; never overwrite its
risk report, rebuild the plan, or launch another provider operation as a retry.
For an already READY multi-stage plan, supply its intended `stage_id` explicitly;
the wrapper verifies the permitted transition before writing. Each replacement
assessment uses a new file so a failed preparation preserves the previous call's
bound evidence.

Do not add a separate routine `--validate-only` call: the normal invocation
already performs input validation. A successful input check does not replace
source, identity, exact-call or output validation. Once preparation succeeds,
continue to exact-call binding and generation; do not repeat preparation,
re-resolve unchanged character/style data, or reopen the same reviewed sources
without changed input or a specific invalidated result. Retain menu provenance
when the choice arrives so preparation never needs a later chat search merely
to reconstruct an already accepted answer.

The root performs `GENERATOR_OPERATOR` and `REGISTRAR` responsibilities
sequentially. Do not spawn agents merely to call a generator, run project CLI,
or record an image. Use at most one optional, bounded visual-QA agent only when
independent visual judgement will materially help. Even with one reviewer, all
required semantic layers remain separately inspected and recorded.

| Delegated image role | Requested model and effort | Limit |
| --- | --- | --- |
| `STYLE_LIBRARIAN` / `IDENTITY_CURATOR` | Registry role: Sol 6.1 Low | Optional bounded source discovery; root resolves material ambiguity |
| `CALL_PLANNER` | Registry role: Sol 6.1 Medium | Only a complex exact call with useful independent planning |
| `VISUAL_QA` / critical independent QA | Registry role: fresh Sol 6.1 Medium | Only when a separate bounded judgement or required independent gate is useful |
| `GENERATOR_OPERATOR` / `REGISTRAR` | No agent; root executes sequentially | N/A |

These are spawn requests, not a runtime scheduler or a way to switch the
current session model. A role is dispatched only when its bounded evidence is
needed for the current deliverable.

Do not create six roles for every image. Use only the smallest useful set:
direct root handling for a simple, already-planned call; a bounded librarian or
identity curator when missing evidence blocks a selection; one planner for a
complex exact call; optionally one visual QA reviewer. Generation, registration,
guard transitions, stage order, and user communication stay with the root.

For a short prompt, retain only the active constraints in this order:

1. goal and locked invariants;
2. identity and non-negotiable body/coverage constraints;
3. style constraints;
4. scene, lighting, and composition.

Do not accumulate synonym stacks, stale constraints, or rejected output anchors.

## Evidence reuse and adapters

Reuse an existing review only for the exact selected source with matching
path and SHA-256, active role, full-resolution view, applicability and recorded
limitations. Unselected candidate pools require no source review. Review changed
or newly selected originals and any uncovered requirement; never invent review
receipts or assume an automatic cache. Confirm current selected originals still
match before relying on a prior review.

For a new source review, `--reviewed-source` takes a JSON reviewer attestation
with the exact semantic role, attachment slot, path, full-resolution view,
outcome, applicability, visual findings and limitations. Record the attestation
only after inspecting that selected source; the manager binds its path and
current SHA-256 but cannot independently prove what the reviewer saw. A result
other than `PASS`, missing findings, or missing limitations blocks preparation.

Reuse a current selected local style adapter when its actual inputs are
unchanged. Run the builder only when that adapter is missing or stale for the
selected source set, and pass `--style-name "<selected STYLE>"` so it refreshes
only the selected adapter and index entry. Do not rebuild every style adapter
as routine startup. Adapters remain routing indexes, not visual sources.

## Failure handling

Calibration is only an explicit user request or a user-consented proposal. This
durable current rule supersedes historical automatic-calibration trigger clauses
in `PORTABLE_GENERATION_RULES.md`, while preserving
every applicable calibration protocol and QA requirement once authorized. It is
never automatically launched because QA failed, a style is unproven, or a user
objects. No calibration or test art is created for workflow optimization.

After two failed QA attempts, preserve all mandatory stages, state the exact
defect and failed layer, and perform one evidence-based recovery using original
sources. Do not silently repeat a loop, widen scope, replace a required stage,
or turn rejected output into a source. Continue through remaining mandatory
stages when the guard permits; record a blocker only for a concrete external
condition.

When a second failure of the same QA layer at the same stage occurs after an
explicit correction addressed the first, the guard requires one `ESCALATION_ORCHESTRATOR` incident before a
further attempt. It is an exceptional, read-only registry `ESCALATION_ORCHESTRATOR` (Sol 6.1 High) error handler,
not a per-frame worker: it receives recorded evidence and returns one bounded
Sol work order. It must not use tools, generate, test, edit, perform QA,
approve, or spawn agents; the root dispatches any recommended executor. It runs
once per corrected incident. Calibration remains a user-requested or
user-consented proposal only.

When a style is `UNKNOWN`/unformed and uncalibrated but has at least five unique
QA-passed outputs, `style-readiness` may propose formalization and calibration.
This is read-only and never creates calibration state or test art.

## Claims

This policy is a process design. Do not claim quantified quota savings, elapsed
time savings, or improved visual quality until a comparable live measurement
exists.

## Image-specific routing authority

This section supersedes historical coding-model assignments and repeated full-pool
inspection requirements only for orchestration/preparation. All art, safety,
archive, identity, approval and semantic QA requirements remain mandatory.

- Use `config/model_routes.json` and `MODEL_PROFILES.md` for explicit role
  assignments. Root performs deterministic operations directly; optional Low
  workers receive bounded independent work. Existing sessions keep their model.
- Reuse the resolver result, approved IDs and user choices while the request
  inputs remain unchanged. Retry `resolve-character` only after a concrete
  invalidation, changed input or resolver error. Ambiguous semantic classification
  stays with the Medium root and approved registry; it does not invent visual identity.
- Usually use zero workers; one when there is useful separate work, at most two
  for independent preparation. No nested agents, full-history forks, or agents
  merely for CLI, archive, generator calls, or individual QA-layer checkboxes.
- Request fresh registry `VISUAL_QA` (Sol 6.1 Medium) for a new face before dependent base views, final
  reusable-base acceptance, changed authoritative identity/style sources,
  conflicting QA requirements, or a repeated defect of the same layer. Do not
  request it automatically for every routine frame. An LLM PASS is not proof
  against specific contradictory visual/user evidence.
- After the first failure, correct the specific cause. After the same-layer
  repeated failure, obtain one diagnosis before another authorized attempt;
  do not automatically generate a stack of prompt variants.
- Sol 6.1 High is exceptional read-only diagnosis only. Bounded implementation
  uses Low; complex planning and fresh substantial-code review use Medium.
  An evidenced complex fault or substantive repair failure qualifies for
  diagnosis regardless of which model performed the earlier attempt.
- Worker packets include explicit model/effort, fork_turns="none", authoritative
  input paths, acceptance and a bounded return. Never dump the complete tool
  catalog. Timing/tool discovery is optional and must not displace the task.
- Quantified quota savings and equal visual quality require actual evidence;
  the routing policy alone proves neither.
- Use existing guard timestamps/provider receipts for preparation, generation,
  QA/recovery and delivery; separate user waits, include failed attempts and
  all workers. Reuse the short locked contract through retries. Report missing
  token/cost telemetry as unknown; do not add per-step measurement commands.
