"""Agent-UX fixes from the Houdini <=> Unreal stress test (notes 4o): discovery and argument spelling.

- list_node_types browses the TAB menu (menus_only / menu);
- a synonym argument (code for vex_code) is renamed, not refused;
- new_scene / load_scene warn when the scene sits in Houdini's install folder.

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
import fxhoudinimcp_server.handlers.node_handlers as nodes  # noqa: E402

from fxhoudinimcp._sdk import apply_synonyms  # noqa: E402
from fxhoudinimcp.tools import scene  # noqa: E402


def _type(label, submenus=None):
    node_type = MagicMock()
    node_type.hidden.return_value = False
    node_type.description.return_value = label
    if submenus is None:
        node_type.definition.return_value = None
    else:
        section = MagicMock()
        section.contents.return_value = "".join(f"<toolSubmenu>{s}</toolSubmenu>" for s in submenus)
        node_type.definition.return_value.sections.return_value = {"Tools.shelf": section}
    return node_type


def _category(monkeypatch, types):
    category = MagicMock()
    category.name.return_value = "Sop"
    category.nodeTypes.return_value = types
    monkeypatch.setattr(nodes.hou, "nodeTypeCategories", lambda: {"Sop": category})
    monkeypatch.setattr(nodes.hou.shelves, "tools", lambda: {})
    nodes._MENU_CACHE.clear()
    return category


class TestTabMenus:
    TYPES = {
        "labs::tree_controller": ("Labs Tree Controller", ["Labs/World Building/Vegetation"]),
        "labs::road_generator": ("Labs Road Generator", ["Labs/World Building/Road"]),
        "heightfield_noise": ("HeightField Noise", ["Environments/Terrain"]),
        "box": ("Box", None),
    }

    def _types(self):
        return {name: _type(label, menus) for name, (label, menus) in self.TYPES.items()}

    def test_menus_only_returns_the_tree_with_counts(self, monkeypatch):
        _category(monkeypatch, self._types())
        out = nodes.list_node_types("Sop", menus_only=True)
        tree = {row["menu"]: row["node_count"] for row in out["menus"]}
        assert tree["Labs"] == 2
        assert tree["Labs/World Building/Road"] == 1
        assert tree["(no TAB submenu)"] == 1

    def test_menus_only_narrows_to_a_branch(self, monkeypatch):
        _category(monkeypatch, self._types())
        out = nodes.list_node_types("Sop", menus_only=True, menu="labs/world building")
        assert [r["menu"] for r in out["menus"]] == [
            "Labs/World Building",
            "Labs/World Building/Road",
            "Labs/World Building/Vegetation",
        ]

    def test_menu_lists_the_nodes_of_a_branch(self, monkeypatch):
        _category(monkeypatch, self._types())
        out = nodes.list_node_types("Sop", menu="Labs")
        assert [t["name"] for t in out["types"]] == [
            "labs::road_generator",
            "labs::tree_controller",
        ]
        assert out["types"][0]["menu"] == ["Labs/World Building/Road"]

    def test_html_entities_in_submenus_are_decoded(self, monkeypatch):
        _category(monkeypatch, {"labs::mocap": _type("Mocap", ["Labs/Character &amp; Animation"])})
        out = nodes.list_node_types("Sop", menu="Labs/Character & Animation")
        assert out["total_count"] == 1


class TestSynonyms:
    def test_code_becomes_vex_code(self):
        assert apply_synonyms({"node_path": "/n", "code": "x"}, {"node_path", "vex_code"}) == {
            "node_path": "/n",
            "vex_code": "x",
        }

    def test_an_accepted_name_is_left_alone(self):
        assert apply_synonyms({"code": "x"}, {"code", "vex_code"}) == {"code": "x"}

    def test_no_rename_when_the_target_is_also_given(self):
        data = {"code": "a", "vex_code": "b"}
        assert apply_synonyms(data, {"vex_code"}) == data


class TestSceneHipWarning:
    def test_new_scene_reply_carries_the_warning(self):
        hip = "C:/Program Files/Side Effects Software/Houdini 22.0.508/bin/untitled.hip"
        assert "hip_warning" in scene._with_hip_warning({"hip_file": hip})

    def test_project_scene_reply_is_unchanged(self):
        assert scene._with_hip_warning({"hip_file": "D:/p/p.hip"}) == {"hip_file": "D:/p/p.hip"}
