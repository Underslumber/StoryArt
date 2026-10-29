"""Opt-in autonomous Oracle MCP browser image execution, with durable reconciliation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import subprocess
import shutil
import threading
import queue
import time
import re
import uuid
from functools import wraps
from pathlib import Path
from datetime import datetime, timezone
from PIL import Image

NEUTRAL_PROMPT = "\u0421\u043e\u0437\u0434\u0430\u0439 \u043a\u0432\u0430\u0434\u0440\u0430\u0442\u043d\u043e\u0435 \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435: \u043e\u0434\u0438\u043d \u0441\u0438\u043d\u0438\u0439 \u043a\u0440\u0443\u0433 \u043f\u043e \u0446\u0435\u043d\u0442\u0440\u0443 \u0431\u0435\u043b\u043e\u0433\u043e \u0444\u043e\u043d\u0430, \u043a\u0430\u043a \u043d\u0430 \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u043d\u043e\u043c \u0440\u0435\u0444\u0435\u0440\u0435\u043d\u0441\u0435. \u0411\u0435\u0437 \u0442\u0435\u043a\u0441\u0442\u0430, \u043b\u044e\u0434\u0435\u0439 \u0438 \u0434\u043e\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u0445 \u043e\u0431\u044a\u0435\u043a\u0442\u043e\u0432."
NEUTRAL_PROMPT_SHA256 = "0a9a8517a791a05e748581313bea1a034ccbeb362ac157b516d714ba55b52239"
BROWSER_MODELS = {"GPT-5.6 Sol": "gpt-5.6-sol", "GPT-5.5": "gpt-5.5-pro",
                  "GPT-5.5 Instant": "gpt-5.5-instant", "Thinking 5.5": "gpt-5.5",
                  "Thinking 5.4": "gpt-5.4"}
NEUTRAL_FIXTURE_SHA256 = "c25862853323ad595f450edc4f4fc7b7abe6ee90ab916f67e1ec7e022aaa48fa"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def image_binding(path):
    path = Path(path)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("Image must be an existing absolute local file")
    with Image.open(path) as image:
        image.verify()
    return {"path": str(path.resolve()), "sha256": digest(path.read_bytes())}


def save(path, state):
    descriptor, name = tempfile.mkstemp(prefix=Path(path).name + ".", suffix=".tmp", dir=Path(path).parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def exclusive(function):
    @wraps(function)
    def locked(path, *args, **kwargs):
        lock = Path(path).resolve().parent / ".chatgpt-web-image.lock"
        handle = lock.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if not handle.tell():
                handle.write(b"0"); handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise ValueError("Journal is exclusively locked by another operation") from error
            return function(path, *args, **kwargs)
        finally:
            handle.close()  # OS releases ownership, including after process termination.
    return locked


def canonical_hash(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def attempt_binding(guard, attempt):
    snapshot = attempt.get("execution_snapshot", {})
    immutable = {key: value for key, value in snapshot.items() if key != "provider_artifact"}
    return {"request_id": guard.get("request_id"), "task_revision": attempt.get("task_revision"),
            "attempt_id": attempt.get("attempt_id"), "stage": attempt.get("stage"),
            "reference_binding": attempt.get("reference_binding"), "snapshot": immutable}


def authorized_attempt(guard, plan, proof, plan_path):
    attempt = guard.get("active_attempt")
    if (guard.get("status") != "ACTIVE" or guard.get("phase") != "EXECUTION"
            or not isinstance(attempt, dict) or attempt.get("status") != "ACTIVE"):
        raise ValueError("Guard has no current ACTIVE execution")
    call = plan.get("execution_call", {})
    snapshot = attempt.get("execution_snapshot", {})
    binding = attempt.get("reference_binding", {})
    call_hash = canonical_hash(call)
    stage = call.get("stage_id")
    revision = guard.get("task_revision")
    if (not stage or not isinstance(revision, int) or not attempt.get("attempt_id")
            or attempt.get("attempt_id") != proof.get("attempt_id")
            or attempt.get("task_revision") != revision
            or attempt.get("stage") != stage
            or snapshot.get("request_id") != guard.get("request_id")
            or snapshot.get("attempt_id") != attempt["attempt_id"]
            or snapshot.get("task_revision") != revision
            or snapshot.get("execution_stage") != stage or snapshot.get("guard_stage") != stage
            or snapshot.get("execution_call") != call
            or snapshot.get("execution_call_sha256") != call_hash
            or binding.get("execution_call_sha256") != call_hash
            or binding.get("prompt_sha256") != call.get("prompt", {}).get("text_sha256")
            or binding.get("stage") != stage
            or binding.get("path") != str(plan_path)
            or binding.get("sha256") != digest(plan_path.read_bytes())
            or [{"path": r.get("path"), "sha256": r.get("sha256")} for r in binding.get("references", [])]
               != [{"path": r.get("path"), "sha256": r.get("sha256")} for r in call.get("slots", [])]
            or snapshot.get("reference_binding") != binding):
        raise ValueError("Current manager attempt does not bind exact stage/call/revision")
    snap_path = Path(snapshot.get("reference_plan_snapshot_path", ""))
    if (not snap_path.is_file() or digest(snap_path.read_bytes()) != snapshot.get("reference_plan_snapshot_sha256")
            or json.loads(snap_path.read_bytes()) != plan):
        raise ValueError("Executed plan snapshot mismatch")
    listed = next((a for a in guard.get("attempts", []) if a.get("attempt_id") == attempt["attempt_id"]), None)
    if not listed or attempt_binding(guard, listed) != attempt_binding(guard, attempt):
        raise ValueError("Active attempt is not the recorded executed attempt")
    return attempt_binding(guard, attempt)


def authorized_native_attempt(guard, call, proof, *, active=True):
    try:
        from tools.task_execution_guard import GuardError, validate_native_default_call
    except ImportError:
        from task_execution_guard import GuardError, validate_native_default_call
    if guard.get("task_kind") != "IMAGE_GENERATION_NATIVE_DEFAULT":
        raise ValueError("Native evidence requires the native/default guard")
    try:
        binding = validate_native_default_call(call, str(guard.get("request_id", "")), None,
            str(guard.get("locked_invariants", {}).get("character", "")))
    except GuardError as error:
        raise ValueError(f"Native exact-call authorization invalid: {error}") from error
    listed = next((a for a in guard.get("attempts", [])
                   if a.get("attempt_id") == proof.get("attempt_id")), None)
    attempt = guard.get("active_attempt") if active else listed
    if (not isinstance(attempt, dict) or not listed
            or (active and (guard.get("status") != "ACTIVE" or guard.get("phase") != "EXECUTION"
                           or attempt.get("status") != "ACTIVE"))):
        raise ValueError("Guard has no recorded authorized native execution")
    snapshot = attempt.get("execution_snapshot", {})
    revision = attempt.get("task_revision")
    if (not isinstance(revision, int) or not attempt.get("attempt_id")
            or attempt.get("attempt_id") != proof.get("attempt_id")
            or (active and revision != guard.get("task_revision"))
            or attempt.get("stage") != "SINGLE_PASS"
            or attempt.get("reference_binding") != binding
            or snapshot.get("reference_binding") != binding
            or snapshot.get("request_id") != guard.get("request_id")
            or snapshot.get("attempt_id") != attempt["attempt_id"]
            or snapshot.get("task_revision") != revision
            or snapshot.get("request_purpose") != "NATIVE_DEFAULT"
            or snapshot.get("execution_stage") != "SINGLE_PASS"
            or snapshot.get("guard_stage") != "SINGLE_PASS"
            or snapshot.get("execution_call") != call
            or snapshot.get("execution_call_sha256") != canonical_hash(call)
            or snapshot.get("prompt_sha256") != binding["prompt_sha256"]
            or attempt_binding(guard, listed) != attempt_binding(guard, attempt)):
        raise ValueError("Native attempt does not bind exact call/revision/snapshot")
    return attempt_binding(guard, attempt)


def event(state, status, **details):
    state["status"] = status
    state["events"].append({"at": datetime.now(timezone.utc).isoformat(), "status": status, **details})


PROJECT_CONFIG = Path(__file__).resolve().parents[1] / "config/chatgpt_web_image.local.json"


def project_binding(config_path=PROJECT_CONFIG):
    source = Path(config_path).resolve()
    raw = source.read_bytes()
    route = json.loads(raw.decode("utf-8-sig"))
    project_id = route.get("project_id", "")
    if (not re.fullmatch(r"g-p-[a-f0-9]+", project_id) or not route.get("project_name")
            or route.get("project_url") != f"https://chatgpt.com/g/{project_id}/project"):
        raise ValueError("Valid dedicated ChatGPT project destination required")
    binding = {"path": str(source), "sha256": digest(raw), "route": route}
    return dict(binding, binding_sha256=canonical_hash(binding))


def verify_project_binding(binding, *, current=False):
    if not isinstance(binding, dict):
        raise ValueError("Dedicated project binding missing; bind-project before submission")
    content = {key: value for key, value in binding.items() if key != "binding_sha256"}
    if canonical_hash(content) != binding.get("binding_sha256"):
        raise ValueError("Pinned project binding changed")
    route = binding.get("route", {})
    project_id = route.get("project_id", "")
    if (not re.fullmatch(r"g-p-[a-f0-9]+", project_id)
            or route.get("project_url") != f"https://chatgpt.com/g/{project_id}/project"):
        raise ValueError("Pinned dedicated destination invalid")
    if current and project_binding(binding["path"]) != binding:
        raise ValueError("Local dedicated project destination changed; submission blocked")
    return route["project_url"]


def runtime_config(state):
    config = {"engine": "browser", "browser": {"manualLogin": True,
        "manualLoginProfileDir": state["profile_dir"], "manualLoginCookieSync": False,
        "cookieSync": False, "captureProviderNative": False,
        "archiveConversations": "never", "headless": not state.get("windows_isolated_browser", False)}}
    if state.get("project_binding"):
        url = verify_project_binding(state["project_binding"])
        config["browser"].update(chatgptUrl=url, url=url)
    return config


def pin_runtime_config(state):
    state["oracle_config"] = runtime_config(state)
    state["oracle_config_sha256"] = canonical_hash(state["oracle_config"])


@exclusive
def bind_project(path, *, config_path=PROJECT_CONFIG):
    state = load(path)
    if (state["status"] != "PREPARED" or state.get("session_id")
            or any(e.get("status") not in {"PREPARED", "PROJECT_BOUND"} for e in state["events"])
            or (Path(state["oracle_home"]) / "receipt.json").exists()):
        raise ValueError("Project binding only allowed for never-submitted PREPARED journal")
    binding = project_binding(config_path)
    config_path_runtime = Path(state["oracle_home"]) / "config.json"
    if config_path_runtime.exists() and json.loads(config_path_runtime.read_text(encoding="utf-8")) != runtime_config(state):
        raise ValueError("Existing private Oracle config changed; cannot bind project")
    state["project_binding"] = binding
    pin_runtime_config(state)
    if config_path_runtime.exists():
        save(config_path_runtime, state["oracle_config"])
    event(state, "PREPARED", action="PROJECT_BOUND", project_id=binding["route"]["project_id"])
    save(path, state)
    return state


@exclusive
def prepare(path, prompt, references, model_strategy, *, opt_in=False, safety_history=None,
            evidence_path=None, neutral_test=False, output=None, profile_dir=None, model_label=None, oracle_root=None, project_config=PROJECT_CONFIG, windows_isolated_browser=False):
    if windows_isolated_browser and os.name != "nt":
        raise ValueError("Isolated ordinary browser requires Windows; no foreground fallback")
    path = Path(path)
    if path.exists():
        raise ValueError("Existing request journal cannot be replaced")
    if opt_in is not True or safety_history != "CLEAR" or model_strategy not in {"current", "select"}:
        raise ValueError("Explicit opt-in, clear safety history and explicit model strategy required")
    if not prompt.strip():
        raise ValueError("Empty prompt")
    runtime_binding = None
    if oracle_root is None:
        try:
            from tools.web_image_setup import verify
        except ImportError:
            from web_image_setup import verify
        runtime_binding = verify()
        oracle_root = runtime_binding["installation"]
        profile_dir = profile_dir or runtime_binding["profile_dir"]
    if model_strategy == "select" and model_label not in BROWSER_MODELS:
        raise ValueError("select requires a verified supported ChatGPT model label")
    if not output or not Path(output).is_absolute() or Path(output).exists():
        raise ValueError("Output must be a new absolute path")
    if not profile_dir or not Path(profile_dir).is_absolute():
        raise ValueError("Existing persistent Oracle profile path required")
    refs = [image_binding(p) for p in references]
    if len({r["path"] for r in refs}) != len(refs):
        raise ValueError("Duplicate references")
    if any(Path(r["path"]).stat().st_size > 1_000_000 for r in refs):
        raise ValueError("Reference exceeds pinned Oracle default 1MB cap; no silent omission")
    prompt_hash = digest(prompt.encode("utf-8"))
    evidence = None
    if neutral_test:
        if prompt != NEUTRAL_PROMPT or prompt_hash != NEUTRAL_PROMPT_SHA256 or len(refs) != 1 or refs[0]["sha256"] != NEUTRAL_FIXTURE_SHA256:
            raise ValueError("Neutral test requires literal prompt and one new root-owned fixture")
    else:
        if evidence_path is None:
            raise ValueError("Scenario exact-call validation evidence required")
        source = Path(evidence_path).resolve()
        raw = source.read_bytes()
        proof = json.loads(raw)
        if (proof.get("validated") is not True or proof.get("prompt_sha256") != prompt_hash
                or proof.get("references") != refs or not proof.get("request_id")
                or not proof.get("guard_path")
                or bool(proof.get("reference_plan_path")) == bool(proof.get("execution_call_path"))):
            raise ValueError("Scenario evidence does not bind this exact call")
        native = bool(proof.get("execution_call_path"))
        plan_path = Path(proof["execution_call_path" if native else "reference_plan_path"]).resolve()
        guard_path = Path(proof["guard_path"]).resolve()
        plan = json.loads(plan_path.read_bytes())
        guard = json.loads(guard_path.read_bytes())
        call = plan if native else plan.get("execution_call", {})
        if (call.get("prompt") != {"text": prompt, "text_sha256": prompt_hash}
                or [{"path": s.get("path"), "sha256": s.get("sha256")} for s in call.get("slots", [])] != refs
                or call.get("request_id") != proof["request_id"]
                or guard.get("request_id") != proof["request_id"]
                or not {"READY_FOR_EXECUTION", "EXECUTION_STARTED"}.issubset(
                    {e.get("event") for e in guard.get("events", [])})):
            raise ValueError("Actual manager plan/guard does not authorize exact execution")
        attempt = (authorized_native_attempt(guard, call, proof) if native
                   else authorized_attempt(guard, plan, proof, plan_path))
        evidence = {"path": str(source), "sha256": digest(raw), "request_id": proof["request_id"], "attempt": attempt,
                    "plan": {"path": str(plan_path), "sha256": digest(plan_path.read_bytes())},
                    "guard": {"path": str(guard_path)}}
        if native:
            evidence["branch"] = "NATIVE_DEFAULT"
    active = path.resolve().parent / ".chatgpt-web-image-active.json"
    if active.exists():
        bound = json.loads(active.read_text(encoding="utf-8"))
        previous = Path(bound["journal"])
        if not previous.is_file():
            raise ValueError("Active journal binding is missing; reconcile original journal")
        previous_state = load(previous)
        if previous_state["status"] not in {"DOWNLOADED", "COMPLETED", "PROVIDER_FAILED"}:
            if previous_state.get("no_send_verdict"):
                try:
                    from tools import chatgpt_web_image_no_send as no_send
                except ImportError:
                    import chatgpt_web_image_no_send as no_send
                no_send.validate_new_handoff(__import__(__name__,fromlist=['']),previous,path,previous_state,evidence)
            else:
                _validate_replacement_handoff(previous, path, previous_state, evidence)
    destination = project_binding(project_config)
    state = {"schema_version": 1, "project_binding": destination, "prompt": prompt, "prompt_sha256": prompt_hash,
             "runtime_binding": runtime_binding, "references": refs, "oracle_root": str(Path(oracle_root).resolve()), "model_strategy": model_strategy, "model_label": model_label,
             "profile_dir": str(Path(profile_dir)), "requested_output": str(Path(output).resolve()),
             "oracle_home": str(path.resolve().parent / (path.stem + ".oracle-runtime")), "opt_in": True,
             "safety_history": "CLEAR", "neutral_test": neutral_test,
             "windows_isolated_browser": bool(windows_isolated_browser),
             "browser_mode": "windows-isolated" if windows_isolated_browser else "headless",
             "scenario_evidence": evidence, "events": []}
    pin_runtime_config(state)
    event(state, "PREPARED")
    save(path, state)
    save(active, {"journal": str(path.resolve())})
    return state


def _validate_replacement_handoff(previous_path, new_path, previous_state, new_evidence):
    """Allow exactly one new journal after the guard records explicit replacement authorization."""
    if (previous_state.get("schema_version") != 1 or previous_state.get("status") != "UNKNOWN"
            or any(previous_state.get(key) for key in ("session_id", "operation_id", "provider_receipt",
                                                       "output", "native_download_receipt"))
            or Path(previous_path).resolve().parent != Path(new_path).resolve().parent
            or not isinstance(new_evidence, dict) or new_evidence.get("branch") != "NATIVE_DEFAULT"):
        raise ValueError("Active journal is not eligible for an explicitly authorized replacement")
    old_evidence = previous_state.get("scenario_evidence") or {}
    old_attempt = old_evidence.get("attempt") or {}
    snapshot = old_attempt.get("snapshot") or {}
    old_call = snapshot.get("execution_call") or {}
    guard_binding = (new_evidence.get("guard") or {})
    old_guard_binding = ((old_evidence.get("guard") or {}).get("path"))
    new_guard_path = guard_binding.get("path")
    new_attempt = new_evidence.get("attempt") or {}
    old_attempt_id = old_attempt.get("attempt_id")
    new_attempt_id = new_attempt.get("attempt_id")
    output_path = previous_state.get("requested_output")
    oracle_home_raw = previous_state.get("oracle_home")
    events = previous_state.get("events")
    request_root = Path(previous_path).resolve().parent
    if not isinstance(oracle_home_raw, str) or not Path(oracle_home_raw).is_absolute():
        raise ValueError("Original Oracle home must be an absolute path inside this request")
    oracle_home = Path(oracle_home_raw).resolve()
    try:
        oracle_home.relative_to(request_root)
    except ValueError as error:
        raise ValueError("Original Oracle home must be inside this request") from error
    if (not isinstance(old_evidence.get("path"), str) or not isinstance(old_evidence.get("sha256"), str)
            or old_evidence.get("branch") != "NATIVE_DEFAULT"
            or not isinstance(old_evidence.get("plan"), dict) or not isinstance(old_evidence.get("guard"), dict)
            or not isinstance(old_attempt.get("request_id"), str)
            or not isinstance(old_attempt.get("snapshot"), dict)
            or not isinstance(snapshot.get("provider_operation_receipt"), str)
            or snapshot.get("provider_operation_receipt") != ""
            or not isinstance(output_path, str) or not output_path
            or Path(output_path).exists()
            or (oracle_home / "receipt.json").exists()
            or (oracle_home / "generated" / "result.png").exists()
            or not isinstance(events, list)
            or any(event.get("status") in {"DOWNLOADED", "COMPLETED"}
                   or any(key in event for key in ("output", "provider_image")) for event in events)
            or not old_attempt_id or not new_attempt_id or old_attempt_id == new_attempt_id
            or not old_guard_binding or new_guard_path != old_guard_binding
            or not isinstance(new_evidence.get("request_id"), str)
            or old_attempt.get("request_id") != new_evidence["request_id"]
            or snapshot.get("request_id") != new_evidence["request_id"]
            or old_call.get("request_id") != new_evidence["request_id"]):
        raise ValueError("Replacement must reuse the original guard and request binding")
    guard = json.loads(Path(new_guard_path).read_bytes())
    authorization = guard.get("replacement_authorization") or {}
    retired = next((a for a in guard.get("attempts", []) if a.get("attempt_id") == old_attempt_id), None)
    if (guard.get("request_id") != old_attempt.get("request_id")
            or not retired or retired.get("status") != "ABANDONED_UNKNOWN"
            or retired.get("outcome") != "NOT_KNOWN"
            or not authorization.get("consumed")
            or authorization.get("attempt_id") != old_attempt_id
            or authorization.get("consumed_by_attempt_id") != new_attempt_id):
        raise ValueError("Guard does not prove one explicitly authorized replacement of this UNKNOWN attempt")
    receipt_path = Path(str(authorization.get("path", "")))
    try:
        receipt_raw = receipt_path.read_bytes()
        receipt = json.loads(receipt_raw)
    except (OSError, ValueError) as error:
        raise ValueError("Replacement authorization receipt is missing or invalid") from error
    try:
        from tools.task_execution_guard import (REPLACEMENT_AUTHORIZATION_ANSWER,
            REPLACEMENT_AUTHORIZATION_QUESTION, REPLACEMENT_AUTHORIZATION_SOURCE)
    except ImportError:
        from task_execution_guard import (REPLACEMENT_AUTHORIZATION_ANSWER,
            REPLACEMENT_AUTHORIZATION_QUESTION, REPLACEMENT_AUTHORIZATION_SOURCE)
    if (digest(receipt_raw) != authorization.get("sha256")
            or receipt.get("request_id") != guard.get("request_id")
            or receipt.get("attempt_id") != old_attempt_id
            or receipt.get("question") != REPLACEMENT_AUTHORIZATION_QUESTION
            or receipt.get("answer") != REPLACEMENT_AUTHORIZATION_ANSWER
            or receipt.get("source") != REPLACEMENT_AUTHORIZATION_SOURCE):
        raise ValueError("Replacement authorization receipt does not match the guard seal")


def load(path, *, for_submission=False):
    state = json.loads(Path(path).read_text(encoding="utf-8"))
    if (state.get("schema_version") != 1 or state.get("opt_in") is not True
            or state.get("safety_history") != "CLEAR"
            or digest(state["prompt"].encode("utf-8")) != state["prompt_sha256"]):
        raise ValueError("Journal binding invalid")
    if "browser_mode" in state and state["browser_mode"] != ("windows-isolated" if state.get("windows_isolated_browser", False) else "headless"):
        raise ValueError("Pinned browser mode changed")
    if state.get("oracle_config") is not None and (canonical_hash(state["oracle_config"]) != state.get("oracle_config_sha256")
            or state["oracle_config"] != runtime_config(state)):
        raise ValueError("Pinned private Oracle configuration changed")
    if state.get("project_binding") is not None or for_submission:
        verify_project_binding(state.get("project_binding"), current=for_submission)
    for ref in state["references"]:
        if image_binding(ref["path"]) != ref:
            raise ValueError("Reference changed")
    proof = state.get("scenario_evidence")
    if proof and digest(Path(proof["path"]).read_bytes()) != proof["sha256"]:
        raise ValueError("Scenario evidence changed")
    if proof:
        if for_submission:
            binding = proof["plan"]
            if digest(Path(binding["path"]).read_bytes()) != binding["sha256"]:
                raise ValueError("Live main reference plan changed; submission not authorized")
        guard = json.loads(Path(proof["guard"]["path"]).read_bytes())
        pin = proof["attempt"]
        attempt = next((a for a in guard.get("attempts", []) if a.get("attempt_id") == pin["attempt_id"]), None)
        if not attempt or attempt_binding(guard, attempt) != pin:
            raise ValueError("Attempt identity/revision/snapshot changed; cannot reconcile")
        snapshot = pin["snapshot"]
        native = proof.get("branch") == "NATIVE_DEFAULT"
        if native:
            attestation = json.loads(Path(proof["path"]).read_bytes())
            if (snapshot.get("execution_call", {}).get("prompt") !=
                    {"text": state["prompt"], "text_sha256": state["prompt_sha256"]}
                    or state["references"]
                    or authorized_native_attempt(guard, snapshot["execution_call"], attestation, active=False) != pin):
                raise ValueError("Pinned native execution changed")
        else:
            snapshot_path = Path(snapshot["reference_plan_snapshot_path"])
            if digest(snapshot_path.read_bytes()) != snapshot["reference_plan_snapshot_sha256"]:
                raise ValueError("Executed immutable plan snapshot changed")
        if for_submission:
            plan_path = Path(proof["plan"]["path"])
            attestation = json.loads(Path(proof["path"]).read_bytes())
            authorization = (authorized_native_attempt(guard, json.loads(plan_path.read_bytes()), attestation)
                if native else authorized_attempt(guard, json.loads(plan_path.read_bytes()), attestation, plan_path))
            if authorization != pin:
                raise ValueError("Live authorization differs from pinned attempt")
    if not proof and not (state.get("neutral_test") and state["prompt"] == NEUTRAL_PROMPT
                          and state["prompt_sha256"] == NEUTRAL_PROMPT_SHA256
                          and len(state["references"]) == 1
                          and state["references"][0]["sha256"] == NEUTRAL_FIXTURE_SHA256):
        raise ValueError("Missing scenario evidence")
    return state


def validate_result(state, result):
    if result.get("sessionId") != state.get("session_id"):
        raise ValueError("Oracle returned different session")
    images = result.get("images")
    if result.get("status") != "completed" or not isinstance(images, list) or len(images) != 1:
        raise ValueError("Exactly one completed generated image is required")
    item = images[0]
    actual = Path(item.get("path", "")).resolve()
    expected = Path(state["oracle_home"]) / "generated" / "result.png"
    if item.get("kind") != "image" or actual != expected.resolve():
        raise ValueError("Unexpected artifact path/kind; screenshots and text are not results")
    binding = image_binding(actual)
    if binding["sha256"] in {r["sha256"] for r in state["references"]}:
        raise ValueError("Downloaded input/reference is not generated output")
    return actual


def backend_events(payload, timeout):
    node = (payload.get("runtime_binding") or {}).get("node_path") or shutil.which("node")
    if not node:
        raise ValueError("Installed Node executable unavailable")
    backend = Path(__file__).with_name("chatgpt_web_image_backend.mjs")
    home = Path(payload["oracle_home"]); home.mkdir(exist_ok=True)
    project_url = (verify_project_binding(payload["project_binding"])
                   if payload.get("project_binding") else None)
    if payload["operation"] == "submit" and not project_url:
        raise ValueError("Dedicated project required before any browser submission")
    config = payload.get("oracle_config", runtime_config(payload))
    if config != runtime_config(payload):
        raise ValueError("Pinned runtime destination changed")
    config_path = home / "config.json"
    if config_path.exists() and json.loads(config_path.read_text()) != config:
        raise ValueError("Private Oracle runtime configuration changed")
    if not config_path.exists():
        save(config_path, config)
    lines = queue.Queue()
    with (home / "transport.log").open("ab") as log:
        child = subprocess.Popen([node, str(backend)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8", shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        def reader():
            for line in child.stdout:
                lines.put(line)
            lines.put(None)
        threading.Thread(target=reader, daemon=True).start()
        try:
            child.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n"); child.stdin.close()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    line = lines.get(timeout=max(.01, deadline - time.monotonic()))
                except queue.Empty:
                    raise TimeoutError("Oracle transport timeout; reconcile same session only")
                if line is None:
                    if child.wait() != 0:
                        raise ValueError("Oracle backend failed; see private transport.log")
                    break
                decoded = json.loads(line)
                receipt_name = (payload["diagnostic_receipt"] if payload["operation"] in {"recovery-status", "image-tool-inspect"} else
                    "recovery-result.json" if payload["operation"] == "recover" else
                    "receipt.json" if decoded.get("type") == "receipt" else "last-result.json")
                save(home / receipt_name, decoded)
                yield decoded
        finally:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=10)


def node_executable(path, state=None):
    """Resolve the Node binary pinned into this request's private runtime."""
    pinned = (state or {}).get("runtime_binding", {}).get("node_path")
    if pinned and Path(pinned).is_file():
        return str(Path(pinned).resolve())
    node = shutil.which("node")
    if not node:
        raise ValueError("Installed Node executable unavailable")
    return node


