"""Agent-UX fixes from the stylized cliff session (notes 4n): bringing Houdini up.

- a Houdini started from a Git Bash shell gets no HOME, so it reads Documents prefs;
- list_projects / new_project / open_project work from a projects root;
- the connection status warns when the scene sits in Houdini's install folder.
"""

from __future__ import annotations

# Built-in
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

# Internal
from fxhoudinimcp.tools import scene, session


def _run(coro):
    return asyncio.run(coro)


def _fn(tool):
    """The plain coroutine behind an @mcp.tool(), whatever the SDK wraps it in."""
    return getattr(tool, "fn", tool)


###### HOME


class TestHoudiniEnv:
    def test_home_is_dropped_on_windows(self):
        with (
            patch.object(session.sys, "platform", "win32"),
            patch.dict(os.environ, {"HOME": "C:/Users/me"}, clear=False),
        ):
            os.environ.pop("HOUDINI_USER_PREF_DIR", None)
            env, note = session.houdini_env()
        assert "HOME" not in env and "HOME" in note

    def test_an_explicit_pref_dir_keeps_home(self):
        with (
            patch.object(session.sys, "platform", "win32"),
            patch.dict(os.environ, {"HOME": "C:/Users/me", "HOUDINI_USER_PREF_DIR": "D:/prefs"}),
        ):
            env, note = session.houdini_env()
        assert env["HOME"] == "C:/Users/me" and note is None

    def test_other_platforms_are_untouched(self):
        with (
            patch.object(session.sys, "platform", "linux"),
            patch.dict(os.environ, {"HOME": "/home/me"}),
        ):
            env, note = session.houdini_env()
        assert env["HOME"] == "/home/me" and note is None


###### projects


def _bridge():
    bridge = MagicMock()
    bridge.execute = AsyncMock(return_value={"hip_file": "x"})
    return bridge


class TestProjects:
    def test_list_finds_folders_with_a_hip(self, tmp_path):
        (tmp_path / "cliff" / "hda").mkdir(parents=True)
        (tmp_path / "cliff" / "cliff.hip").write_text("")
        (tmp_path / "cliff" / "hda" / "cliff.hda").write_text("")
        (tmp_path / "empty").mkdir()
        out = _run(_fn(session.list_projects)(MagicMock(), root=str(tmp_path)))
        assert [p["name"] for p in out["projects"]] == ["cliff"]
        assert out["projects"][0]["hda_files"] == ["cliff.hda"]

    def test_new_project_makes_folders_sets_job_and_saves(self, tmp_path):
        bridge = _bridge()
        with patch.object(session, "_get_bridge", return_value=bridge):
            out = _run(_fn(session.new_project)(MagicMock(), "rocks", root=str(tmp_path)))
        assert all((tmp_path / "rocks" / sub).is_dir() for sub in session.PROJECT_FOLDERS)
        commands = [c.args[0] for c in bridge.execute.call_args_list]
        assert commands == ["code.execute_hscript", "scene.save_scene"]
        assert bridge.execute.call_args_list[1].args[1]["file_path"].endswith("rocks/rocks.hip")
        assert out["job"].endswith("/rocks")

    def test_new_project_refuses_an_existing_one(self, tmp_path):
        (tmp_path / "rocks").mkdir()
        (tmp_path / "rocks" / "rocks.hip").write_text("")
        with patch.object(session, "_get_bridge", return_value=_bridge()):
            try:
                _run(_fn(session.new_project)(MagicMock(), "rocks", root=str(tmp_path)))
            except ValueError as exc:
                assert "open_project" in str(exc)
            else:
                raise AssertionError("an existing project was overwritten")

    def test_open_project_by_unique_substring(self, tmp_path):
        for name in ("river_terrain", "terrain_road"):
            (tmp_path / name).mkdir()
            (tmp_path / name / f"{name}.hip").write_text("")
        bridge = _bridge()
        with patch.object(session, "_get_bridge", return_value=bridge):
            out = _run(_fn(session.open_project)(MagicMock(), "river", root=str(tmp_path)))
            assert out["opened"] == "river_terrain.hip"
            try:
                _run(_fn(session.open_project)(MagicMock(), "terrain", root=str(tmp_path)))
            except ValueError as exc:
                assert "More than one" in str(exc)
            else:
                raise AssertionError("an ambiguous name was opened")


###### install-folder scene


class TestInstallDirHip:
    def test_fresh_session_is_flagged(self):
        hip = "C:/Program Files/Side Effects Software/Houdini 22.0.508/bin/untitled.hip"
        assert "new_project" in scene.install_dir_hip_warning(hip)

    def test_project_scene_is_not(self):
        assert scene.install_dir_hip_warning("D:/work/cliff/cliff.hip") is None
        assert scene.install_dir_hip_warning(None) is None
