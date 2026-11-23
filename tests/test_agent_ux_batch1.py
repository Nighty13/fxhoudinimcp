"""Agent-UX batch 1: fixes found while driving Houdini 22.0.508 through the MCP.

- verify_network reports a failure once, at its root (one VEX typo was listed on
  21 nodes, ~15k chars);
- compare_volumes samples inside a heightfield's one-voxel-thick box (it read 0);
- parm_filter accepts `a|b` alternatives;
- the dispatcher names arguments a handler silently ignored.

hou is mocked here; the live checks ran on Houdini 22.0.508.
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
import fxhoudinimcp_server.handlers.geometry_handlers as geometry  # noqa: E402
import fxhoudinimcp_server.handlers.graph_handlers as graph  # noqa: E402


class _Node:
    def __init__(self, path, inputs=()):
        self._path = path
        self._inputs = list(inputs)

    def path(self):
        return self._path

    def inputs(self):
        return self._inputs


def _chain():
    """a (root VEX error) -> b -> c, plus d with its own unrelated error fed by b."""
    a = _Node("/obj/g/a")
    b = _Node("/obj/g/b", [a])
    c = _Node("/obj/g/c", [b])
    d = _Node("/obj/g/d", [b])
    reports = [
        {"path": a.path(), "errors": ["Ambiguous call to binary operator '<'"]},
        {
            "path": b.path(),
            "errors": ["Invalid source /obj/g/a/attribvop1. (Error: Ambiguous ...)"],
        },
        {"path": c.path(), "errors": ["Invalid source /obj/g/b ..."]},
        {"path": d.path(), "errors": ["Invalid source /obj/g/b", "Missing file: rock.bgeo"]},
    ]
    return [a, b, c, d], reports


class TestRootErrors:
    def test_a_failure_is_reported_once_at_its_root(self):
        children, reports = _chain()
        roots = graph._collapse_inherited_errors(children, reports)
        root_paths = [r["path"] for r in roots]
        assert "/obj/g/a" in root_paths
        by = {r["path"]: r for r in reports}
        assert by["/obj/g/b"]["blocked_by"] == ["/obj/g/a"]
        assert by["/obj/g/c"]["blocked_by"] == ["/obj/g/a"]
        assert "errors" not in by["/obj/g/c"]

    def test_a_node_with_its_own_error_stays_a_root(self):
        children, reports = _chain()
        roots = graph._collapse_inherited_errors(children, reports)
        assert "/obj/g/d" in [r["path"] for r in roots]
        assert "Missing file: rock.bgeo" in {r["path"]: r for r in reports}["/obj/g/d"]["errors"]

    def test_no_errors_no_roots(self):
        a = _Node("/obj/g/a")
        assert graph._collapse_inherited_errors([a], [{"path": a.path()}]) == []


class TestParmFilter:
    def test_alternatives(self):
        assert graph._filter_matches("piece|pack", "useidattrib", "Piece Attribute")
        assert graph._filter_matches("piece|pack", "pack", "Pack and Instance")
        assert not graph._filter_matches("piece|pack", "pivot", "Pivot Location")

    def test_plain_substring_still_works(self):
        assert graph._filter_matches("amp", "amp", "Amplitude")


class TestGridPositionsInsideThinBoxes:
    def test_heightfield_samples_stay_inside_the_slab(self):
        prim = MagicMock()
        box = MagicMock()
        box.sizevec.return_value = (2017.0, 1.0, 2017.0)
        box.minvec.return_value = (-1008.5, -0.5, -1008.5)
        prim.boundingBox.return_value = box
        prim.intrinsicValue.return_value = (1.0, 1.0, 1.0)
        positions = geometry._grid_positions(prim)
        ys = {round(p[1], 6) for p in positions}
        assert ys == {0.0}  # centre of the one-voxel slab, not 31.5 above it
        assert len(positions) <= 30000
        xs = [p[0] for p in positions]
        assert min(xs) > -1008.5 and max(xs) < 1008.5


class TestIgnoredArguments:
    def test_unknown_names_are_reported(self):
        def handler(node_path, parm_name=None, filter=None, **_):
            return {}

        ignored = dispatcher._ignored_arguments(handler, {"node_path": "/obj", "parm_names": ["a"]})
        assert ignored == ["parm_names"]

    def test_declared_names_are_not(self):
        def handler(node_path, **_):
            return {}

        assert dispatcher._ignored_arguments(handler, {"node_path": "/obj"}) == []
