"""Codex Stop hook for the currently focused, chat-bound StoryArt image task.

No generator calls, transcript parsing, source scanning, trust changes or guard
mutation. A blocking Stop decision asks the existing agent to continue work.
Codex must independently trust the hook definition before it is executed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

try:
    from . import task_execution_guard as guard
except ImportError:
    import task_execution_guard as guard

ROOT = Path(__file__).resolve().parents[1]
IMAGE_KINDS = {"IMAGE_GENERATION", "IMAGE_GENERATION_NATIVE_DEFAULT", "IMAGE_EDIT"}


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Expected an object")
    return value


def decide(event: dict, root: Path = ROOT) -> dict:
    # Never revive an interruption or keep a worker alive on its parent's task.
    if event.get("hook_event_name") != "Stop" or event.get("agent_id"):
        return {}
    session = str(event.get("session_id") or "").strip()
    if not session or Path(str(event.get("cwd") or "")).resolve() != root.resolve():
        return {}
    registry = root / ".agent" / "chat_requests"
    key = hashlib.sha256(session.encode("utf-8")).hexdigest()
    focus_path = registry / "focus" / f"{key}.json"
    if not focus_path.is_file():
        return {}
    try:
        focus = _read(focus_path)
        if focus.get("thread_id") != session or focus.get("task_kind") not in IMAGE_KINDS:
            return {}
        path = Path(str(focus.get("guard_path") or "")).resolve()
        relative = path.relative_to(root.resolve())
        if (len(relative.parts) != 4 or not relative.parts[0].upper().endswith("_GENERATIONS")
                or relative.parts[1].upper() != "00_PENDING"
                or relative.parts[-1] != "EXECUTION_GUARD.json"):
            raise ValueError("Focused image guard is outside its permitted request folder")
        chat = _read(registry / "chats" / f"{key}.json")
        folder_key = hashlib.sha256(str(path.parent).encode("utf-8")).hexdigest()
        folder = _read(registry / "folders" / f"{folder_key}.json")
        if (chat.get("thread_id") != session or folder.get("thread_id") != session
                or Path(str(chat.get("active_folder") or "")).resolve() != path.parent
                or Path(str(folder.get("active_folder") or "")).resolve() != path.parent):
            raise ValueError("Focused image guard does not match current chat ownership")
    except (OSError, ValueError, TypeError) as error:
        # No trusted image scope => do not open other folders or force an
        # unrelated task. Report the enforcement gap instead of pretending OK.
        return {"systemMessage": f"StoryArt completion hook could not verify current request ownership: {error}"}

    try:
        state = _read(path)
    except (OSError, ValueError, TypeError) as error:
        # Ownership has already been verified independently of guard contents.
        # A torn/missing current state is internal recovery, not task completion.
        return {"decision": "block", "reason": (
            f"StoryArt current request state {path} cannot be read: {error}. "
            "Recover only this chat-bound request state from its existing evidence and continue; "
            "do not open another request, fabricate completion, or repeat an unknown provider operation."
        )}
    if (state.get("owner_chat_id") != session or state.get("task_kind") not in IMAGE_KINDS
            or state.get("request_id") != path.parent.name):
        return {"decision": "block", "reason": (
            f"StoryArt current guard {path} has identity fields inconsistent with its independently verified chat and folder ownership. "
            "Recover only this owned guard using its existing request evidence and registry binding, then continue; "
            "do not open another request or claim completion from the corrupted state."
        )}

    if state.get("status") in {"STOPPED", "CANCELLED", "WAITING_FOR_USER"}:
        return {}
    if state.get("status") == "BLOCKED" and state.get("blocker_kind") in {"EXTERNAL", "SAFETY", "USER_INPUT"}:
        return {}
    try:
        continuation = guard.completion_continuation(state)
    except (guard.GuardError, OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        return {"decision": "block", "reason": (
            f"StoryArt current request {state['request_id']} has an internal completion-state error: {error}. "
            f"Repair the existing guard {path} and continue the authorized request; do not claim completion."
        )}
    action = str(continuation.get("action", ""))
    if state.get("status") == "COMPLETE" and continuation.get("can_complete"):
        return {}
    retry_guidance = (
        "A previous Stop already requested continuation. Diagnose why that next action did not finish, "
        "revise the strategy from concrete evidence, and perform the next useful action; "
        "do not repeat the same final promise or unchanged failed call. "
        if event.get("stop_hook_active") else ""
    )
    return {
        "decision": "block",
        "reason": (
            f"StoryArt request {state['request_id']} is not finished. "
            f"Current guard: {path}. Next action: {action}. "
            f"{continuation.get('reason', '')} "
            + retry_guidance +
            "Continue the authorized task now using docs/REQUEST_FIDELITY.md. "
            "Internal QA failures, elapsed time and repair counts require recovery or a changed strategy, "
            "not a final answer promising future work. Preserve the full user contract and primary sources; "
            "never fabricate PASS, repeat an unknown provider operation, or reinterpret an external/safety limit. "
            "Respect an explicit user stop, cancellation, or changed task."
        ),
    }


def main() -> int:
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            raise ValueError("Hook event must be an object")
        result = decide(event)
    except (OSError, ValueError, TypeError) as error:
        result = {"systemMessage": f"StoryArt completion hook input failed: {error}"}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