@exclusive
def execute(path, *, inspect=False, timeout=55, transport=backend_events):
    state = load(path, for_submission=not inspect)
    if not inspect and state.get("web_handoff"):
        try:
            from tools.chatgpt_web_image_handoff import validate_bound_handoff
        except ImportError:
            from chatgpt_web_image_handoff import validate_bound_handoff
        validate_bound_handoff(path, state)
    if state.get("runtime_binding"):
        try:
            from tools.web_image_setup import verify
        except ImportError:
            from web_image_setup import verify
        if verify() != state["runtime_binding"]:
            raise ValueError("Runtime binding changed")
    active = Path(path).resolve().parent / ".chatgpt-web-image-active.json"
    if not active.is_file() or json.loads(active.read_text(encoding="utf-8"))["journal"] != str(Path(path).resolve()):
        raise ValueError("Journal is not the active request binding")
    if inspect:
        if not state.get("session_id"):
            receipt_path = Path(state["oracle_home"]) / "receipt.json"
            if receipt_path.is_file():
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if (receipt.get("type") == "receipt" and receipt.get("prompt_sha256") == state["prompt_sha256"]
                        and isinstance(receipt.get("session_id"), str) and receipt["session_id"]):
                    state["session_id"] = receipt["session_id"]
                    event(state, "RUNNING", session_id=state["session_id"], recovered_durable_receipt=True)
                    save(path, state)
            if not state.get("session_id"):
                raise ValueError("No durable session receipt; UNKNOWN cannot be resubmitted")
        if state["status"] not in {"UNKNOWN", "RUNNING"}:
            return state
    else:
        if state["status"] != "PREPARED":
            raise ValueError("Single-use submission; reconcile only, never resend")
        if (Path(state["oracle_home"]) / "generated" / "result.png").exists():
            raise ValueError("Private Oracle output already exists; cannot overwrite")
        if Path(state["requested_output"]).exists():
            raise ValueError("Requested output already exists")
        operation_id = uuid.uuid4().hex
        state["submission_operation_id"] = operation_id
        event(state, "UNKNOWN", reason="Committed before possible provider submission",
              submission_operation_id=operation_id)
        save(path, state)
    payload = dict(state, operation="inspect" if inspect else "submit", wait_ms=min(45000, int(timeout*1000)-5000))
    try:
        for item in transport(payload, timeout):
            if item.get("type") == "receipt":
                sid = item.get("session_id")
                if not isinstance(sid, str) or not sid or state.get("session_id", sid) != sid:
                    raise ValueError("Invalid or changed session receipt")
                state["session_id"] = sid
                event(state, "RUNNING", session_id=sid)
                save(path, state)
            elif item.get("type") == "result":
                result = item["result"]
                if result.get("sessionId") != state.get("session_id"):
                    raise ValueError("Different Oracle session result")
                if result.get("status") in {"queued", "running"}:
                    event(state, "RUNNING", session_id=state["session_id"])
                elif result.get("status") == "completed":
                    actual = validate_result(state, result)
                    target = Path(state["requested_output"])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("xb") as dst, actual.open("rb") as src:
                        shutil.copyfileobj(src, dst); dst.flush(); os.fsync(dst.fileno())
                    state["output"] = image_binding(target)
                    event(state, "DOWNLOADED", output=state["output"], provider_image=image_binding(actual))
                else:
                    event(state, "PROVIDER_FAILED", provider_status=result.get("status"))
                save(path, state)
            else:
                raise ValueError("Unexpected backend event")
    except Exception as error:
        event(state, state["status"], transport_error=str(error))
        save(path, state)
        raise
    return state


