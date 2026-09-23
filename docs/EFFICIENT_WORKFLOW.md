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
| Ordinary image production | Luna Medium | Default model for image work; root keeps user communication, guard transitions, choices, final decision, and evidence reconciliation. |
| Bounded metadata/catalog discovery | Luna High | Receives a bounded packet and returns evidence; no user conversation or broad writes. |
| Code planning/integration and independent review | Sol Low | Root plans and integrates; a fresh Sol Low reviews the actual diff after machine checks. |
| Discovery, mechanics, ordinary code implementation | Luna High | Owns only explicitly named files and verification. |
| Complex implementation or repair after evidenced Luna failure | Sol High | Sol High requires evidence of a substantive Luna failure for either complex implementation or repair; complexity alone does not qualify. |

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

The project TOML defaults are project-wide and optimized for image work, not
a per-task scheduler. For a code/infrastructure task explicitly select Sol Low
for the root at task creation and Luna High for discovery and ordinary
implementation, then fresh Sol Low for review. Sol High requires evidence of
a substantive Luna failure for either complex implementation or repair;
complexity alone does not qualify. If an existing root differs,
report that fact and use explicit worker assignments; never claim it switched.

## Image-production route

The root performs `GENERATOR_OPERATOR` and `REGISTRAR` responsibilities
sequentially. Do not spawn agents merely to call a generator, run project CLI,
or record an image. Use at most one optional, bounded visual-QA agent only when
independent visual judgement will materially help. Even with one reviewer, all
required semantic layers remain separately inspected and recorded.

| Delegated image role | Requested model and effort | Limit |
| --- | --- | --- |
| `STYLE_LIBRARIAN` / `IDENTITY_CURATOR` | `gpt-6-luna`, High | Bounded source discovery; root owns choices and integration |
| `CALL_PLANNER` | `gpt-6-luna`, Medium | Ordinary image planning; root integrates |
| Routine `VISUAL_QA` | `gpt-6-luna`, Medium | Only when a separate bounded review is useful |
| Critical independent QA | fresh `gpt-6-sol`, Low | Objective high-impact gate only |
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

Recorded complete source-pool review can be reused across tasks only when file
hashes, source roles, style/character, covered views and recorded limitations
still apply. Inspect the selected authoritative originals for the current shot.
Review changed/new sources and any uncovered requirement; never invent reviewed
counts. Missing evidence requires the normal complete applicable review. This
is an evidence protocol, not a claim that an automatic software cache exists.

Reuse a current selected local style adapter when its actual inputs are
unchanged. Run the existing builder only when the selected adapter is missing or
stale for the selected source set. Its current CLI has no per-style selector and
refreshes the local root; do not invent a selector or run it merely because a
workflow begins. Adapters remain routing indexes, not visual sources.

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
further attempt. It is an exceptional, read-only `gpt-6-astra` Low error handler,
not a per-frame worker: it receives recorded evidence and returns one bounded
Luna/Sol work order. It must not use tools, generate, test, edit, perform QA,
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

- Use Luna Medium as the single ordinary image-production default. Existing sessions
  do not switch models merely because this document or config changes.
- Luna High handles bounded metadata/search discovery; return ambiguous semantic
  classification to the Luna Medium image lead. It does not select authoritative visual identity.
- Usually use zero workers; one when there is useful separate work, at most two
  for independent preparation. No nested agents, full-history forks, or agents
  merely for CLI, archive, generator calls, or individual QA-layer checkboxes.
- Request fresh Sol Low for a new face before dependent base views, final
  reusable-base acceptance, changed authoritative identity/style sources,
  conflicting QA requirements, or a repeated defect of the same layer. Do not
  request it automatically for every routine frame. An LLM PASS is not proof
  against specific contradictory visual/user evidence.
- After the first failure, correct the specific cause. After the same-layer
  repeated failure, obtain one diagnosis before another authorized attempt;
  do not automatically generate a stack of prompt variants.
- Astra Low is exceptional read-only diagnosis only. Code development remains
  separate: Sol Low planning/integration and fresh review; Luna High discovery
  and ordinary implementation. Sol High requires evidence of a substantive Luna
  failure for either complex implementation or repair; complexity alone does not
  qualify.
- Worker packets include explicit model/effort, fork_turns="none", authoritative
  input paths, acceptance and a bounded return. Never dump the complete tool
  catalog. Timing/tool discovery is optional and must not displace the task.
- Quantified quota savings and equal visual quality require actual evidence;
  the routing policy alone proves neither.
