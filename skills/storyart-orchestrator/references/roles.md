# Role contracts

Use the smallest set of roles needed for the request. The primary agent remains the
orchestrator and never delegates user communication, scope changes, or guard completion.

## STYLE_LIBRARIAN

- Read the complete selected style pack and current `style-context`.
- Inspect actual candidates for every applicable visual role.
- Return a minimal compatible reference proposal with rejected alternatives and reasons.
- Remain read-only. Do not create prompts, call the generator, or modify manifests.

## IDENTITY_CURATOR

- Inspect the approved character profile, assembly, face, and all canonical body views.
- Select the smallest authoritative identity subset for the requested shot.
- Report continuity risks and missing identity evidence.
- Remain read-only. Do not redesign the character or approve a permanent identity change.

## CALL_PLANNER

- Combine the locked goal, selected style, identity sources, requested composition, attachment
  limit, current risk report, and active `REFERENCE_PLAN.json`.
- Return one exact-call prompt and one exact physical attachment list.
- Remain read-only. Do not call the generator or broaden the request.

## GENERATOR_OPERATOR

- Receive one already validated prompt and exact attachment list.
- Confirm the matching `EXECUTION_STARTED` transition immediately before the real call.
- Call the image generator exactly once for the declared deliverable.
- Write only inside the request directory and mandatory `GENERATION_RESULTS` archive through
  existing StoryArt recording tools.
- Do not research, change the prompt, select new references, edit infrastructure, or retry
  independently.

## VISUAL_QA

- Inspect the full-resolution result and authoritative comparison sources.
- Score every required semantic QA layer independently.
- Return `PASS` or `FAIL` per layer with short visual evidence.
- Remain read-only. Do not repair, regenerate, archive, approve, or reinterpret a failed layer.

## REGISTRAR

- Receive the generator output plus finalized QA verdict.
- Use existing StoryArt managers to record, archive, reject, approve, or store the result.
- Write only to the active request, `GENERATION_RESULTS`, and the explicitly selected permanent
  character or storyline destination.
- Do not choose references, change QA, call the generator, or modify project infrastructure.
