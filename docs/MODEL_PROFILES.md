# StoryArt model routing

The authoritative role registry is `config/model_routes.json`. The profile
switcher and orchestration handoffs read it; do not infer a worker's model family
from the root model or copy old Luna/Astra assignments. This project route
supersedes the generic orchestration model defaults for StoryArt. Other protocol,
ownership, fidelity, stage, safety and QA requirements still apply.

## Active route

| Responsibility | Model / effort | Use |
|---|---|---|
| Root: user contract, planning, execution, recovery and acceptance | `gpt-6.1-sol`, Medium | Current production default |
| Bounded source discovery and ordinary code implementation | `gpt-6.1-sol`, Low | Optional worker with explicit inputs and ownership |
| Complex call planning and independent visual/code review | `gpt-6.1-sol`, Medium | Only where a separate judgement materially helps |
| Exceptional diagnosis | `gpt-6.1-sol`, High | Recorded complex fault or substantive repair failure |
| Ordinary-scene root experiment | `gpt-6.1-sol`, Low | Explicit trial; no automatic lowering of root effort |
| Mechanical-worker experiment | `gpt-5.6-luna`, Low | Explicit trial with no decisions about identity, blockers or acceptance |

Sol 6.1 High requires evidence of a complex fault or substantive repair failure.
High is not the default for workers. Luna 6 is outside the normal production
route. The two experiments are opt-in and are not automatic fallbacks.

## Configuration

Apply the approved production profile from the repository root:

```powershell
.\scripts\set-model-profile.ps1 6.1
```

The switcher updates only the root/default-agent model and their effort in
`.codex/config.toml`, preserving other settings. A missing default project config
is created from `config/codex.project.example.toml`. The production profile uses
Medium root and Low default worker. Spawn requests must still specify the
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
