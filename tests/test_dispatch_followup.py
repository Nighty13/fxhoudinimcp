"""A follow-up runs in a second main-thread call after its command succeeds.

Houdini repaints a pane only once its event loop runs again, so a network
editor capture taken in the call that framed the node showed the previous
framing (Houdini 22.0.508). register_followup lets a command finish in a later
main-thread call, after the redraw.
"""

from __future__ import annotations

# Built-in
import os
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("hou", MagicMock())
sys.modules.setdefault("hdefereval", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "houdini", "scripts", "python"))

# Internal
import fxhoudinimcp_server.dispatcher as dispatcher  # noqa: E402


def test_the_followup_replaces_the_data_in_a_separate_main_thread_call(monkeypatch):
    calls = []

    def on_main(fn):
        calls.append(fn)
        return fn()

    monkeypatch.setattr(dispatcher.time, "sleep", lambda seconds: calls.append(seconds))
    monkeypatch.setitem(
        dispatcher._followup_registry,
        "test.cmd",
        (lambda data, params: {**data, "b": params["x"]}, 0.2),
    )
    result = dispatcher._run_followup(
        "test.cmd", {"x": 2}, {"status": "success", "data": {"a": 1}}, on_main
    )
    assert result == {"status": "success", "data": {"a": 1, "b": 2}}
    assert calls[0] == 0.2  # the main thread is left idle first
    assert callable(calls[1])  # then the follow-up is marshalled to it


def test_a_failed_command_skips_its_followup(monkeypatch):
    followup = MagicMock()
    monkeypatch.setitem(dispatcher._followup_registry, "test.cmd", (followup, 0.0))
    error = {"status": "error", "error": {"code": "X", "message": "no"}}
    assert dispatcher._run_followup("test.cmd", {}, error, None) is error
    followup.assert_not_called()


def test_a_followup_error_is_reported_not_raised(monkeypatch):
    def boom(data, params):
        raise RuntimeError("grab failed")

    monkeypatch.setitem(dispatcher._followup_registry, "test.cmd", (boom, 0.0))
    result = dispatcher._run_followup("test.cmd", {}, {"status": "success", "data": {}}, None)
    assert result["status"] == "error"
    assert result["error"]["code"] == "RuntimeError"


def test_a_command_without_followup_is_untouched():
    result = {"status": "success", "data": {"a": 1}}
    assert dispatcher._run_followup("test.none", {}, result, None) is result


def test_the_network_editor_capture_registers_one():
    import fxhoudinimcp_server.handlers.viewport_handlers  # noqa: F401

    assert "viewport.capture_network_editor" in dispatcher._followup_registry
