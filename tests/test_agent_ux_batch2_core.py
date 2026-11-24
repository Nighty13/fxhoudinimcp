"""Agent-UX batch 2, core: errors, build/verify cooking, scans, overview, snapshots.

- a tool error keeps its message (mcp 2.x reduced anything not a ToolError to
  "Error executing tool X");
- build_network compiles snippets (dry run) and cooks side branches (build);
  verify_network names never-cooked wrangles instead of calling them healthy;
- get_node_errors_detailed skips locked-HDA internals and dedupes messages;
- list_node_types lists the newest version of a type only;
- tidy_network's layout default follows FXHOUDINIMCP_AUTO_LAYOUT;
- heightfield_erode no longer triggers the simulation_cache note;
- get_network_overview(detail="compact") is the text graph only;
- compare_snapshots(action="restore") writes a scoped snapshot back;
- cook_frame_range(reset=True) finds the simulations upstream (resetsim too);
- get_node_card's help is a summary plus section names;
- the scene watcher reports edits made outside MCP calls;
- a placed node is shifted off a sibling it would cover.

hou is mocked here.
"""

from __future__ import annotations

# Built-in
import os
import sys
from unittest.mock import MagicMock

# Third-party
import pytest

sys.modules.setdefault("hou", MagicMock())
sys.modules.setdefault("hdefereval", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "houdini", "scripts", "python"))

# Internal
import fxhoudinimcp_server.config as server_config  # noqa: E402
import fxhoudinimcp_server.handlers.context_handlers as context  # noqa: E402
import fxhoudinimcp_server.handlers.dop_handlers as dops  # noqa: E402
import fxhoudinimcp_server.handlers.graph_handlers as graph  # noqa: E402
import fxhoudinimcp_server.handlers.node_handlers as nodes  # noqa: E402
import fxhoudinimcp_server.watch as watch  # noqa: E402

from fxhoudinimcp._sdk import ToolError, tool_error_from  # noqa: E402
from fxhoudinimcp.errors import FXHoudiniError, HoudiniCommandError  # noqa: E402

###### fakes


class _Type:
    def __init__(self, name, components=None):
        self._name = name
        self._components = components

    def name(self):
        return self._name

    def nameComponents(self):
        if self._components is None:
            raise RuntimeError("no components")
        return self._components


class _Node:
    def __init__(self, path, type_name="null", inputs=(), snippet=None, needs_cook=True):
        self._path = path
        self._type = _Type(type_name)
        self._inputs = list(inputs)
        self._outputs = []
        self._snippet = snippet
        self.needs_cook = needs_cook
        self.cooked = 0
        self.errors_list = []
        for i in self._inputs:
            i._outputs.append(self)

    def path(self):
        return self._path

    def name(self):
        return self._path.rsplit("/", 1)[-1]

    def type(self):
        return self._type

    def inputs(self):
        return self._inputs

    def outputs(self):
        return self._outputs

    def parm(self, name):
        if name == "snippet" and self._snippet is not None:
            return MagicMock()
        return None

    def needsToCook(self):
        return self.needs_cook

    def cook(self, force=False):
        self.cooked += 1
        self.needs_cook = False

    def errors(self):
        return self.errors_list


###### errors reach the model


class TestToolErrors:
    def test_fxhoudini_error_is_a_tool_error(self):
        assert issubclass(FXHoudiniError, ToolError)

    def test_a_command_error_keeps_code_and_message(self):
        error = HoudiniCommandError("valid sections are: Inputs, Outputs", code="ValueError")
        assert tool_error_from(error) is error  # already a ToolError: passes through

    def test_a_plain_exception_becomes_a_tool_error_with_its_text(self):
        converted = tool_error_from(ValueError("op 3: unknown kind 'bogus'"))
        assert isinstance(converted, ToolError)
        assert "op 3: unknown kind 'bogus'" in str(converted)
        assert "ValueError" in str(converted)

    def test_an_empty_message_still_names_the_type(self):
        assert "KeyError" in str(tool_error_from(KeyError()))

    @pytest.mark.asyncio
    async def test_the_registered_tool_raises_a_tool_error(self):
        from fxhoudinimcp.server import _compact_tool

        registered = {}

        def fake_register(*args, **kwargs):
            def take(function):
                registered["fn"] = function
                return function

            return take

        import fxhoudinimcp.server as server

        original = server._sdk_tool
        server._sdk_tool = fake_register
        original_forbid = server.forbid_unknown_arguments
        server.forbid_unknown_arguments = lambda *a, **k: None
        try:

            @_compact_tool()
            async def broken() -> dict:
                raise RuntimeError("the handler said why")

        finally:
            server._sdk_tool = original
            server.forbid_unknown_arguments = original_forbid
        with pytest.raises(ToolError, match="the handler said why"):
            await registered["fn"]()


