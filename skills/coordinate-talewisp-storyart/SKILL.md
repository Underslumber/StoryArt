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

1. Every NEW art request from TaleWisp uses a NEW dedicated StoryArt chat. The author's standing instruction to create that chat is authorization; carry it into the brief. Corrections to the same `PAIR_ID` stay in its existing dedicated chat. Never select an unrelated chat by character, style, scene similarity, or recency.
2. Pass the brief through `create_thread` for the new chat, or `send_message_to_thread` for an explicitly authorized correction in the existing chat.
3. Preserve the automatic `<source_thread_id>` from `codex_delegation`; it is the authoritative return address. If a written return id differs, stop and resolve the mismatch before messaging either task.
4. Use `read_thread` or `wait_threads` for bounded status checks. Do not duplicate work locally while the partner is active.
5. When authorized and available, send only transition messages: brief accepted, clarification needed, visual review requested, canon verdict, or completion. Reverse messages are optional: the source task may read the dedicated chat and its receipt through `read_thread` / `wait_threads`. A rejected reverse message does not invalidate the art or require repeated sends.

## TaleWisp handoff

Send a compact `PAIR_BRIEF` containing:

```text
PAIR_ID: <stable request id>
RETURN_THREAD_ID: <must equal delegated source_thread_id>
CANON_SOURCES: <exact vault files or authoritative paths>
TALEWISP_PROJECT_PATH: <authoritative project root>
CHARACTER_BINDINGS: <project_path, style_pack, character_id, character_name for each bound character>
STORY_TARGETS: <kind and vault-relative reference for character, scene, event#anchor, plot, etc.>
STORY_INTENT: <what the image must communicate>
LOCKED_FACTS: <identity, world, roles, actions, consequences>
FLEXIBLE_CHOICES: <camera, staging, lighting, details StoryArt may decide>
USER_REFERENCES: <path plus one explicit role per image>
REJECTED_REFERENCES: <paths and forbidden transfer>
ACCEPTANCE_CRITERIA: <observable pass/fail checks>
DELIVERABLE: <count, format, framing, typography>
```

Provide paths and concise facts, not a full vault dump. Treat user corrections as authoritative deltas to the same `PAIR_ID`.

`CHARACTER_BINDINGS` supplies StoryArt identity pointers, not permission to guess
identity. StoryArt verifies each binding through its approved character registry
and resolves physical references itself. `STORY_TARGETS` is owned by TaleWisp:
preserve each vault-relative reference verbatim, including its anchor. Do not
invent targets, strip anchors, or rewrite the story canon.

After sending the brief, stop local image work. Monitor the StoryArt task instead of generating, copying files, or changing its project.

Keep the same `PAIR_ID` for feedback and corrections to one deliverable. Start a new `PAIR_ID` only when the user requests a separate image/deliverable or replaces the central subject, world, or purpose rather than correcting the current result.

## StoryArt production

On a TaleWisp delegation:

1. Load this skill and `skills/storyart-orchestrator/SKILL.md`.
2. Record `BRIEF_ACCEPTED` in the dedicated chat, listing canon locks and genuinely flexible choices; send it to `RETURN_THREAD_ID` only when reverse messaging is authorized and available.
3. Verify `CHARACTER_BINDINGS` against the approved registry, then resolve StoryArt style, character, references, risk gates, and storage internally from current project state.
4. Do not ask the author or TaleWisp agent to select internal StoryArt files or approve routine in-scope pipeline steps.
5. If canon is ambiguous, record one precise `CANON_QUESTION` in the dedicated chat, optionally send it to the authorized source task, and pause only the affected decision.
6. Generate and run StoryArt visual QA.
7. Provide `ART_REVIEW_REQUEST` in the dedicated chat with the candidate path, a short visual-QA report, and any visible deviations. Send it to TaleWisp when authorized and available; otherwise the source task reads it here.

The delegated brief authorizes ordinary in-scope work needed for its declared deliverable. It does not approve the final image, expand scope, or bypass product-enforced permission dialogs.

