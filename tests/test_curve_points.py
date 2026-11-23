"""set_curve_points writes a Curve SOP's own points.

curve::2.0 has no point-list parameter: what is drawn lives in the hidden
geometry parameter savedstashgeo. A session pressed "Update From Input" and
then unwired the input, which left the curve empty, and fell back to probing
parms through execute_python. The handler writes the stash directly (or the
legacy curve's coords string) and refuses a curve with an input wired, whose
stash is then not what it outputs.

hou is mocked here; the live check ran on Houdini 22.0.508.
"""

from __future__ import annotations

# Built-in
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("hou", MagicMock())
sys.modules.setdefault("hdefereval", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "houdini", "scripts", "python"))

# Internal
import fxhoudinimcp_server.handlers.geometry_handlers as geometry  # noqa: E402


class _Parm:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


class _Geo:
    """Enough of hou.Geometry to record one polygon."""

    def __init__(self):
        self.positions = []
        self.closed = None

    def createPolygon(self):
        geo = self

        class _Poly:
            def setIsClosed(self, closed):
                geo.closed = closed

            def addVertex(self, point):
                pass

        return _Poly()

    def createPoint(self):
        geo = self

        class _Point:
            def setPosition(self, position):
                geo.positions.append(tuple(position))

        return _Point()


def _node(parms, inputs=()):
    node = MagicMock()
    node.parm.side_effect = lambda name: parms.get(name)
    node.inputs.return_value = list(inputs)
    node.path.return_value = "/obj/geo1/curve1"
    node.errors.return_value = []
    node.geometry.return_value = None
    return node


@pytest.fixture
def hou(monkeypatch):
    fake = MagicMock()
    fake.Geometry.side_effect = _Geo
    monkeypatch.setattr(geometry, "hou", fake)
    return fake


def test_curve_2_points_go_into_the_stash(hou):
    stash = _Parm()
    hou.node.return_value = _node({"savedstashgeo": stash})
    result = geometry._set_curve_points(
        node_path="/obj/geo1/curve1", points=[[0, 0, 0], [1, 0, 2], [3, 0, 4]], closed=True
    )
    assert result["method"] == "savedstashgeo"
    assert result["point_count"] == 3
    assert stash.value.positions == [(0.0, 0.0, 0.0), (1.0, 0.0, 2.0), (3.0, 0.0, 4.0)]
    assert stash.value.closed is True


def test_a_wired_input_is_refused(hou):
    hou.node.return_value = _node({"savedstashgeo": _Parm()}, inputs=[MagicMock()])
    with pytest.raises(ValueError, match="input wired"):
        geometry._set_curve_points(node_path="/obj/geo1/curve1", points=[[0, 0, 0], [1, 0, 0]])


def test_the_legacy_curve_takes_a_coords_string(hou):
    coords, close = _Parm(), _Parm()
    hou.node.return_value = _node({"coords": coords, "close": close})
    result = geometry._set_curve_points(
        node_path="/obj/geo1/curve1", points=[[0, 0, 0], [1.5, 2, -3]]
    )
    assert result["method"] == "coords"
    assert coords.value == "0.0,0.0,0.0 1.5,2.0,-3.0"
    assert close.value == 0


@pytest.mark.parametrize(
    "points, message",
    [
        ([[0, 0, 0]], "at least two"),
        ([[0, 0, 0], [1, 0]], r"points\[1\] must be \[x, y, z\]"),
        ([[0, 0, 0], [1, "a", 0]], r"points\[1\] must hold numbers"),
    ],
)
def test_bad_points_are_named(hou, points, message):
    hou.node.return_value = _node({"savedstashgeo": _Parm()})
    with pytest.raises(ValueError, match=message):
        geometry._set_curve_points(node_path="/obj/geo1/curve1", points=points)


def test_a_node_that_is_not_a_curve_is_refused(hou):
    hou.node.return_value = _node({})
    with pytest.raises(ValueError, match="not a Curve SOP"):
        geometry._set_curve_points(node_path="/obj/geo1/box1", points=[[0, 0, 0], [1, 0, 0]])
