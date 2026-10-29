# Role contracts

Use the smallest set of roles needed. The primary agent remains the coordinator
and never delegates user communication, scope changes, guard completion,
visual acceptance or registration. Built-in generation is root-only; explicit
ChatGPT Web execution uses the WEB_IMAGE exception below. The root
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

## WEB_IMAGE

This role is dispatched only when the user explicitly chooses ChatGPT Web.
`chatgpt_web_image_handoff create` binds the complete final specification,
unchanged execution prompt, source hashes, current attempt and private project
destination. Pass its packet to `web_image` (registry profile: Sol 6.1 Low).
The role writes only inside the active request, submits once and downloads the
original through the local Oracle CLI. UNKNOWN/RUNNING continue the original
session. No source selection, prompt edits, manual browser control, visible
generation windows, auth copying, Cloudflare bypass or alternate generator.
Root owns source authority, visual QA, registration and COMPLETE. See
`docs/CHATGPT_WEB_IMAGE.md` for portable setup and human login/project binding.

## VISUAL_QA

- Apply `docs/IMAGE_REVIEW_DECISION.md` to the actual output and full user
  contract. Return an evidence-backed `ACCEPT`, `REVISE`, or `REVIEW_REQUIRED`
  summary alongside the independent per-layer results. This is a review
  verdict, not user approval or a replacement for existing QA records.
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

- Available for root diagnosis when a stage fails again, including when its QA
  label is missing or conflicts with other evidence. Repeat diagnosis after
  later failures as needed. The counter triggers diagnosis; it is not a task
  stop condition or evidence of acceptance.
- Request the registry's `ESCALATION_ORCHESTRATOR` profile (Sol 6.1 High),
  strictly as exceptional diagnosis, not routine review or a per-frame worker.
- Consume only listed evidence and return one bounded, evidence-based Sol work
  order. The root alone dispatches that executor; no nesting. Diagnosis needs
  evidence of a complex fault or substantive repair failure; an earlier Luna
  attempt is not a prerequisite.
- Remain read-only and do not use tools, generate, test, edit, perform QA,
  approve, or spawn agents.

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