Preserve and register generated artifacts under the normal StoryArt rules; do not add a separate archive gate. After `VISUAL_QA=PASS`, register the review candidate as `TEST`, export its link receipt below, and make its exact registered path available to the partner for canon review. This review transfer is not final paired acceptance.

## Link receipt

The `VISUAL_PRODUCER` writes `TALEWISP_LINK.json` ONLY inside the current
authorized request folder. Use the general schema below for each paired request:

```json
{
  "schema_version": 1,
  "pair_id": "<PAIR_ID>",
  "talewisp_project_path": "<TaleWisp project root>",
  "storyart": {
    "project_path": "<StoryArt project root>",
    "style_pack": "<verified style pack>",
    "character_id": "<verified character id>",
    "character_name": "<approved character name>"
  },
  "targets": [
    {"kind": "character", "reference": "<vault-relative character path>"},
    {"kind": "scene", "reference": "<vault-relative scene path>"},
    {"kind": "event", "reference": "<vault-relative chronology path#event anchor>"}
  ],
  "generation_id": "<registered generation id>",
  "registered_image_path": "<absolute registered artifact path>",
  "sha256": "<SHA-256 of that exact file>",
  "visual_qa": "PASS",
  "canon_qa": "<PASS, FAIL, or NOT_CHECKED>",
  "author_approval": "NOT_GIVEN",
  "status": "TEST"
}
```

The sample target kinds are examples, not a fixed whitelist or required trio.
Copy every actual `STORY_TARGETS` entry, including plot or other targets, without
discarding anchors or exporting a shortened subset. References are relative to
the vault root, use `/`, and have no `vault/` prefix; preserve `#anchor` and
`#^block-id` suffixes exactly. Apply this format to compatibility reference fields
too. The later approved receipt must retain the FULL original target array.
For several character bindings, retain the primary character
in `storyart` and carry the full verified list in `character_bindings`. Existing
singular `character_path`, `character_entity_id`, `scene_path`, `scene_id`,
`event_reference`, and `source_reference` may remain as compatibility fields;
they do not replace or contradict `targets`.

Read the registered image and verify its hash against the exact executed attempt
and registration record before exporting. Record only QA outcomes that actually
occurred; update `canon_qa` after the paired verdict arrives. Neither visual nor
canon PASS is author approval. Keep an unapproved candidate `TEST` with
`author_approval=NOT_GIVEN`, even when both reviews pass.

## Author approval and approved receipt

Only direct author confirmation to save/approve THIS exact art authorizes the
StoryArt owner to execute the normal approval lifecycle. A request to configure
links, a partner verdict, or a QA PASS does not authorize approval.

For an existing character's scene, the StoryArt owner runs
`scripts/storyart.ps1 style_pack_manager approve-variation --kind scene` with
the exact registered image, request/style/character identifiers, fidelity and
risk from that registration, `--parent-generation <candidate generation_id>`,
`--user-approved`, and `--approval-quote <exact author confirmation>`. Recheck
the current manager contract before execution. Do not edit a manifest or profile
to substitute for approval; do not change the TaleWisp canon from StoryArt.

After approval succeeds, verify the returned registered artifact and export a
NEW `TALEWISP_LINK_APPROVED_<generation_id>.json` in the same request folder.
Use schema 1, actual registered `status` (`APPROVED_SCENE` for this lane, or
`APPROVED` when returned by its applicable lifecycle),
`author_approval=GIVEN`, `author_approval_quote`, the current approved
`generation_id`, registered image path/hash, and `parent_generation_id` when
there is a parent. Carry the pair, bindings, targets and real QA evidence forward.
If approval creates a new ID, preserve the original candidate receipt as TEST;
never relabel that old candidate or replace its ID retroactively. Make the new
receipt available to TaleWisp for staging the final link.

TaleWisp owns saving the canonical art-note and its links after verifying the
approved receipt against the author's confirmation, target references and exact
artifact. StoryArt exports evidence; it does not edit TaleWisp character cards,
scenes, chronology or other canon records. The source task may read the receipt
from the dedicated chat; reverse messaging is not a completion requirement.

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