@exclusive
def reconcile_pre_receipt_failure(path):
    """Restore one retry only when durable backend phases prove consult was never entered."""
    state = load(path)
    if state.get("status") != "UNKNOWN" or state.get("session_id"):
        raise ValueError("Only an UNKNOWN journal without a session can be reconciled")
    if state.get("pre_receipt_reconciliations", 0) >= 1:
        raise ValueError("Pre-receipt retry already consumed; UNKNOWN remains terminal")
    active = Path(path).resolve().parent / ".chatgpt-web-image-active.json"
    if not active.is_file() or json.loads(active.read_text(encoding="utf-8"))["journal"] != str(Path(path).resolve()):
        raise ValueError("Journal is not the active request binding")
    home = Path(state["oracle_home"])
    operation_id = state.get("submission_operation_id")
    if not isinstance(operation_id, str) or not re.fullmatch(r"[a-f0-9]{32}", operation_id):
        raise ValueError("Legacy UNKNOWN has no durable operation ID; no-send is unproven")
    phase_path = home / f"transport-phase-{operation_id}.jsonl"
    try:
        phases = [json.loads(line) for line in phase_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Durable preconnect evidence is missing or invalid; UNKNOWN remains consumed") from error
    if (len(phases) != 2
            or [row.get("phase") for row in phases] != ["CLIENT_CONNECTING", "CONNECT_FAILED"]
            or any(row.get("schema_version") != 1 or row.get("operation_id") != operation_id
                   or row.get("prompt_sha256") != state["prompt_sha256"] for row in phases)
            or phases[0].get("provider_call_started") is not False
            or phases[1].get("provider_call_started") is not False
            or phases[1].get("error_code") not in {"CONNECTION_CLOSED", "CONNECT_FAILED"}):
        raise ValueError("Phase history does not prove failure before the provider call; UNKNOWN remains consumed")
    if (state.get("session_id") or (home / "receipt.json").exists()
            or (home / "generated" / "result.png").exists()
            or Path(state["requested_output"]).exists()):
        raise ValueError("Session receipt or output exists; original UNKNOWN operation must be reconciled")
    state["pre_receipt_reconciliations"] = 1
    state["pre_receipt_reconciliation"] = {
        "operation_id": operation_id,
        "phase_path": str(phase_path.resolve()),
        "phase_sha256": digest(phase_path.read_bytes()),
        "evidence": [row["phase"] for row in phases],
        "provider_call_started": False,
    }
    state["status"] = "PREPARED"
    event(state, "PREPARED", action="PRE_RECEIPT_RECONCILED_NO_SEND",
          operation_id=operation_id, phase_sha256=state["pre_receipt_reconciliation"]["phase_sha256"])
    save(path, state)
    return state


@exclusive
def recover(path, *, timeout=55, transport=backend_events):
    state = load(path)
    if (state.get("windows_isolated_browser") is not True or os.name != "nt"
            or state.get("status") not in {"RUNNING", "UNKNOWN", "PROVIDER_FAILED"}
            or not re.fullmatch(r"[a-zA-Z0-9_-]+", state.get("session_id", ""))):
        raise ValueError("Original isolated Windows session required for recovery")
    active = Path(path).resolve().parent / ".chatgpt-web-image-active.json"
    if not active.is_file() or json.loads(active.read_text(encoding="utf-8"))["journal"] != str(Path(path).resolve()):
        raise ValueError("Journal is not the active request binding")
    if state.get("recovery_started"):
        raise ValueError("Single-use recovery already consumed; no repeated download")
    receipt = json.loads((Path(state["oracle_home"]) / "receipt.json").read_bytes())
    if (receipt.get("type") != "receipt" or receipt.get("session_id") != state["session_id"]
            or receipt.get("prompt_sha256") != state["prompt_sha256"]):
        raise ValueError("Original durable session receipt mismatch")
    meta_path = Path(state["oracle_home"]) / "sessions" / state["session_id"] / "meta.json"
    meta_raw = meta_path.read_bytes(); meta = json.loads(meta_raw)
    if (meta.get("id") != state["session_id"] or meta.get("options", {}).get("prompt") != state["prompt"]
            or meta.get("browser", {}).get("runtime", {}).get("promptSubmitted") is not True):
        raise ValueError("Original recovery metadata/prompt mismatch")
    if Path(state["requested_output"]).exists() or (Path(state["oracle_home"]) / "generated" / "result.png").exists():
        raise ValueError("Existing output cannot be replaced")
    state["recovery_started"] = True
    event(state, "UNKNOWN", reason="Original no-send recovery; original provider error preserved")
    save(path, state)
    payload = dict(state, operation="recover", recovery_meta_sha256=digest(meta_raw))
    try:
        for item in transport(payload, timeout):
            if item.get("type") != "result" or item["result"].get("sendOperations") != 0:
                raise ValueError("Invalid no-send recovery result")
            result = item["result"]
            evidence = result.get("recoveryEvidence", {})
            if (evidence.get("prompt_sha256") != state["prompt_sha256"]
                    or evidence.get("conversationId") != meta["browser"]["runtime"].get("conversationId")
                    or not evidence.get("userMessageId")):
                raise ValueError("Recovered original prompt identity missing")
            actual = validate_result(state, result)
            target = Path(state["requested_output"])
            with target.open("xb") as dst, actual.open("rb") as src:
                shutil.copyfileobj(src, dst); dst.flush(); os.fsync(dst.fileno())
            state["output"] = image_binding(target)
            state["recovery_evidence"] = evidence
            event(state, "DOWNLOADED", output=state["output"], recovery_evidence=evidence,
                  original_session_id=state["session_id"], cleanup=result.get("cleanup"))
            save(path, state)
    except Exception as error:
        event(state, "UNKNOWN", recovery_error=str(error)); save(path, state); raise
    return state


@exclusive
def recovery_status(path, *, timeout=55, transport=backend_events, operation="recovery-status", activate_native_image_tool=False, pinned_project_editor=False, native_mode_proof_only=False):
    if operation not in {"recovery-status", "image-tool-inspect"}:
        raise ValueError("Unsupported diagnostic operation")
    if activate_native_image_tool and operation != "image-tool-inspect":
        raise ValueError("Native mode probe requires image-tool-inspect")
    if native_mode_proof_only and (activate_native_image_tool or not pinned_project_editor):
        raise ValueError("Proof-only requires pinned project and excludes activation")
    if pinned_project_editor and (operation != "image-tool-inspect" or not (activate_native_image_tool or native_mode_proof_only)):
        raise ValueError("Pinned project inspection requires native diagnostic")
    state = load(path)
    if (state.get("windows_isolated_browser") is not True or os.name != "nt"
            or not re.fullmatch(r"[a-zA-Z0-9_-]+", state.get("session_id", ""))):
        raise ValueError("Original isolated Windows session required for diagnostic")
    active = Path(path).resolve().parent / ".chatgpt-web-image-active.json"
    if not active.is_file() or json.loads(active.read_text(encoding="utf-8"))["journal"] != str(Path(path).resolve()):
        raise ValueError("Journal is not the active request binding")
    home = Path(state["oracle_home"])
    receipt = json.loads((home / "receipt.json").read_bytes())
    if (receipt.get("session_id") != state["session_id"]
            or receipt.get("prompt_sha256") != state["prompt_sha256"]):
        raise ValueError("Original durable session receipt mismatch")
    meta_raw = (home / "sessions" / state["session_id"] / "meta.json").read_bytes()
    meta = json.loads(meta_raw)
    if meta.get("id") != state["session_id"] or meta.get("options", {}).get("prompt") != state["prompt"]:
        raise ValueError("Original diagnostic metadata/prompt mismatch")
    name = operation + "-" + uuid.uuid4().hex + ".json"
    payload = dict(state, operation=operation, recovery_meta_sha256=digest(meta_raw), diagnostic_receipt=name, activate_native_image_tool=activate_native_image_tool, pinned_project_editor=pinned_project_editor, native_mode_proof_only=native_mode_proof_only)
    try:
        for item in transport(payload, timeout):
            result = item.get("result", {})
            if (item.get("type") != "result" or result.get("sessionId") != state["session_id"]
                    or result.get("status") != ("diagnostic" if operation == "recovery-status" else "image-tool-diagnostic") or result.get("sendOperations") != 0
                    or result.get("imageCollectOperations") != 0
                    or result.get("conversationId") != meta["browser"]["runtime"].get("conversationId")
                    or (operation == "recovery-status" and len(result.get("snapshots", [])) != 3)
                    or (operation == "image-tool-inspect" and (result.get("menu", {}).get("selectionOperations") not in {0, 1, None} if activate_native_image_tool else result.get("menu", {}).get("selectionOperations") != 0))):
                raise ValueError("Invalid original-conversation diagnostic receipt")
            destination = home / name
            if not destination.exists():
                with destination.open("x", encoding="utf-8") as handle:
                    json.dump(item, handle, ensure_ascii=False, indent=2)
            return dict(result, diagnostic_receipt=str(destination))
        raise ValueError("No diagnostic result; original journal unchanged")
    except Exception as error:
        destination = home / name
        if not destination.exists():
            with destination.open("x", encoding="utf-8") as handle:
                json.dump({"type":"diagnostic-error", "sessionId":state["session_id"],
                    "conversationId":meta["browser"]["runtime"].get("conversationId"),
                    "sendOperations":0, "imageCollectOperations":0,
                    "error":str(error)}, handle, ensure_ascii=False, indent=2)
        raise



def live_status(path, *, timeout=55):
    """Observe only the original live target, without browser ownership changes."""
    state = load(path)
    if state.get("runtime_binding"):
        try:
            from tools.web_image_setup import verify
        except ModuleNotFoundError:
            from web_image_setup import verify
        if verify() != state["runtime_binding"]:
            raise ValueError("Runtime binding changed")
    command = [node_executable(path, state), str(Path(__file__).with_name("chatgpt_web_image_live_status.mjs")), str(Path(path).resolve())]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=min(timeout, 30), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode != 0:
        raise ValueError("Pure live observation failed: " + completed.stderr[-1024:])
    result = json.loads(completed.stdout)
    if (result.get("operation") != "live-status" or result.get("sendOperations") != 0
            or result.get("imageCollectOperations") != 0 or result.get("cleanup") != "observer-client-closed"):
        raise ValueError("Invalid pure live observation receipt")
    return result


def open_original_image(path, *, action_guard, timeout=55, inspect=False, preclick_reconciliation=None):
    state = load(path)
    if preclick_reconciliation:
        raise ValueError("Request-specific reconciliation artifacts are not supported")
    if state.get("runtime_binding"):
        try:
            from tools.web_image_setup import verify
        except ModuleNotFoundError:
            from web_image_setup import verify
        if verify() != state["runtime_binding"]:
            raise ValueError("Runtime binding changed")
    command = [node_executable(path, state), str(Path(__file__).with_name("chatgpt_web_image_native_download.mjs")), str(Path(path).resolve()), str(Path(action_guard).resolve()), "inspect-original-viewer" if inspect else "open-original-image"]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=min(timeout, 30), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode != 0:
        raise ValueError("Original native preview failed: " + completed.stderr[-1024:])
    result = json.loads(completed.stdout)
    if (result.get("operation") != ("inspect-original-viewer" if inspect else "open-original-image") or result.get("sendOperations") != 0
            or result.get("downloadOperations") != 0 or result.get("imageCollectOperations") != 0
            or result.get("cleanup") != "observer-client-closed"):
        raise ValueError("Invalid original native preview receipt")
    return result


@exclusive
def finalize_native_download(path, receipt_path, *, action_guard=None):
    state = load(path)
    home = Path(state["oracle_home"]).resolve()
    active = Path(path).resolve().parent / ".chatgpt-web-image-active.json"
    if not active.is_file() or json.loads(active.read_text(encoding="utf-8"))["journal"] != str(Path(path).resolve()):
        raise ValueError("Journal is not the active original binding")
    receipt_path = Path(receipt_path).resolve()
    if receipt_path.parent != home or not re.fullmatch(r"native-download-[0-9]+\.json", receipt_path.name):
        raise ValueError("Scoped native download receipt required")
    receipt_raw = receipt_path.read_bytes()
    result = json.loads(receipt_raw)
    if state.get("status") == "COMPLETED":
        if (state.get("native_download_receipt", {}).get("sha256") == digest(receipt_raw)
                and state.get("output") == image_binding(state["requested_output"])):
            return state
        raise ValueError("Different native completed artifact")
    meta_raw = (home / "sessions" / state["session_id"] / "meta.json").read_bytes()
    meta = json.loads(meta_raw)
    guard_path = Path(state["scenario_evidence"]["guard"]["path"])
    guard_raw = guard_path.read_bytes()
    if action_guard:
        action_path = Path(action_guard).resolve()
        if action_path.parent != Path(path).resolve().parent:
            raise ValueError("Native action guard outside active request")
        action_raw = action_path.read_bytes()
        action = json.loads(action_raw)
        guard = json.loads(guard_raw)
        if (action.get("request_id") != guard.get("request_id")
                or any(action.get("active_attempt", {}).get(key) != guard.get("active_attempt", {}).get(key)
                       for key in ("attempt_id", "status", "reference_binding", "stage", "task_revision", "execution_snapshot"))
                or action.get("status") != "ACTIVE"
                or action.get("locked_invariants", {}).get("send") != "ZERO"
                or action.get("locked_invariants", {}).get("cloudflare") != "NO_BYPASS"
                or not any(stage.get("id") == "REVIEW" and stage.get("status") == "COMPLETED" for stage in action.get("required_stages", []))):
            raise ValueError("Native action guard binding mismatch")
        guard_raw = action_raw
    if (state.get("status") not in {"RUNNING", "UNKNOWN", "PROVIDER_FAILED"}
            or result.get("operation") != "download-original-image"
            or result.get("sessionId") != state["session_id"]
            or result.get("conversationId") != meta["browser"]["runtime"]["conversationId"]
            or result.get("journalSha256") != digest(Path(path).read_bytes())
            or result.get("metaSha256") != digest(meta_raw)
            or result.get("receiptSha256") != digest((home / "receipt.json").read_bytes())
            or result.get("guardSha256") != digest(guard_raw)
            or result.get("sendOperations") != 0 or result.get("downloadOperations") != 1
            or result.get("cleanup") != "observer-client-closed"):
        raise ValueError("Original native download binding mismatch")
    download = result.get("download", {})
    if download.get("click", {}).get("clicked") is not True or download.get("click", {}).get("sourceMatchesPreview") is not True:
        raise ValueError("Original native click proof missing")
    original = Path(download["originalPath"]).resolve()
    requested = Path(state["requested_output"]).resolve()
    if (original.parent != home / "native-original-download"
            or Path(download["outputPath"]).resolve() != requested
            or len(list(original.parent.iterdir())) != 1):
        raise ValueError("Native original output scope mismatch")
    proof = download.get("downloadProof", {})
    source = Path(proof.get("viewerReceiptPath", "")).resolve()
    if source.parent != home or not re.fullmatch(r"native-download-viewer-[0-9]+\.json", source.name) or digest(source.read_bytes()) != proof.get("viewerReceiptSha256"):
        raise ValueError("Original viewer download chain mismatch")
    viewer_receipt = json.loads(source.read_text(encoding="utf-8"))
    viewer_hash = viewer_receipt.pop("sha256", None)
    meta_conversation = meta.get("browser", {}).get("runtime", {}).get("conversationId")
    if (viewer_receipt.get("operation") != "inspect-original-viewer"
            or viewer_receipt.get("sessionId") != state["session_id"]
            or viewer_receipt.get("conversationId") != meta_conversation
            or digest(json.dumps(viewer_receipt, ensure_ascii=False, separators=(",", ":")).encode()) != viewer_hash):
        raise ValueError("Original viewer receipt binding mismatch")
    with Image.open(original) as image:
        if image.format != "PNG" or min(image.size) < 1:
            raise ValueError("Original native artifact is not a valid PNG")
    original_binding = image_binding(original)
    output_binding = image_binding(requested)
    if original_binding["sha256"] != output_binding["sha256"] or output_binding["sha256"] != download.get("sha256"):
        raise ValueError("Original native PNG bytes mismatch")
    state["output"] = output_binding
    state["native_download_receipt"] = {"path": str(receipt_path), "sha256": digest(receipt_raw)}
    event(state, "COMPLETED", output=output_binding, provider_image=original_binding, native_download_receipt=state["native_download_receipt"], method="original-native-browser-download")
    save(path, state)
    return state


def download_original_image(path, *, action_guard, viewer_receipt, viewer_receipt_sha256, timeout=55):
    state = load(path)
    if state.get("runtime_binding"):
        try:
            from tools.web_image_setup import verify
        except ModuleNotFoundError:
            from web_image_setup import verify
        if verify() != state["runtime_binding"]:
            raise ValueError("Runtime binding changed")
    command = [node_executable(path, state), str(Path(__file__).with_name("chatgpt_web_image_native_download.mjs")), str(Path(path).resolve()), str(Path(action_guard).resolve()), "download-original-image", str(Path(viewer_receipt).resolve()), viewer_receipt_sha256]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=min(timeout, 30), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode != 0:
        raise ValueError("Original native Download failed: " + completed.stderr[-1024:])
    result = json.loads(completed.stdout)
    return finalize_native_download(path, result["diagnostic_receipt"], action_guard=action_guard)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("journal"); p.add_argument("--prompt-file", required=True)
    p.add_argument("--reference", action="append", default=[])
    p.add_argument("--model-strategy", required=True, choices=["current", "select"])
    p.add_argument("--oracle-root")
    p.add_argument("--model-label"); p.add_argument("--profile-dir")
    p.add_argument("--output", required=True); p.add_argument("--opt-in", action="store_true")
    p.add_argument("--safety-history", required=True, choices=["CLEAR"])
    p.add_argument("--windows-isolated-browser", action="store_true")
    p.add_argument("--evidence"); p.add_argument("--neutral-test", action="store_true")
    p = sub.add_parser("bind-project"); p.add_argument("journal")
    p.add_argument("--project-config", default=str(PROJECT_CONFIG))
    for name in ("run", "submit", "status", "result", "recover", "reconcile-pre-receipt-failure", "reconcile-desktop-proof-failure", "recovery-status", "image-tool-inspect", "live-status", "open-original-image", "inspect-original-viewer", "download-original-image", "finalize-native-download"):
        p = sub.add_parser(name); p.add_argument("journal")
        p.add_argument("--timeout", type=int, default=55, choices=range(10, 61))
        if name == "reconcile-desktop-proof-failure":
            p.add_argument("--manifest-snapshot",required=True)
            p.add_argument("--backend-snapshot",required=True)
        if name in {"open-original-image", "inspect-original-viewer", "download-original-image"}:
            p.add_argument("--action-guard", required=True)
            p.add_argument("--preclick-reconciliation")
        if name == "download-original-image":
            p.add_argument("--viewer-receipt", required=True)
            p.add_argument("--viewer-receipt-sha256", required=True)
        if name == "finalize-native-download":
            p.add_argument("--action-guard")
            p.add_argument("--download-receipt", required=True)
        if name == "image-tool-inspect":
            p.add_argument("--activate-native-image-tool", action="store_true")
            p.add_argument("--pinned-project-editor", action="store_true")
            p.add_argument("--native-mode-proof-only", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.journal, Path(args.prompt_file).read_text(encoding="utf-8"),
            args.reference, args.model_strategy, opt_in=args.opt_in,
            safety_history=args.safety_history, evidence_path=args.evidence,
            neutral_test=args.neutral_test, output=args.output,
            profile_dir=args.profile_dir, model_label=args.model_label, oracle_root=args.oracle_root, windows_isolated_browser=args.windows_isolated_browser)
    elif args.command == "bind-project":
        result = bind_project(args.journal, config_path=args.project_config)
    elif args.command in {"recovery-status", "image-tool-inspect"}:
        result = recovery_status(args.journal, timeout=args.timeout, operation=args.command, activate_native_image_tool=getattr(args, "activate_native_image_tool", False), pinned_project_editor=getattr(args, "pinned_project_editor", False), native_mode_proof_only=getattr(args, "native_mode_proof_only", False))
    elif args.command == "download-original-image":
        result = download_original_image(args.journal, action_guard=args.action_guard, viewer_receipt=args.viewer_receipt, viewer_receipt_sha256=args.viewer_receipt_sha256, timeout=args.timeout)
    elif args.command == "finalize-native-download":
        result = finalize_native_download(args.journal, args.download_receipt, action_guard=args.action_guard)
    elif args.command in {"open-original-image", "inspect-original-viewer"}:
        result = open_original_image(args.journal, action_guard=args.action_guard, timeout=args.timeout, inspect=args.command == "inspect-original-viewer", preclick_reconciliation=args.preclick_reconciliation)
    elif args.command == "live-status":
        result = live_status(args.journal, timeout=args.timeout)
    elif args.command == "recover":
        result = recover(args.journal, timeout=args.timeout)
    elif args.command == "reconcile-pre-receipt-failure":
        result = reconcile_pre_receipt_failure(args.journal)
    elif args.command == "reconcile-desktop-proof-failure":
        try:
            from tools import chatgpt_web_image_no_send as no_send
        except ImportError:
            import chatgpt_web_image_no_send as no_send
        result = no_send.record_no_send(__import__(__name__,fromlist=['']),args.journal,args.manifest_snapshot,args.backend_snapshot)
    else:
        result = execute(args.journal, inspect=args.command in {"status", "result"}, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
