"""Agent-UX fixes from the terrain_road vegetation session (notes 4m).

- a wrangle's ch() parms that do not exist are reported by build_network;
- create_spare_parameters re-writes a wrangle's snippet so VEX sees the new parms;
- find_error_nodes / get_scene_summary leave locked assets' internals out;
- get_volume_info's compact table keeps the histogram it was asked for;
- start_houdini(gui=True) explains an exit with an empty log.

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
import fxhoudinimcp_server.handlers.geometry_handlers as geometry  # noqa: E402
import fxhoudinimcp_server.handlers.graph_handlers as graph  # noqa: E402
import fxhoudinimcp_server.handlers.viewport_handlers as viewport  # noqa: E402

###### ch() names in a snippet


class TestChParmNames:
    def test_every_ch_flavour_with_a_bare_name(self):
        code = (
            'float a = chf("road_clear");\nint s = chi( "seed" );\n'
            'vector c = chv(\'tint\');\nfloat r = chramp("falloff", 0.5);\nfloat b = ch("blend");'
        )
        assert graph.ch_parm_names(code) == ["road_clear", "seed", "tint", "falloff", "blend"]

    def test_paths_and_repeats_are_left_out(self):
        code = 'chf("../CTRL/x") + chf("/obj/geo1/y") + chf("k") + chf("k")'
        assert graph.ch_parm_names(code) == ["k"]

    def test_a_node_reports_only_names_it_lacks(self):
        node = MagicMock()
        snippet = MagicMock()
        snippet.unexpandedString.return_value = 'chf("have") + chf("missing")'
        parms = {"snippet": snippet, "have": object()}
        node.parm.side_effect = parms.get
        node.parmTuple.return_value = None
        assert graph._missing_ch_parms(node) == ["missing"]

    def test_a_spec_is_checked_against_its_type(self):
        group = MagicMock()
        group.find.side_effect = lambda name: object() if name == "class" else None
        node_type = MagicMock()
        node_type.parmTemplateGroup.return_value = group
        specs = [
            {
                "type": "attribwrangle",
                "name": "cull",
                "parms": {"snippet": 'chi("class")+chf("knob")'},
            },
            {"type": "null", "name": "OUT"},
        ]
        found = graph._spec_missing_ch_parms({"attribwrangle": node_type}, specs)
        assert found == {"cull": ["knob"]}


###### get_volume_info compact + bins


def test_compact_table_keeps_histogram_counts():
    entry = {
        "name": "humidity",
        "resolution": [300, 300, 1],
        "voxel_size": [2.0, 2.0, 2.0],
        "min_value": 0.0,
        "max_value": 1.0,
        "mean_value": 0.5,
        "voxel_stats": {
            "percentiles": {"50": 0.5, "95": 1.0},
            "histogram": [
                {"from": 0.0, "to": 0.5, "count": 3},
                {"from": 0.5, "to": 1.0, "count": 7},
            ],
        },
    }
    table = geometry._compact_volume_table([entry])
    assert table["columns"][-1] == "hist"
    assert table["rows"][0][-1] == [3, 7]


###### find_error_nodes does not enter locked assets


class _N:
    def __init__(self, path, errors=(), children=(), locked=False):
        self._path, self._errors, self._children, self._locked = (
            path,
            list(errors),
            list(children),
            locked,
        )

    def path(self):
        return self._path

    def type(self):
        t = MagicMock()
        t.name.return_value = "node"
        return t

    def errors(self):
        return self._errors

    def warnings(self):
        return []

    def children(self):
        return self._children

    def isLockedHDA(self):
        return self._locked


def _scene():
    inner = _N("/obj/geo/hf_noise/get_piece", errors=["Linked SOP isn't generating any pieces."])
    asset = _N("/obj/geo/hf_noise", children=[inner], locked=True)
    real = _N("/obj/geo/bad", errors=["Unknown function"])
    return _N("/obj", children=[_N("/obj/geo", children=[asset, real])])


def test_find_error_nodes_skips_locked_internals(monkeypatch):
    root = _scene()
    monkeypatch.setattr(viewport.hou, "node", lambda path: root)
    found = viewport.find_error_nodes("/obj")
    assert [n["path"] for n in found["error_nodes"]] == ["/obj/geo/bad"]
    assert found["error_count"] == 1
    assert found["locked_assets_not_entered"] == 1


def test_find_error_nodes_enters_locked_assets_on_request(monkeypatch):
    root = _scene()
    monkeypatch.setattr(viewport.hou, "node", lambda path: root)
    found = viewport.find_error_nodes("/obj", include_locked_assets=True)
    assert found["error_count"] == 2