class TestTrim:
    def test_geometry_floats_are_trimmed(self):
        from fxhoudinimcp.server import _trim

        assert _trim(-10.388985633850098) == -10.38899

    def test_an_epoch_keeps_its_seconds(self):
        from fxhoudinimcp.server import _trim

        assert _trim(1795525432.123456) == 1795525432.123


###### build_network / verify_network cooking


class TestSideBranches:
    def test_wrangles_and_branch_ends_off_display_are_cooked(self):
        a = _Node("/obj/g/a", "grid", needs_cook=False)
        wrangle = _Node("/obj/g/w", "attribwrangle", [a], snippet="@P.y = 1;")
        tail = _Node("/obj/g/tail", "null", [wrangle])
        cooked = graph._cook_side_branches([a, wrangle, tail])
        assert "/obj/g/w" in cooked and "/obj/g/tail" in cooked
        assert a.cooked == 0  # already cooked by the display chain

    def test_sims_and_caches_are_never_cooked(self):
        sim = _Node("/obj/g/vellumsolver1", "vellumsolver")
        cache = _Node("/obj/g/filecache1", "filecache::2.0")
        assert graph._cook_side_branches([sim, cache]) == []
        assert sim.cooked == cache.cooked == 0

    def test_block_ends_are_cooked(self):
        start = _Node("/obj/g/begin", "block_begin", needs_cook=False)
        end = _Node("/obj/g/end", "block_end", [start])
        after = _Node("/obj/g/after", "null", [end], needs_cook=False)
        assert graph._cook_side_branches([start, end, after]) == ["/obj/g/end"]


class TestDryRunVex:
    def test_compile_errors_come_back_per_spec(self, monkeypatch):
        probe = MagicMock()
        probe.errors.return_value = ["Ambiguous call to function point()", "Not enough sources"]
        parent = MagicMock()
        parent.createNode.return_value = probe
        monkeypatch.setattr(graph, "_condensed", lambda e: e)
        wrangle_type = MagicMock()
        wrangle_type.name.return_value = "attribwrangle"
        specs = [{"type": "attribwrangle", "name": "w", "parms": {"snippet": "bad"}}]
        found = graph._dry_run_vex(parent, {"attribwrangle": wrangle_type}, specs)
        assert found == {"w": ["Ambiguous call to function point()"]}
        probe.destroy.assert_called_once()

    def test_specs_without_snippets_build_no_probe(self):
        parent = MagicMock()
        assert graph._dry_run_vex(parent, {}, [{"type": "null"}]) == {}
        parent.createNode.assert_not_called()


###### error scan


class TestErrorsDetailed:
    def test_messages_repeated_on_a_node_are_counted_once(self):
        assert context._dedupe_messages(["w", "w", "x"]) == ["w (x2)", "x"]

    def test_locked_hda_internals_are_skipped(self, monkeypatch):
        def make(path, errors, locked):
            node = MagicMock()
            node.path.return_value = path
            node.type.return_value.name.return_value = "null"
            node.errors.return_value = errors
            node.warnings.return_value = []
            node.parms.return_value = []
            node.isInsideLockedHDA.return_value = locked
            node.inputs.return_value = []
            return node

        root = make("/obj", [], False)
        mine = make("/obj/geo/wrangle", ["Syntax error"], False)
        stock = make("/obj/geo/hf/foreach/get_piece", ["no pieces"], True)
        root.allSubChildren.return_value = [mine, stock]
        monkeypatch.setattr(context, "_get_node", lambda path: root)
        reply = context._get_node_errors_detailed(root_path="/obj")
        assert [d["node_path"] for d in reply["details"]] == ["/obj/geo/wrangle"]
        assert reply["skipped_inside_locked_assets"] == 1
        reply = context._get_node_errors_detailed(root_path="/obj", include_locked_assets=True)
        assert reply["error_node_count"] == 2


