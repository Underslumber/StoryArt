"""Keep synthetic requests independent of the developer's active Codex chat."""

import pytest


@pytest.fixture(autouse=True)
def isolated_chat_environment(monkeypatch, tmp_path):
    from tools import task_execution_guard

    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(task_execution_guard, "_chat_request_root", lambda: tmp_path / "chat-bindings")
