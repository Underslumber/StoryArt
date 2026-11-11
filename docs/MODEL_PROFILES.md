# StoryArt model routing

The current project-specific production route supersedes historical generic
Luna assignments: Sol 6.1 Medium root, Sol 6.1 Low bounded ordinary workers,
Medium complex call planning and independent review, High exceptional diagnosis.
Root keeps its selected model; existing chats never switch from prose/config.

The authoritative role registry is `config/model_routes.json`. The profile
switcher and orchestration handoffs read it; do not infer a worker's model family
from the root model or copy old model assignments. Other protocol,
ownership, fidelity, stage, safety and QA requirements still apply.

## Active route

| Responsibility | Model / effort | Use |
|---|---|---|
| Root: user contract, planning, execution, recovery and acceptance | `gpt-6.1-sol`, Medium | Current production default |
| Source discovery, identity curation and ordinary implementation | `gpt-6.1-sol`, Low | Registry DEFAULT_WORKER / STYLE_LIBRARIAN / IDENTITY_CURATOR / CODE_IMPLEMENTER; bounded packet without transcript |
| Complex call planning and independent visual QA | `gpt-6.1-sol`, Medium | CALL_PLANNER / VISUAL_QA; root retains acceptance |
| Mandatory independent code review | `gpt-6.1-sol`, Medium | `sol_review` after machine checks and recorded SOL_GATE |
| Exceptional diagnosis after evidenced complex fault or repair failure | `gpt-6.1-sol`, High | `sol_escalation` with evidence and recorded SOL_GATE |
| Explicit ChatGPT Web image execution | `gpt-6.1-sol`, Low | Project-local `web_image`; complete final-spec packet; root retains QA |
| Ordinary-scene root experiment | `gpt-6.1-sol`, Low | Explicit trial; no automatic lowering of root effort |

High is not a routine fallback. Luna 6 Low is an explicit experiment only;
unavailability never authorizes automatic family substitution or Astra. Before
dispatch verify the configured role supports the requested model/effort; after
dispatch check available telemetry. If a runtime role pins another model, root
executes sequentially rather than claiming the requested route was used.
Specialized Web roles are not substitutes for ordinary workers.

## Configuration

Apply the approved production profile from the repository root:

```powershell
.\scripts\set-model-profile.ps1 6.1
```

The switcher updates only the root/default-agent model and their effort in
`.codex/config.toml`, preserving other settings. A missing default project config
is created from `config/codex.project.example.toml`. The production profile uses
Medium root and Sol 6.1 Low default worker. Spawn requests must still specify the
registry role's model and effort explicitly with `fork_turns="none"`.

Legacy `5.6` and `6` profiles remain explicit compatibility/comparison choices;
they are not the recommended production profile. Their root/default-agent
settings do not redefine the current role registry or authorize automatic
family substitution. An unavailable requested model must be reported accurately;
preserve the request and recover or have root execute the contract sequentially.

Changes apply to new sessions and newly spawned agents. They do not switch an
already running chat. Local permission settings are separate from model routing.

## End-to-end efficiency

For ordinary art, root executes lookup, preparation, the generator, QA and
registration sequentially. Normally zero workers; one when bounded independent
work materially helps; at most two independent workers within runtime capacity.
Do not spawn a worker merely for a CLI command, a generator call or an individual
QA checkbox. Independent code review remains required for substantial code work.

Reuse the active request's accepted choices, valid resolver results, exact-call
binding and equivalent source reviews. Re-read only changed or uncovered inputs.
Send a short contract/evidence packet, never the complete chat or large raw logs.
Packet budgets return evidence to root; they do not terminate the user goal.

Use existing guard event times and actual provider receipts to report preparation,
generation, QA/recovery, registration and delivery when available. Separate time
waiting for the user. Count all attempts and workers, including rejected results.
Do not add a telemetry command before each existing step. Accepted output means
the full user contract passed visual QA and was delivered; user approval is a
separate fact and must not be inferred from an agent PASS.

Low has no separate token-price discount. Compare total tokens/time and cost per
accepted result on ordinary user-requested work; label incomplete telemetry as
unknown. No extra test art or calibration is authorized by model optimization.
Neither this route nor syntax checks prove savings or future visual quality.