###### node types, tidy, dop note


class TestLatestVersions:
    def test_only_the_newest_version_is_kept(self):
        entries = [
            (name, _Type(name, comps), {"name": name, "label": name})
            for name, comps in [
                ("labs::vat::2.0", ("", "labs", "vat", "2.0")),
                ("labs::vat::4.0", ("", "labs", "vat", "4.0")),
                ("labs::vat::3.0", ("", "labs", "vat", "3.0")),
                ("box", ("", "", "box", "")),
            ]
        ]
        kept, hidden = nodes._latest_versions_only(entries)
        assert [r["name"] for r in kept] == ["box", "labs::vat::4.0"]
        assert hidden == 2

    def test_version_10_beats_9(self):
        assert nodes._version_key("10.0") > nodes._version_key("9.5")


class TestTidyLayoutDefault:
    @pytest.mark.asyncio
    async def test_the_server_setting_is_sent(self, mock_ctx, mock_bridge, monkeypatch):
        import fxhoudinimcp.tools.nodes as tools_nodes

        monkeypatch.setattr(tools_nodes, "auto_layout_enabled", lambda: False)
        await tools_nodes.tidy_network(mock_ctx, "/obj/geo1")
        params = mock_bridge.execute.call_args.args[1]
        assert params["layout"] is False


class TestErodeNote:
    def test_heightfield_erode_resimulates_by_itself(self, monkeypatch):
        erode = MagicMock()
        erode.type.return_value.name.return_value = "heightfield_erode::3.0"
        monkeypatch.setattr(dops, "_inside_dop", lambda node: False)
        assert dops._resimulates_on_input_change(erode)

    def test_a_vellum_solver_does_not(self, monkeypatch):
        solver = MagicMock()
        solver.type.return_value.name.return_value = "vellumsolver"
        monkeypatch.setattr(dops, "_inside_dop", lambda node: False)
        assert not dops._resimulates_on_input_change(solver)

    def test_resetsim_counts_as_a_reset_button(self):
        bullet = MagicMock()
        bullet.parm.side_effect = lambda name: MagicMock() if name == "resetsim" else None
        assert dops.reset_parm(bullet) is not None


###### overview, snapshots, help


class TestCompactOverview:
    def test_compact_is_text_and_errors_only(self, monkeypatch):
        a = MagicMock()
        a.name.return_value = "a"
        a.path.return_value = "/obj/g/a"
        a.type.return_value.name.return_value = "grid"
        a.inputs.return_value = []
        a.children.return_value = []
        a.errors.return_value = []
        network = MagicMock()
        network.path.return_value = "/obj/g"
        network.children.return_value = (a,)
        monkeypatch.setattr(context, "_get_node", lambda path: network)
        monkeypatch.setattr(context, "_node_flags", lambda node: {"display": True})
        monkeypatch.setattr(context, "_has_errors", lambda node: False)
        reply = context._get_network_overview("/obj/g", depth=1, detail="compact")
        assert "nodes" not in reply and "connections" not in reply
        assert reply["graph"].startswith("a  grid")
        assert reply["display_node"] == "a"


