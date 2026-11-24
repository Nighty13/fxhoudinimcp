"""Fixes from the town-house stress test (notes/stress-test-townhouse.md).

- package help (SideFX Labs) is searched, and a package node's card reads its
  own page, never the core page of the same base name;
- the scene watcher no longer reports cook-driven parm changes, locked-asset
  internals or flag events that changed no flag;
- an Int menu parm takes its token (color's class "point");
- a scripted Copy to Points gets the Tab menu's target-attribute entries;
- open polygons (polylines) are counted in geometry evidence;
- verify_network lists only nodes with something to report.

hou is mocked here.
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
import fxhoudinimcp_server.handlers.help_handlers as help_handlers  # noqa: E402
import fxhoudinimcp_server.handlers.node_handlers as nodes  # noqa: E402
import fxhoudinimcp_server.handlers.parameter_handlers as parameters  # noqa: E402
import fxhoudinimcp_server.watch as watch  # noqa: E402

###### package help


def _page(path, title, body=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"= {title} =\n\n{body}\n")


def _help_layout(tmp_path, monkeypatch):
    core = tmp_path / "hfs" / "houdini" / "help"
    package = tmp_path / "labs"
    _page(str(core / "nodes" / "sop" / "filecache.txt"), "File Cache", "core cache page")
    _page(
        str(package / "help" / "nodes" / "sop" / "labs--edge_damage-2.1.txt"),
        "Labs Edge Damage",
        "edge wear",
    )
    _page(
        str(package / "help" / "nodes" / "sop" / "labs--filecache-2.0.txt"),
        "Labs File Cache",
        "labs cache page",
    )
    monkeypatch.setattr(help_handlers, "_help_dir", lambda: str(core))
    monkeypatch.setattr(
        help_handlers.hou,
        "houdiniPath",
        lambda: [str(package), str(tmp_path / "nohelp")],
        raising=False,
    )
    help_handlers._CACHE.clear()
    return core, package


class TestPackageHelp:
    def teardown_method(self):
        help_handlers._CACHE.clear()

    def test_a_package_help_folder_is_searched(self, tmp_path, monkeypatch):
        _help_layout(tmp_path, monkeypatch)
        found = help_handlers.search_help("edge wear")
        assert [hit["path"] for hit in found["results"]] == ["nodes/sop/labs--edge_damage-2.1"]
        page = help_handlers.get_help_page("nodes/sop/labs--edge_damage-2.1")
        assert page["title"] == "Labs Edge Damage"

    def test_the_install_page_wins_a_name_clash(self, tmp_path, monkeypatch):
        core, package = _help_layout(tmp_path, monkeypatch)
        _page(str(package / "help" / "nodes" / "sop" / "filecache.txt"), "Imposter")
        help_handlers._CACHE.clear()
        assert help_handlers.get_help_page("nodes/sop/filecache")["title"] == "File Cache"

    def test_help_file_stems(self):
        assert graph._help_file_stems("labs::edge_damage::2.1") == [
            "labs--edge_damage-2.1",
            "labs--edge_damage",
        ]
        assert graph._help_file_stems("labs::box_clip") == ["labs--box_clip"]
        assert graph._help_file_stems("scatter") == ["scatter"]

    def test_a_package_node_reads_its_own_page(self, tmp_path, monkeypatch):
        _help_layout(tmp_path, monkeypatch)
        node_type = MagicMock()
        node_type.name.return_value = "labs::filecache::2.0"
        assert "labs cache page" in graph._package_help(node_type, "Sop")

    def test_another_version_page_beats_none(self, tmp_path, monkeypatch):
        _help_layout(tmp_path, monkeypatch)
        node_type = MagicMock()
        node_type.name.return_value = "labs::edge_damage::3.0"
        assert "Labs Edge Damage" in graph._package_help(node_type, "Sop")

    def test_a_package_asset_never_reads_the_core_page(self, monkeypatch):
        node_type = MagicMock()
        node_type.name.return_value = "studio::filecache::1.0"
        node_type.embeddedHelp.return_value = ""
        monkeypatch.setattr(graph, "_package_help", lambda *a: None)
        monkeypatch.setattr(graph, "_ships_with_houdini", lambda t: False)
        assert graph._help_text(node_type, "Sop") is None


###### scene watcher


def _event(name):
    kind = MagicMock()
    kind.name.return_value = name
    return kind


class TestWatcherNoise:
    def setup_method(self):
        watch._changes.clear()
        watch._watched.clear()
        watch._flags.clear()
        watch._depth = 0

    def _node(self, path="/obj/house/grid"):
        node = MagicMock()
        node.path.return_value = path
        node.isInsideLockedHDA.return_value = False
        node.sessionId.return_value = 7
        return node

    def test_a_cook_driven_parm_is_not_an_edit(self):
        parm = MagicMock()
        parm.keyframes.return_value = [MagicMock()]  # an expression
        tuple_ = MagicMock()
        tuple_.__iter__.return_value = iter([parm])
        tuple_.name.return_value = "t"
        watch._on_event(_event("ParmTupleChanged"), node=self._node(), parm_tuple=tuple_)
        assert watch.take_changes() is None

    def test_a_literal_parm_edit_is_reported(self):
        parm = MagicMock()
        parm.keyframes.return_value = []
        tuple_ = MagicMock()
        tuple_.__iter__.return_value = iter([parm])
        tuple_.name.return_value = "rows"
        watch._on_event(_event("ParmTupleChanged"), node=self._node(), parm_tuple=tuple_)
        assert watch.take_changes()["nodes"] == {"/obj/house/grid": ["parm rows"]}

    def test_locked_asset_internals_are_ignored(self):
        node = self._node("/obj/house/tint/switch1")
        node.isInsideLockedHDA.return_value = True
        watch._on_event(_event("FlagChanged"), node=node)
        assert watch.take_changes() is None

    def test_a_flag_event_that_changed_no_flag_is_ignored(self):
        node = self._node()
        watch._flags["7"] = watch._flags_of(node)
        watch._on_event(_event("FlagChanged"), node=node)
        assert watch.take_changes() is None

    def test_a_real_flag_change_is_reported_once(self):
        node = self._node()
        node.isDisplayFlagSet.return_value = False
        watch._flags["7"] = watch._flags_of(node)
        node.isDisplayFlagSet.return_value = True
        watch._on_event(_event("FlagChanged"), node=node)
        watch._on_event(_event("FlagChanged"), node=node)
        assert watch.take_changes()["nodes"] == {"/obj/house/grid": ["flags"]}

    def test_a_locked_asset_is_watched_but_not_its_insides(self, monkeypatch):
        asset = self._node("/obj/house/building")
        asset.isLockedHDA.return_value = True
        inner = self._node("/obj/house/building/line3")
        asset.children.return_value = [inner]
        watched = []
        monkeypatch.setattr(watch, "_watch", watched.append)
        monkeypatch.setattr(watch.hou, "node", lambda path: asset)
        watch.end_call({"node_path": "/obj/house/building"})
        assert inner not in watched and asset in watched


###### Int menus, Tab defaults, open polygons


def test_a_radio_folder_takes_its_index_token():
    parm = MagicMock()
    parm.parmTemplate.return_value.type.return_value = parameters.hou.parmTemplateType.FolderSet
    assert parameters.int_menu_value(parm, "1") == 1
    assert parameters.int_menu_value(parm, 1) == 1


def test_int_menu_tokens_read_the_template():
    template = MagicMock()
    template.type.return_value = graph.hou.parmTemplateType.Int
    template.menuItems.return_value = ("detail", "primitive", "point", "vertex")
    node_type = MagicMock()
    node_type.parmTemplateGroup.return_value.find.return_value = template
    assert graph.int_menu_tokens(node_type, "class") == ["detail", "primitive", "point", "vertex"]
    assert graph.int_menu_tokens(None, "class") == []


def test_a_scripted_copy_to_points_gets_the_tab_entries():
    node = MagicMock()
    node.parm.side_effect = lambda name: {"targetattribs": count, "resettargetattribs": button}[
        name
    ]
    count, button = MagicMock(), MagicMock()
    count.eval.return_value = 0
    nodes.apply_tab_defaults(node)
    button.pressButton.assert_called_once()
    button.reset_mock()
    count.eval.return_value = 3
    nodes.apply_tab_defaults(node)
    button.pressButton.assert_not_called()


def test_a_node_without_target_attributes_is_left_alone():
    node = MagicMock()
    node.parm.return_value = None
    nodes.apply_tab_defaults(node)


def test_open_polygons_are_counted(monkeypatch):
    class Polygon:
        pass

    monkeypatch.setattr(geometry.hou, "Polygon", Polygon, raising=False)
    geo = MagicMock()
    geo.countPrimType.return_value = 5
    geo.globPrims.return_value = [Polygon(), Polygon(), object()]  # an open NURBS curve too
    assert geometry.open_polygon_count(geo) == 2
    geo.globPrims.assert_called_with("@intrinsic:closed==0")
    geo.countPrimType.return_value = 0
    assert geometry.open_polygon_count(geo) == 0


###### verify_network


def test_verify_lists_only_nodes_with_something_to_report(monkeypatch):
    parent = MagicMock()
    clean, flagged = MagicMock(), MagicMock()
    clean.path.return_value, flagged.path.return_value = "/obj/g/a", "/obj/g/b"
    clean.isDisplayFlagSet.return_value = False
    flagged.isDisplayFlagSet.return_value = True
    parent.children.return_value = [clean, flagged]
    parent.displayNode.return_value = flagged
    monkeypatch.setattr(graph.hou, "node", lambda path: parent)
    monkeypatch.setattr(graph, "_node_report", lambda n: {"path": n.path(), "type": "box"})
    monkeypatch.setattr(graph, "_has_errors", lambda n: False)
    monkeypatch.setattr(graph, "_collapse_inherited_errors", lambda c, r: [])
    monkeypatch.setattr(graph, "_geometry_summary", lambda n: {})
    monkeypatch.setattr(graph, "_has_snippet", lambda n: False)
    monkeypatch.setattr(graph, "_is_branch_end", lambda n, c: False)
    monkeypatch.setattr(graph, "license_error", lambda errors: None)
    result = graph.verify_network("/obj/g")
    assert [r["path"] for r in result["nodes"]] == ["/obj/g/b"]
    assert result["clean_nodes"] == 1
    full = graph.verify_network("/obj/g", all_nodes=True)
    assert len(full["nodes"]) == 2 and "clean_nodes" not in full


###### heavy geometry, stats on a second output


def _sop(path, prims, inputs=()):
    node = MagicMock()
    node.path.return_value = path
    node.type.return_value.category.return_value.name.return_value = "Sop"
    node.needsToCook.return_value = False
    node.geometry.return_value.intrinsicValue.return_value = prims
    node.inputs.return_value = list(inputs)
    return node


def test_a_node_that_multiplies_its_input_is_named():
    module = _sop("/obj/b/wall_geo", 6)
    damage = _sop("/obj/b/wall_damage", 240_000, [module])
    report = graph.heavy_geometry([module, damage], {"prims": 4_933_741})
    assert report["heavy_nodes"] == [
        {"path": "/obj/b/wall_damage", "prims": 240_000, "largest_input_prims": 6}
    ]
    assert "4,933,741" in report["heavy_note"]


def test_light_or_proportional_geometry_says_nothing():
    grid = _sop("/obj/t/grid", 200_000)
    noise = _sop("/obj/t/noise", 200_000, [grid])
    assert graph.heavy_geometry([grid, noise], {"prims": 200_000}) is None


def test_a_node_that_would_have_to_cook_is_not_cooked():
    node = _sop("/obj/b/x", 10**7)
    node.needsToCook.return_value = True
    assert graph.heavy_geometry([node], {"prims": 10}) is None
    node.geometry.assert_not_called()


def test_attrib_stats_reads_the_output_it_is_asked_for(monkeypatch):
    seen = []
    monkeypatch.setattr(
        geometry, "_attrib_stats_once", lambda path, *a, **kw: seen.append(kw) or {"ok": True}
    )
    geometry._get_attrib_stats(node_path="/obj/b/building", output_index=1)
    assert seen[-1]["output_index"] == 1
    geometry._get_attrib_stats(node_path="/obj/b/building")
    assert "output_index" not in seen[-1]


###### native menu callbacks, wrangle inputs


def _menu_parm(callback=""):
    parm = MagicMock()
    template = parm.parmTemplate.return_value
    template.menuItems.return_value = ("area", "perimeter")
    template.type.return_value = "Menu"
    template.scriptCallback.return_value = callback
    return parm


def test_a_native_menu_callback_runs_after_a_write():
    node = MagicMock()
    node.type.return_value.definition.return_value = None
    parm = _menu_parm()
    node.parm.return_value = parm
    assert graph.native_menu_callback(node, "measure") is True
    parm.pressButton.assert_called_once()


def test_an_asset_or_scripted_callback_is_left_alone():
    asset = MagicMock()
    asset.type.return_value.definition.return_value = object()
    asset.parm.return_value = _menu_parm()
    assert graph.native_menu_callback(asset, "mode") is False
    scripted = MagicMock()
    scripted.type.return_value.definition.return_value = None
    scripted.parm.return_value = _menu_parm(callback="hou.pwd()...")
    assert graph.native_menu_callback(scripted, "mode") is False
    scripted.parm.return_value.pressButton.assert_not_called()


def test_a_wrangle_is_wired_before_its_code_is_checked(monkeypatch):
    import fxhoudinimcp_server.handlers.vex_handlers as vex

    parent, node, upstream = MagicMock(), MagicMock(), MagicMock()
    upstream.path.return_value = "/obj/t/ROAD_CURVE"
    parent.node.return_value = upstream
    parent.createNode.return_value = node
    node.parent.return_value = None
    monkeypatch.setattr(vex.hou, "node", lambda path: parent)
    monkeypatch.setattr(vex, "_resolve_class_value", lambda n, r: 0)
    monkeypatch.setattr(vex, "_focus_network_editor", lambda n: None)
    monkeypatch.setattr(vex, "_validate_vex_quick", lambda n: {"vex_valid": True})
    result = vex.create_wrangle("/obj/t", "f@a = 1;", inputs=["ROAD_CURVE"])
    node.setInput.assert_called_once_with(0, upstream)
    assert result["inputs"] == ["/obj/t/ROAD_CURVE"]
