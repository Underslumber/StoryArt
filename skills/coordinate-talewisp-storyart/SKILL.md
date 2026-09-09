---
name: coordinate-talewisp-storyart
description: Coordinate a pair of Codex tasks between TaleWisp and StoryArt for canon-grounded illustrations. Use when a TaleWisp task asks to create, revise, hand off, monitor, or canon-review art in the StoryArt project, or when a StoryArt task receives a codex_delegation from TaleWisp.
---

# Coordinate TaleWisp and StoryArt

Work as two peer agents with separate authority. Never collapse both roles into one task.

## Ownership

Determine the current role from the selected project or working directory and
the role-bound brief:

- the task owning the authoritative story vault and canon brief is
  `CANON_OWNER`;
- the task owning the StoryArt request, style, character, and visual pipeline
  is `VISUAL_PRODUCER`.

Do not require a fixed filesystem path or infer a role from a project name
alone. If the selected project and brief do not establish one role, ask before
dispatching or changing either project.

`CANON_OWNER` owns:

- live story canon, terminology, character knowledge, narrative intent, and desired reader impression;
- the scene brief and canon acceptance verdict;
- communication with the author about story meaning.

`VISUAL_PRODUCER` owns:

- existing StoryArt styles, characters, reference selection, risk assessment, generation, visual QA, registration, and approval lifecycle;
- composition and rendering choices not locked by canon;
- communication with the author about the produced visual artifact.

Never let TaleWisp choose StoryArt reference files, run image generation, edit StoryArt request state, or register an image. Never let StoryArt invent or silently revise canon.

## Connect the pair

Use Codex task coordination tools. Discover them with `tool_search` when they are not already callable.

1. Reuse an existing relevant task in the destination project. Create a new task only when the user explicitly requests one.
2. Send the brief with `send_message_to_thread`.
3. Preserve the automatic `<source_thread_id>` from `codex_delegation`; it is the authoritative return address. If a written return id differs, stop and resolve the mismatch before messaging either task.
4. Use `read_thread` or `wait_threads` for bounded status checks. Do not duplicate work locally while the partner is active.
5. Send only transition messages: brief accepted, clarification needed, visual review requested, canon verdict, or completion.

When choosing among existing StoryArt tasks, prefer in order:

1. the task already owning the named approved character, style, or active request;
2. a task whose title and summary match the deliverable;
3. the most recently active matching StoryArt task.

If two candidates remain equally valid, ask the user instead of guessing.

## TaleWisp handoff

Send a compact `PAIR_BRIEF` containing:

```text
PAIR_ID: <stable request id>
RETURN_THREAD_ID: <must equal delegated source_thread_id>
CANON_SOURCES: <exact vault files or authoritative paths>
STORY_INTENT: <what the image must communicate>
LOCKED_FACTS: <identity, world, roles, actions, consequences>
FLEXIBLE_CHOICES: <camera, staging, lighting, details StoryArt may decide>
USER_REFERENCES: <path plus one explicit role per image>
REJECTED_REFERENCES: <paths and forbidden transfer>
ACCEPTANCE_CRITERIA: <observable pass/fail checks>
DELIVERABLE: <count, format, framing, typography>
```

Provide paths and concise facts, not a full vault dump. Treat user corrections as authoritative deltas to the same `PAIR_ID`.

After sending the brief, stop local image work. Monitor the StoryArt task instead of generating, copying files, or changing its project.

Keep the same `PAIR_ID` for feedback and corrections to one deliverable. Start a new `PAIR_ID` only when the user requests a separate image/deliverable or replaces the central subject, world, or purpose rather than correcting the current result.

## StoryArt production

On a TaleWisp delegation:

1. Load this skill and `skills/storyart-orchestrator/SKILL.md`.
2. Reply to `RETURN_THREAD_ID` with `BRIEF_ACCEPTED`, listing canon locks and genuinely flexible choices.
3. Resolve StoryArt style, character, references, risk gates, and storage internally from current project state.
4. Do not ask the author or TaleWisp agent to select internal StoryArt files or approve routine in-scope pipeline steps.
5. If canon is ambiguous, send one precise `CANON_QUESTION` to the source task and pause only the affected decision.
6. Generate and run StoryArt visual QA.
7. Send `ART_REVIEW_REQUEST` to the TaleWisp task with the candidate path, a short visual-QA report, and any visible deviations.

The delegated brief authorizes ordinary in-scope work needed for its declared deliverable. It does not approve the final image, expand scope, or bypass product-enforced permission dialogs.

Archive every generated file immediately under normal StoryArt rules. After `VISUAL_QA=PASS`, register the review candidate as `TEST`, then send its path privately to the partner task for canon review. This review transfer is not final presentation to the user.

## Dual review

Keep the checks independent:

- `VISUAL_QA`: style, identity pixels, anatomy, composition, effects, attachments, canvas, and StoryArt storage rules.
- `CANON_QA`: world logic, profession/role meaning, character knowledge, terminology, narrative emphasis, and requested symbolism.

The TaleWisp task must answer with either:

```text
CANON_PASS
PAIR_ID: <id>
EVIDENCE: <observable reasons>
```

or:

```text
CANON_FAIL
PAIR_ID: <id>
CORRECTIONS: <minimal observable deltas>
UNCHANGED_LOCKS: <facts that must not drift>
```

StoryArt may present a candidate as the paired result only after both `VISUAL_QA=PASS` and `CANON_QA=PASS`. Keep it `TEST` until the author directly approves it.

For monitoring, wait in bounded 30–60 second snapshots and do not narrate unchanged states. After three unchanged snapshots, inspect the partner's last event and its age; report the observed state, but never restart, close, or duplicate the task without user authorization.

## Boundaries

- The partner agent is evidence and a domain authority, not a substitute for the user.
- Never treat agent-to-agent praise as final user approval.
- Never use rejected or accidental cross-project generations as positive references.
- Never ask one agent to approve a system permission dialog for the other. Product-enforced approval remains a user boundary.
- On failure, return the smallest correction to the owning agent instead of taking over its role.