class TestSnapshotRestore:
    def test_restore_writes_values_and_expressions_back(self, monkeypatch):
        class Parm:
            def __init__(self, name, value=None, expr=None):
                self._name, self.value, self.expr = name, value, expr

            def name(self):
                return self._name

            def keyframes(self):
                return [1] if self.expr else []

            def expression(self):
                if self.expr is None:
                    raise RuntimeError("no expression")
                return self.expr

            def expressionLanguage(self):
                return "hscript"

            def parmTemplate(self):
                raise RuntimeError("not needed")

            def eval(self):
                return self.value

            def isLocked(self):
                return False

            def setExpression(self, expr, language, replace_expression=True):
                self.expr = expr

            def deleteAllKeyframes(self):
                self.expr = None

            def set(self, value):
                self.value = value

        width, height = Parm("width", 2.0), Parm("height", expr="ch('width')")
        node = MagicMock()
        node.path.return_value = "/obj/hda"
        node.parms.return_value = [width, height]
        node.parm.side_effect = {"width": width, "height": height}.get
        node.allSubChildren.return_value = []
        monkeypatch.setattr(context, "_get_node", lambda path: node)
        monkeypatch.setattr(context, "_non_default_parms", lambda n: {})
        monkeypatch.setattr(context.hou, "node", lambda path: node)
        monkeypatch.setattr(context, "_is_count_parm", lambda parm: False)
        context._compare_snapshots("take", "s1", node_path="/obj/hda")
        width.value, height.expr = 9.0, None
        height.value = 3.0
        reply = context._compare_snapshots("restore", "s1")
        assert reply["parms_restored"] == 2
        assert width.value == 2.0 and height.expr == "ch('width')"

    def test_restore_needs_a_scoped_snapshot(self):
        with pytest.raises(ValueError, match="node_path"):
            context._compare_snapshots("restore", "never-taken")


class TestHelpSummary:
    def test_summary_stops_at_the_first_section(self):
        text = (
            '= Scatter =\n"""Scatters points."""\nLonger intro.\n@parameters\nDensity\n== Tips ==\n'
        )
        summary, sections = graph._help_summary(text)
        assert "Scatters points." in summary and "Density" not in summary
        assert sections == ["parameters", "Tips"]


###### simulations upstream


class TestSimulationsUpstream:
    def test_a_bullet_solver_upstream_is_found(self, monkeypatch):
        bullet = MagicMock()
        bullet.path.return_value = "/obj/g/rbdbulletsolver1"
        bullet.parm.side_effect = lambda name: MagicMock() if name == "resetsim" else None
        bullet.inputs.return_value = []
        tail = MagicMock()
        tail.path.return_value = "/obj/g/OUT"
        tail.parm.return_value = None
        tail.inputs.return_value = [bullet]
        monkeypatch.setattr(dops, "_inside_dop", lambda node: False)
        monkeypatch.setattr(dops, "_simulation_to_reset", lambda node: node)
        assert dops.simulations_upstream(tail) == [bullet]


###### scene watcher


class TestWatcher:
    def setup_method(self):
        watch._changes.clear()
        watch._watched.clear()
        watch._depth = 0

    def _event(self, name):
        kind = MagicMock()
        kind.name.return_value = name
        return kind

    def test_an_edit_outside_a_call_is_reported_once(self):
        node = MagicMock()
        node.path.return_value = "/obj/river"
        tuple_ = MagicMock()
        tuple_.name.return_value = "res"
        watch._on_event(self._event("ParmTupleChanged"), node=node, parm_tuple=tuple_)
        watch._on_event(self._event("FlagChanged"), node=node)
        report = watch.take_changes()
        assert report["nodes"] == {"/obj/river": ["parm res", "flags"]}
        assert watch.take_changes() is None

    def test_edits_inside_a_call_are_ignored(self):
        node = MagicMock()
        node.path.return_value = "/obj/river"
        watch.begin_call()
        watch._on_event(self._event("FlagChanged"), node=node)
        watch._depth = 0
        assert watch.take_changes() is None

    def test_paths_named_by_a_command_are_found(self):
        params = {
            "node_path": "/obj/a",
            "parent_path": "/obj",
            "node_paths": ["/obj/b"],
            "x": "/no",
        }
        assert sorted(watch._paths_in(params)) == ["/obj", "/obj/a", "/obj/b"]


###### placement


class TestOverlap:
    def test_a_new_node_is_moved_off_a_sibling(self):
        class Node:
            def __init__(self, path, pos):
                self._path, self._pos = path, pos

            def path(self):
                return self._path

            def position(self):
                return self._pos

            def setPosition(self, pos):
                self._pos = tuple(pos)

            def size(self):
                return (1.0, 0.3)

        existing = Node("/obj/g/a", (0.0, 0.0))
        new = Node("/obj/g/b", (0.2, 0.1))
        parent = MagicMock()
        parent.children.return_value = [existing, new]
        new.parent = lambda: parent
        server_config._clear_overlap(new)
        assert new.position()[0] >= 1.0
        assert existing.position() == (0.0, 0.0)
