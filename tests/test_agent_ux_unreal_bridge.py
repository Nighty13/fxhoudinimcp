"""The Unreal bridge tools (notes 4n #9-#10): reply unwrapping, engine lookup, busy editor errors."""

from __future__ import annotations

# Built-in
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

# Third-party
import httpx
import pytest

# Internal
from fxhoudinimcp.tools import unreal


def _reply(value) -> dict:
    return {"content": [{"type": "text", "text": json.dumps({"returnValue": value})}]}


class TestUnwrap:
    def test_toolset_json_string_is_decoded(self):
        assert unreal._unwrap(_reply(json.dumps({"num_outputs": 1}))) == {"num_outputs": 1}

    def test_plain_value_passes(self):
        assert unreal._unwrap(_reply("created")) == "created"

    def test_error_text_is_flagged(self):
        result = {"content": [{"type": "text", "text": "Toolset 'X' not found"}], "isError": True}
        assert unreal._unwrap(result) == {"text": "Toolset 'X' not found", "is_error": True}


class TestEngine:
    def test_ue_root_overrides(self, tmp_path, monkeypatch):
        project = tmp_path / "P.uproject"
        project.write_text(json.dumps({"EngineAssociation": "5.8"}))
        monkeypatch.setenv("UE_ROOT", str(tmp_path / "UE"))
        assert unreal.engine_root(project) == tmp_path / "UE"

    def test_editor_exe_path(self):
        assert unreal.editor_exe(Path("E")).parts[-3:-1] in (
            ("Binaries", "Win64"),
            ("Binaries", "Linux"),
            ("Binaries", "Mac"),
        )


class TestBusyEditor:
    def test_timeout_names_the_likely_cause(self):
        async def slow(*a, **k):
            raise httpx.ReadTimeout("slow")

        with patch.object(unreal, "_call_unreal", slow), pytest.raises(TimeoutError, match="busy"):
            asyncio.run(unreal.call_unreal("list_toolsets"))

    def test_no_editor_says_start_unreal(self):
        async def refused(*a, **k):
            raise httpx.ConnectError("refused")

        with (
            patch.object(unreal, "_call_unreal", refused),
            pytest.raises(ConnectionError, match="start_unreal"),
        ):
            asyncio.run(unreal.call_unreal("list_toolsets"))
