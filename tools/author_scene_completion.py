"""Verify explicit author storage independently of technical image acceptance."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import stat
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path

SESSION_ROOT = Path.home() / ".codex" / "sessions"
RECEIPT_NAME = "AUTHOR_SCENE_COMPLETION.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plain_file(path: Path) -> Path:
    """Do not let junctions or symlinks redirect a trusted receipt or source."""
    path = path.absolute()
    for parent in (path, *path.parents):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Author scene paths cannot contain links or reparse points.")
    if not path.is_file():
        raise ValueError("Author scene evidence is not a file.")
    return path.resolve()


def same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def text_blocks(value: object) -> str:
    if isinstance(value, str):
        return value
    return "".join(x.get("text", "") for x in value or [] if isinstance(x, dict))


def checked_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or "\ufffd" in value:
        raise ValueError("Author evidence text is missing or corrupted.")
    return unicodedata.normalize("NFC", value.strip())


def has_original_inline_image(output: object, expected_hash: str) -> bool:
    if not isinstance(output, list):
        return False
    for block in output:
        if not isinstance(block, dict) or block.get("type") != "input_image":
            continue
        url = block.get("image_url")
        if not isinstance(url, str) or not url.startswith("data:image/") or ";base64," not in url:
            continue
        try:
            raw = base64.b64decode(url.split(";base64,", 1)[1], validate=True)
        except (ValueError, binascii.Error):
            continue
        if hashlib.sha256(raw).hexdigest() == expected_hash:
            return True
    return False


def _logged_record(lines: list[bytes], binding: dict) -> dict:
    line = binding.get("line")
    if type(line) is not int or not 1 <= line <= len(lines):
        raise ValueError("Author host-log line is missing.")
    raw = lines[line - 1]
    if hashlib.sha256(raw).hexdigest() != binding.get("sha256"):
        raise ValueError("Author host-log evidence changed.")
    return json.loads(raw)


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate(guard_path: Path, state: dict, receipt_path: Path) -> dict:
    """Replay native approval, original output and host-recorded author turns."""
    try:
        from . import style_pack_manager as manager
    except ImportError:
        import style_pack_manager as manager

    root = guard_path.resolve().parent
    receipt_path = plain_file(receipt_path)
    if not same_path(receipt_path, root / RECEIPT_NAME):
        raise ValueError("Author scene receipt must use its fixed current-request path.")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("schema_version") != 1 or receipt.get("request_id") != state.get("request_id"):
        raise ValueError("Author scene receipt has a foreign request or schema.")
    stages = state.get("required_stages", [])
    if (state.get("task_kind") != "IMAGE_GENERATION" or state.get("request_purpose") != "SCENE"
            or len(stages) != 1 or stages[0].get("id") != "SINGLE_PASS"
            or state.get("author_contract_acceptance") or state.get("direct_edit_completion_delivery")
            or state.get("recovery")):
        raise ValueError("Author storage is restricted to one unmixed SCENE request.")
    attempts = state.get("attempts", [])
    if (not attempts or any(a.get("status") == "UNKNOWN" for a in attempts)
            or any(a.get("execution_snapshot", {}).get("request_purpose") == "CHARACTER_BASE" for a in attempts)):
        raise ValueError("Author storage cannot reconcile UNKNOWN or absent execution.")
    latest = attempts[-1]
    if (receipt.get("attempt_id") != latest.get("attempt_id")
            or receipt.get("task_revision") != state.get("task_revision")
            or latest.get("task_revision") != state.get("task_revision")
            or latest.get("status") not in {"ACTIVE", "RESULT_AVAILABLE", "DELIVERED"}
            or any(a.get("status") == "ACTIVE" for a in attempts[:-1])
            or (state.get("active_attempt") or {}).get("attempt_id", latest["attempt_id"]) != latest["attempt_id"]):
        raise ValueError("Author storage must bind the latest current-revision received attempt.")
    if latest.get("status") == "DELIVERED":
        if not state.get("author_scene_storage") or latest.get("result_status") != "APPROVED_SCENE":
            raise ValueError("Author storage cannot rewrite an already-delivered attempt.")
    elif latest.get("result_status") or latest.get("delivered_at"):
        raise ValueError("Author storage cannot rewrite an existing result or delivery status.")
    snapshot = latest.get("execution_snapshot", {})
    plan_path = plain_file(Path(snapshot.get("reference_plan_snapshot_path", "")))
    if not plan_path.is_relative_to(root) or digest(plan_path) != snapshot.get("reference_plan_snapshot_sha256"):
        raise ValueError("Author scene immutable execution snapshot changed.")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    call = snapshot.get("execution_call", {})
    if (plan.get("generation_purpose") != "SCENE" or snapshot.get("request_purpose") != "SCENE"
            or plan.get("request_id") != state["request_id"] or plan.get("execution_call") != call
            or hashlib.sha256(json.dumps(call, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest() != snapshot.get("execution_call_sha256")
            or hashlib.sha256(call.get("prompt", {}).get("text", "").encode()).hexdigest() != snapshot.get("prompt_sha256")):
        raise ValueError("Author scene call, purpose or prompt binding changed.")
    character = plan.get("character_id", "")
    if not character.startswith("CHAR_") or receipt.get("character_id") != character:
        raise ValueError("Author scene character binding differs.")
    artifact = latest.get("provider_artifact", {})
    output = plain_file(Path(artifact.get("path", "")))
    output_hash = digest(output)
    if (not output.is_relative_to(root) or artifact.get("sha256") != output_hash
            or receipt.get("output_sha256") != output_hash):
        raise ValueError("Author scene original provider bytes differ.")
    paths = manager.make_paths(Path(plan["pack_path"]).resolve().parent, plan["style_name"])
    registry = manager.read_csv(paths.generation_manifest)
    rows = [r for r in registry
            if r.get("request_id") == manager.safe_component(state["request_id"], "approved")
            and r.get("character_id") == character and r.get("status") == "APPROVED_SCENE"]
    if len(rows) != 1 or rows[0].get("generation_id") != receipt.get("generation_id"):
        raise ValueError("Author scene requires one exact native approved registry entry.")
    row = rows[0]
    saved = plain_file(Path(row["style_file"]))
    saved_key = os.path.normcase(os.path.abspath(saved))
    if any(os.path.normcase(os.path.abspath(r.get("style_file", ""))) == saved_key
           and r.get("qa_output_sha256") != output_hash for r in registry):
        raise ValueError("Another registry row binds different bytes to the saved scene path.")
    scene_root = (manager.character_folder(paths, character) / "02_SCENES").resolve()
    quote = checked_text(receipt.get("approval_quote"))
    notes = checked_text(row.get("notes"))
    if (not saved.is_relative_to(scene_root) or not same_path(saved, Path(receipt.get("approved_scene_path", "")))
            or digest(saved) != output_hash or row.get("qa_output_sha256") != output_hash
            or not notes.startswith("Author accepted this exact scene for storage despite the following QA findings: ")
            or not notes.endswith("author_acceptance_quote=" + quote)
            or receipt.get("technical_findings") != row.get("notes")
            or not isinstance(receipt.get("delivery_evidence"), list)
            or not receipt["delivery_evidence"]
            or any(not isinstance(item, str) or not item.strip() for item in receipt["delivery_evidence"])):
        raise ValueError("Author scene native approval, findings, saved bytes or delivery differ.")

    # The host session log is outside the writable project. Never accept an
    # agent-created quotation receipt as the sole proof of author permission.
    session = plain_file(Path(receipt.get("host_session_log", "")))
    session_root = SESSION_ROOT.resolve()
    if not session.is_relative_to(session_root) or session.is_relative_to(paths.workspace.resolve()):
        raise ValueError("Author permission must come from the host session log.")
    lines = session.read_bytes().splitlines()
    meta = json.loads(lines[0])
    owner = state.get("owner_chat_id")
    if not isinstance(owner, str) or not owner.strip() or meta.get("type") != "session_meta" or meta.get("payload", {}).get("id") != owner:
        raise ValueError("Author host session belongs to another chat.")
    delivered = _logged_record(lines, receipt.get("host_delivery", {}))
    payload = delivered.get("payload", {})
    provider = plain_file(Path(receipt.get("provider_output_path", "")))
    delivery_text = text_blocks(payload.get("output", []))
    generated_root = session_root.parent / "generated_images" / str(owner)
    if (not provider.is_relative_to(generated_root.resolve()) or digest(provider) != output_hash
            or delivered.get("type") != "response_item"
            or payload.get("type") not in {"custom_tool_call_output", "function_call_output"}
            or not has_original_inline_image(payload.get("output"), output_hash)
            or os.path.normcase(str(provider)) not in os.path.normcase(delivery_text)
            or "The generated image is already displayed to the user" not in delivery_text
            or _time(delivered["timestamp"]) < _time(latest["started_at"])):
        raise ValueError("Author approval lacks this exact original image's prior inline delivery.")
    approvals = receipt.get("host_user_messages", [])
    if not approvals:
        raise ValueError("Author scene lacks host-recorded user permission.")
    quotes = []
    previous_line = receipt["host_delivery"]["line"]
    message_ids = set()
    for binding in approvals:
        if (type(binding.get("line")) is not int or binding["line"] <= previous_line
                or binding.get("message_id") in message_ids):
            raise ValueError("Author messages must have unique increasing host-log positions.")
        previous_line = binding["line"]
        message_ids.add(binding.get("message_id"))
        logged = _logged_record(lines, binding)
        payload = logged.get("payload", {})
        message = checked_text(text_blocks(payload.get("content", [])))
        if (logged.get("type") != "response_item" or payload.get("type") != "message"
                or payload.get("role") != "user" or message != checked_text(binding.get("quote"))
                or payload.get("id") != binding.get("message_id")
                or _time(logged["timestamp"]) <= _time(delivered["timestamp"])):
            raise ValueError("Author permission is forged, foreign or predates image delivery.")
        quotes.append(message)
    approval_lines = {item["line"] for item in approvals}
    for number in range(receipt["host_delivery"]["line"] + 1, previous_line + 1):
        logged = json.loads(lines[number - 1])
        payload = logged.get("payload", {})
        if logged.get("type") != "response_item":
            continue
        if payload.get("role") == "user" and number not in approval_lines:
            raise ValueError("Another user turn intervened before scene acceptance.")
        output = payload.get("output")
        if isinstance(output, list) and any(isinstance(b, dict) and b.get("type") == "input_image" for b in output):
            raise ValueError("Another displayed image intervened before scene acceptance.")
    if "; ".join(quotes) != quote:
        raise ValueError("Native approval quote differs from the host-recorded author turns.")
    return {"receipt_path": str(receipt_path), "receipt_sha256": digest(receipt_path),
            "generation_id": row["generation_id"], "attempt_id": latest["attempt_id"],
            "task_revision": state["task_revision"], "output_sha256": output_hash,
            "approved_scene_path": str(saved), "technical_findings": row["notes"]}


def retire(guard_path: Path, state: dict, event: str, timestamp: str) -> None:
    """A new correction must not be shadowed by a previous storage completion."""
    binding = state.pop("author_scene_storage", None)
    if not binding:
        return
    history = {"binding": binding, "retired_by": event, "retired_at": timestamp}
    source = guard_path.resolve().parent / RECEIPT_NAME
    if source.is_file() and digest(source) == binding.get("receipt_sha256"):
        source = plain_file(source)
        archive = source.with_name("AUTHOR_SCENE_COMPLETION_" + digest(source) + ".json")
        if not archive.exists() or digest(archive) != digest(source):
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=source.parent, prefix=".author-scene-", suffix=".tmp", delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(source.read_bytes())
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, archive)
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
        history["receipt_archive"] = str(archive)
    state.setdefault("author_scene_storage_history", []).append(history)
