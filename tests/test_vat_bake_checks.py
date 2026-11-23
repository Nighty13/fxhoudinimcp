"""Fixes from the VAT wall test: zero-filled bakes, BOM code, huge returned strings.

A Labs VAT bake with internal cooking off wrote all-zero textures with no error,
and start_render called the ROP "unverified" because its outputs live in
path_pos / path_rot / path_col. hou and OpenImageIO are mocked here; the live
check ran on Houdini 22.0.508.
"""

from __future__ import annotations

# Built-in
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("hou", MagicMock())
sys.modules.setdefault("hdefereval", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "houdini", "scripts", "python"))

# Internal
import fxhoudinimcp_server.handlers.code_handlers as code  # noqa: E402
import fxhoudinimcp_server.outputs as outputs  # noqa: E402


def test_execute_python_strips_bom():
    result = code._execute_python("\ufeffx = 1", return_expression="x")
    assert result["success"] is True
    assert result["return_value"] == 1


def test_nested_long_strings_are_capped_top_level_kept():
    long = "y" * 5000
    assert code._serialize_result(long) == long
    capped = code._serialize_result({"mat": long})["mat"]
    assert len(capped) < 2100
    assert capped.endswith("chars]")


class _Parm:
    def __init__(self, name, value):
        self._name, self._value = name, value

    def name(self):
        return self._name

    def eval(self):
        return self._value

    def isHidden(self):
        return False


class _Rop:
    def __init__(self, parms):
        self._parms = parms

    def type(self):
        return types.SimpleNamespace(category=lambda: types.SimpleNamespace(name=lambda: "Driver"))

    def parm(self, name):
        return None

    def parms(self):
        return self._parms


@pytest.fixture
def vat_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(outputs, "_is_file_parm", lambda p: True)
    (tmp_path / "pos.exr").write_bytes(b"x")
    return tmp_path


def test_unknown_rop_outputs_come_from_its_file_parms(vat_dir):
    rop = _Rop(
        [
            _Parm("path_pos", str(vat_dir / "pos.exr")),
            _Parm("path_pos2", str(vat_dir / "pos2.exr")),
            _Parm("exportpath", str(vat_dir)),
            _Parm("prerender", ""),
        ]
    )
    found = outputs.reported_outputs(rop)
    assert [e["parm"] for e in found] == ["path_pos"]


def test_nothing_written_lists_every_named_file(vat_dir):
    rop = _Rop(
        [_Parm("path_rot", str(vat_dir / "rot.exr")), _Parm("path_col", str(vat_dir / "col.exr"))]
    )
    assert [e["exists"] for e in outputs.reported_outputs(rop)] == [False, False]


def _fake_oiio(monkeypatch, lo, hi):
    stats = types.SimpleNamespace(min=lo, max=hi)
    fake = types.SimpleNamespace(
        ImageBuf=lambda p: p, ImageBufAlgo=types.SimpleNamespace(computePixelStats=lambda b: stats)
    )
    monkeypatch.setitem(sys.modules, "OpenImageIO", fake)


def test_all_zero_image_is_flagged(monkeypatch):
    _fake_oiio(monkeypatch, [0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0])
    after = [{"path": "C:/t/pos.exr", "exists": True, "mtime": 2.0}]
    assert outputs.flat_images([], after) == ["C:/t/pos.exr"]


def test_real_image_and_untouched_files_pass(monkeypatch):
    _fake_oiio(monkeypatch, [-3.0, 0.0], [10.0, 0.75])
    after = [
        {"path": "C:/t/pos.exr", "exists": True, "mtime": 2.0},
        {"path": "C:/t/mesh.fbx", "exists": True, "mtime": 2.0},
    ]
    assert outputs.flat_images([], after) == []
    _fake_oiio(monkeypatch, [0.0], [0.0])
    same = [{"path": "C:/t/old.exr", "exists": True, "mtime": 1.0}]
    assert outputs.flat_images(same, same) == []
