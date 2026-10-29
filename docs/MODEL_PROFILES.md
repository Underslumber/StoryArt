# StoryArt model routing

User routing correction of 2026-10-13 takes precedence over historical profile
descriptions below: ordinary subagents use Luna 6 High with no inherited
transcript; root keeps its selected model. Sol requires the dedicated review or
escalation gate, or a direct user request. The specialized Web image agent keeps
Sol 6.1 Low for explicitly selected ChatGPT Web image execution.

The authoritative role registry is `config/model_routes.json`. The profile
switcher and orchestration handoffs read it; do not infer a worker's model family
from the root model or copy old model assignments. The user routing correction
governs this registry. Other protocol,
ownership, fidelity, stage, safety and QA requirements still apply.

## Active route

| Responsibility | Model / effort | Use |
|---|---|---|
| Root: user contract, planning, execution, recovery and acceptance | `gpt-6.1-sol`, Medium | Current production default |
| Source discovery, call planning, ordinary implementation and result checks | `gpt-6-luna`, High | `default`, `worker` or `explorer`; bounded packet without transcript |
| Mandatory independent code review | `gpt-6.1-sol`, Medium | `sol_review` after machine checks and recorded SOL_GATE |
| Escalation after substantive Luna failure | `gpt-6.1-sol`, High | `sol_escalation` with evidence and recorded SOL_GATE |
| Explicit ChatGPT Web image execution | `gpt-6.1-sol`, Low | Project-local `web_image`; complete final-spec packet; root retains QA |
| Ordinary-scene root experiment | `gpt-6.1-sol`, Low | Explicit trial; no automatic lowering of root effort |

Difficulty alone does not authorize Sol. Ordinary workers use Luna High;
unavailability never permits automatic Sol fallback. Specialized Web roles are
not substitutes for ordinary workers.

## Configuration

Apply the approved production profile from the repository root:

```powershell
.\scripts\set-model-profile.ps1 6.1
```

The switcher updates only the root/default-agent model and their effort in
`.codex/config.toml`, preserving other settings. A missing default project config
is created from `config/codex.project.example.toml`. The production profile uses
Medium root and Luna High default worker. Spawn requests must still specify the
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
