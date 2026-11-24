"""Agent-UX batch 2, parameters and VEX (notes/recommended-changes.md).

- #17 one parameter-filter vocabulary: glob / substring / `a|b`, everywhere;
- #18 get_parameter answers a menu's tokens, generated menus included;
- #19 a String parm is loaded from a file, and the reply carries counts;
- #25 sweep_parameter: set, cook, measure, restore;
- #30 validate_vex(code=...) in a scratch wrangle, with lint hints;
- #15 set_wrangle_code does not echo a full replacement;
- #34 link_parameters says when only a ramp's count is linked.

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
import fxhoudinimcp_server.handlers.graph_handlers as graph  # noqa: E402
import fxhoudinimcp_server.handlers.parameter_handlers as params  # noqa: E402
import fxhoudinimcp_server.handlers.vex_handlers as vex  # noqa: E402
from fxhoudinimcp_server import parm_patterns  # noqa: E402


class _Kind:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


_STRING, _FLOAT, _RAMP = _Kind("String"), _Kind("Float"), _Kind("Ramp")


@pytest.fixture
def types(monkeypatch):
    monkeypatch.setattr(
        params.hou,
        "parmTemplateType",
        MagicMock(String=_STRING, Float=_FLOAT, Ramp=_RAMP),
        raising=False,
    )
    # _serialize_value tests isinstance against these; a MagicMock is no type.
    for name in ("Vector2", "Vector3", "Vector4", "Matrix3", "Matrix4", "Ramp", "Geometry"):
        monkeypatch.setattr(params.hou, name, type(name, (), {}), raising=False)


class _Template:
    def __init__(self, kind, label=""):
        self._kind, self._label = kind, label

    def type(self):
        return self._kind

    def label(self):
        return self._label


class _Key:
    def __init__(self, expression):
        self._expression = expression

    def expression(self):
        return self._expression

    def isExpressionSet(self):
        return True


class _Parm:
    """Enough of hou.Parm for a write, a read-back and a restore."""

    def __init__(self, name, value=0.0, kind=_FLOAT, label="", expression=None, node=None):
        self._name, self.value, self._kind, self._label = name, value, kind, label
        self.expr = expression
        self.lang = "hscript"
        self._node = node
        self.restored_keys = None

    def name(self):
        return self._name

    def node(self):
        return self._node

    def path(self):
        return f"/obj/g/n/{self._name}"

    def parmTemplate(self):
        return _Template(self._kind, self._label)

    def isLocked(self):
        return False

    def eval(self):
        return self.value

    def evalAsString(self):
        return str(self.value)

    def rawValue(self):
        return self.value

    def unexpandedString(self):
        return self.value

    def set(self, value):
        self.value = value

    def keyframes(self):
        return [_Key(self.expr)] if self.expr else []

    def expression(self):
        if not self.expr:
            raise RuntimeError("no expression")
        return self.expr

    def expressionLanguage(self):
        return self.lang

    def deleteAllKeyframes(self):
        self.expr = None

    def setExpression(self, expression, language=None):
        self.expr, self.lang = expression, language

    def setKeyframes(self, keys):
        self.restored_keys = keys


class _Node:
    def __init__(self, path, parms):
        self._path = path
        self._parms = {p.name(): p for p in parms}
        for parm in parms:
            parm._node = self
        self.cooks = 0

    def path(self):
        return self._path

    def parm(self, name):
        return self._parms.get(name)

    def parmTuple(self, name):
        return None

    def parms(self):
        return list(self._parms.values())

    def cook(self, force=False):
        self.cooks += 1

    def errors(self):
        return []

    def geometry(self):
        return None

    def type(self):
        return MagicMock(name=lambda: "null")


###### #17 parm_patterns


class TestParmPatterns:
    @pytest.mark.parametrize(
        ("pattern", "text", "hit"),
        [
            ("scale", "uniformscale", True),  # substring
            ("SCALE", "uniformscale", True),  # case-insensitive
            ("qa_*", "qa_seed", True),  # glob, whole text
            ("qa_*", "myqa_seed", False),
            ("*scale", "uniformscale", True),
            ("ramp?pos", "ramp2pos", True),
            ("ramp[0-9]c", "ramp3c", True),
            ("flame|wind", "windspeed", True),  # alternatives
            ("flame|wind", "buoyancy", False),
        ],
    )
    def test_glob_substring_and_alternatives(self, pattern, text, hit):
        assert parm_patterns.matches(pattern, text) is hit

    def test_a_label_matches_too(self):
        assert parm_patterns.matches(["min value"], "valuea", "Min Value")

    def test_no_pattern_is_no_filter(self):
        assert parm_patterns.compile_patterns(None) is None
        assert parm_patterns.compile_patterns([]) is None
        assert parm_patterns.compile_patterns(["", " | "]) is None
        assert parm_patterns.matches_any(None, "anything")

    def test_a_list_and_bars_flatten_once(self):
        assert parm_patterns.compile_patterns(["a|B", "b", "c"]) == ["a", "b", "c"]

    @pytest.mark.parametrize("parm_filter", ["scale", "flame|wind", "Wind", "|rad|", "none"])
    def test_agrees_with_get_node_card_filter_on_plain_terms(self, parm_filter):
        for texts in (("windspeed", "Wind Speed"), ("scale", "Uniform Scale"), ("rad", "")):
            assert parm_patterns.matches(parm_filter, *texts) == graph._filter_matches(
                parm_filter, *texts
            )


class TestGetParametersGlob:
    def _node(self):
        return _Node(
            "/obj/g/n",
            [
                _Parm("qa_seed", 1, label="QA Seed"),
                _Parm("myqa_x", 2, label="X"),
                _Parm("scale", 3, label="Uniform Scale"),
            ],
        )

    def test_a_glob_matches_whole_names_only(self, monkeypatch, types):
        node = self._node()
        monkeypatch.setattr(params.hou, "node", lambda path: node, raising=False)
        result = params._get_parameters(node_path="/obj/g/n", patterns=["qa_*"])
        assert list(result["parameters"]) == ["qa_seed"]

    def test_substring_and_bar_still_work(self, monkeypatch, types):
        node = self._node()
        monkeypatch.setattr(params.hou, "node", lambda path: node, raising=False)
        result = params._get_parameters(node_path="/obj/g/n", patterns="qa|uniform")
        assert set(result["parameters"]) == {"qa_seed", "myqa_x", "scale"}


class TestParameterSchemaFilter:
    def _node(self, monkeypatch):
        folder = type("Folder", (), {})
        monkeypatch.setattr(params.hou, "FolderParmTemplate", folder, raising=False)
        templates = []
        for name, label in (("qa_seed", "QA Seed"), ("myqa", "Other"), ("scale", "Scale")):
            t = MagicMock()
            t.isHidden.return_value = False
            templates.append(t)
            t._name, t._label = name, label
        monkeypatch.setattr(
            params,
            "_template_to_dict",
            lambda t: {"name": t._name, "label": t._label},
        )
        node = MagicMock()
        node.parmTemplateGroup.return_value.parmTemplates.return_value = templates
        monkeypatch.setattr(params, "_resolve_node", lambda path: node)
        return node

    def test_filter_takes_a_glob(self, monkeypatch):
        self._node(monkeypatch)
        result = params._get_parameter_schema("/obj/n", filter="qa_*")
        assert [p["name"] for p in result["parameters"]] == ["qa_seed"]

    def test_patterns_is_an_alias_and_combines_with_filter(self, monkeypatch):
        self._node(monkeypatch)
        result = params._get_parameter_schema("/obj/n", filter="scale", patterns=["myqa"])
        assert [p["name"] for p in result["parameters"]] == ["myqa", "scale"]


###### #18 get_parameter menu tokens


class TestMenuTokens:
    def test_a_generated_menu_answers_tokens_labels_and_the_current_one(self, monkeypatch):
        parm = _Parm("pivot", "centroid")
        monkeypatch.setattr(
            params, "items_and_labels", lambda p: (["origin", "centroid"], ["Origin", "Centroid"])
        )
        found = params._menu_of(parm, parm.parmTemplate())
        assert found == {
            "menu_items": ["origin", "centroid"],
            "menu_labels": ["Origin", "Centroid"],
            "menu_token": "centroid",
        }

    def test_a_long_menu_is_capped(self, monkeypatch):
        tokens = [f"t{i}" for i in range(80)]
        monkeypatch.setattr(params, "items_and_labels", lambda p: (tokens, tokens))
        found = params._menu_of(_Parm("m", "x"), _Template(_FLOAT))
        assert len(found["menu_items"]) == params._MENU_CAP
        assert found["menu_truncated"] == 80

    def test_a_raising_menu_script_is_reported_not_raised(self, monkeypatch):
        def boom(parm):
            raise params.MenuScriptError("Menu script of /obj/n/group raised TypeError\ntb")

        monkeypatch.setattr(params, "items_and_labels", boom)
        assert params._menu_of(_Parm("group"), _Template(_STRING)) == {
            "menu_error": "Menu script of /obj/n/group raised TypeError"
        }

    def test_not_a_menu_answers_nothing(self, monkeypatch):
        def refuse(parm):
            raise RuntimeError("not a menu")

        monkeypatch.setattr(params, "items_and_labels", refuse)
        assert params._menu_of(_Parm("scale"), object()) == {}


###### #19 from_file


class TestFromFile:
    def _setup(self, monkeypatch, kind=_STRING):
        parm = _Parm("python", "", kind=kind)
        node = _Node("/obj/g/py", [parm])
        monkeypatch.setattr(params, "_resolve_node", lambda path: node)
        monkeypatch.setattr(params, "dop_cache_note", lambda nodes: None)
        monkeypatch.setattr(params, "_expand_path", lambda path: path)
        return node, parm

    def test_set_parameter_loads_the_file_and_answers_counts(self, monkeypatch, types, tmp_path):
        _, parm = self._setup(monkeypatch)
        source = tmp_path / "gen.py"
        source.write_bytes(b"node = hou.pwd()\r\ngeo = node.geometry()\r\n")
        result = params._set_parameter("/obj/g/py", "python", from_file=str(source))
        assert parm.value == "node = hou.pwd()\ngeo = node.geometry()\n"
        assert result["chars"] == len(parm.value) and result["lines"] == 2
        assert result["stored_matches_file"] is True
        assert "new_value" not in result and "raw_value" not in result

    def test_value_and_from_file_together_are_refused(self, monkeypatch, types, tmp_path):
        self._setup(monkeypatch)
        with pytest.raises(ValueError, match="value or from_file"):
            params._set_parameter("/obj/g/py", "python", value="x", from_file="a.py")

    def test_a_missing_file_names_the_expanded_path(self, monkeypatch, types, tmp_path):
        self._setup(monkeypatch)
        with pytest.raises(ValueError, match="no such file"):
            params._set_parameter("/obj/g/py", "python", from_file=str(tmp_path / "none.py"))

    def test_a_non_string_parm_is_refused(self, monkeypatch, types, tmp_path):
        self._setup(monkeypatch, kind=_FLOAT)
        source = tmp_path / "v.txt"
        source.write_text("1")
        with pytest.raises(ValueError, match="String parameters"):
            params._set_parameter("/obj/g/py", "python", from_file=str(source))

    def test_set_parameters_takes_a_from_file_value(self, monkeypatch, types, tmp_path):
        _, parm = self._setup(monkeypatch)
        source = tmp_path / "gen.py"
        source.write_text("print(1)")
        reply = params._set_parameters("/obj/g/py", {"python": {"from_file": str(source)}})
        assert reply["errors"] == []
        assert reply["set"][0]["chars"] == 8 and parm.value == "print(1)"
        assert "new_value" not in reply["set"][0]

    def test_expand_path_uses_houdini_variables(self, monkeypatch):
        monkeypatch.setattr(
            params.hou,
            "text",
            MagicMock(expandString=lambda s: s.replace("$HIP", "/proj")),
            raising=False,
        )
        assert params._expand_path("$HIP/scripts/a.py").replace("\\", "/") == "/proj/scripts/a.py"


###### #25 sweep_parameter


class TestSweepParameter:
    def _setup(self, monkeypatch, parm):
        measure = _Parm("count", 0)
        node = _Node("/obj/g/n", [parm, measure])
        monkeypatch.setattr(params, "_resolve_node", lambda path: node)
        monkeypatch.setattr(params.hou, "node", lambda path: node, raising=False)
        return node, measure

    def test_one_row_per_value_and_the_literal_comes_back(self, monkeypatch, types):
        parm = _Parm("threshold", 0.25)
        node, measure = self._setup(monkeypatch, parm)
        # The measured parm follows the swept one, as a downstream cook would.
        measure.eval = lambda: parm.value * 10
        reply = params._sweep_parameter(
            "/obj/g/n", "threshold", [0.1, 0.5, 0.9], measure={"parms": ["count"]}
        )
        assert [row["value"] for row in reply["rows"]] == [0.1, 0.5, 0.9]
        assert [row["parms"]["count"] for row in reply["rows"]] == [1.0, 5.0, 9.0]
        assert all("cook_ms" in row for row in reply["rows"])
        assert node.cooks == 3
        assert parm.value == 0.25 and reply["restored"] is True and reply["original"] == 0.25

    def test_an_expression_is_restored_not_left_as_the_last_value(self, monkeypatch, types):
        parm = _Parm("threshold", 0.25, expression='ch("../ctl")')
        self._setup(monkeypatch, parm)
        reply = params._sweep_parameter("/obj/g/n", "threshold", [1, 2])
        assert parm.expr == 'ch("../ctl")'
        assert reply["original"] == {"expression": 'ch("../ctl")'}

    def test_the_parm_is_restored_when_measuring_raises(self, monkeypatch, types):
        parm = _Parm("threshold", 0.25)
        self._setup(monkeypatch, parm)

        def boom(spec):
            raise RuntimeError("cook crashed")

        monkeypatch.setattr(params, "_measure_once", boom)
        with pytest.raises(RuntimeError):
            params._sweep_parameter("/obj/g/n", "threshold", [3.0])
        assert parm.value == 0.25

    def test_a_menu_token_becomes_its_index(self, monkeypatch, types):
        parm = _Parm("pivot", 0)
        self._setup(monkeypatch, parm)
        monkeypatch.setattr(params, "menu_items", lambda p: ["origin", "centroid"])
        seen = []
        original_set = parm.set
        parm.set = lambda v: seen.append(v) or original_set(v)
        params._sweep_parameter("/obj/g/n", "pivot", ["centroid"])
        assert seen[0] == 1 and parm.value == 0

    def test_a_bad_value_is_a_row_error_not_a_failed_call(self, monkeypatch, types):
        parm = _Parm("threshold", 0.25)
        self._setup(monkeypatch, parm)
        reply = params._sweep_parameter("/obj/g/n", "threshold", [[1, 2], 0.5])
        assert "error" in reply["rows"][0] and "error" not in reply["rows"][1]

    @pytest.mark.parametrize(
        ("values", "measure", "match"),
        [
            ([], None, "non-empty"),
            (list(range(100)), None, "at most"),
            ([1], {"attribz": ["P"]}, "unknown key"),
        ],
    )
    def test_bad_arguments_are_refused(self, monkeypatch, types, values, measure, match):
        self._setup(monkeypatch, _Parm("threshold", 0.25))
        with pytest.raises(ValueError, match=match):
            params._sweep_parameter("/obj/g/n", "threshold", values, measure=measure)

    def test_stats_are_compacted(self, monkeypatch):
        stats = {"P": {"count": 4, "size": 3, "sum": 9, "min": 0, "max": 1, "mean": 0.5}}
        assert params._compact_stats(stats) == {"P": {"min": 0, "max": 1, "mean": 0.5}}


###### #30 / #15 VEX


class TestVexLint:
    def test_point_in_a_comparison_is_flagged(self):
        hints = vex.vex_lint(
            'if (point(0, "id", @ptnum) < point(0, "id", 0)) removepoint(0, @ptnum);'
        )
        assert any("point() at line 1" in h and "comparison" in h for h in hints)

    def test_a_typed_variable_is_not_flagged(self):
        code = (
            'float a = point(0, "x", 0);\nif (a < 1) @P.y = 0;\nfloat b = float(point(0, "x", 1)) ;'
        )
        assert vex.vex_lint(code) == []

    def test_point_as_an_append_argument_is_flagged(self):
        hints = vex.vex_lint('int ids[];\nappend(ids, point(0, "id", 3));')
        assert any("an argument of append()" in h and "line 2" in h for h in hints)

    def test_foreach_over_an_array_literal_is_explained(self):
        hints = vex.vex_lint("foreach (int tp; array(t1, t2)) { }")
        assert any("__impl_foreach_end" in h for h in hints)

    def test_addpoint_with_point_0_reads_the_input(self):
        code = 'int pt = addpoint(0, {0,0,0});\nvector p = point(0, "P", pt);'
        assert any("addpoint()" in h for h in vex.vex_lint(code))

    def test_range_does_not_exist(self):
        assert any("range()" in h for h in vex.vex_lint("for (int i : range(3)) {}"))

    def test_comments_do_not_trigger_hints(self):
        assert vex.vex_lint('// if (point(0,"a",0) < 1)\n/* foreach (int x; {1,2}) */') == []

    def test_compiler_errors_get_a_plain_reading(self):
        hints = vex.error_hints(["Error: uninitialized __impl_foreach_end"])
        assert hints and "array" in hints[0]


class TestSetWrangleCodeCompact:
    def test_a_full_replacement_is_not_echoed(self, monkeypatch):
        node = MagicMock()
        node.path.return_value = "/obj/g/w"
        monkeypatch.setattr(vex, "_get_node", lambda path: node)
        monkeypatch.setattr(
            vex,
            "_validate_vex_quick",
            lambda n: {"vex_valid": False, "vex_errors": ["Ambiguous call to point"]},
        )
        code = 'if (point(0, "a", 0) < 1)\n    @P.y = 0;\n'
        result = vex.set_wrangle_code("/obj/g/w", vex_code=code)
        node.parm.return_value.set.assert_called_once_with(code)
        assert "vex_code" not in result
        assert result["line_count"] == 2 and result["char_count"] == len(code)
        assert result["vex_valid"] is False and result["hints"]


class _Wrangle:
    def __init__(self):
        self.snippet = MagicMock()
        self.cls = MagicMock()

    def parm(self, name):
        return {"snippet": self.snippet, "class": None}.get(name)

    def path(self):
        return "/obj/__fxh_vex_scratch/__fxh_vex_check"


class TestValidateVexCode:
    def test_a_snippet_compiles_in_a_scratch_node_that_is_destroyed(self, monkeypatch):
        wrangle, scratch = _Wrangle(), MagicMock()
        monkeypatch.setattr(vex, "_scratch_wrangle", lambda near, kind: (wrangle, scratch))
        monkeypatch.setattr(
            vex,
            "_validate_vex_quick",
            lambda n: {
                "vex_valid": False,
                "vex_errors": [f"{n.path()}: uninitialized __impl_foreach_end"],
                "vex_warnings": [],
            },
        )
        code = "foreach (int x; array(1, 2)) { }"
        result = vex.validate_vex(code=code)
        wrangle.snippet.set.assert_called_once_with(code)
        scratch.destroy.assert_called_once()
        assert result["is_valid"] is False
        assert result["errors"] == ["<snippet>: uninitialized __impl_foreach_end"]
        assert "vex_code" not in result and "code" not in result
        assert any("__impl_foreach_end" in h for h in result["hints"])

    def test_the_scratch_is_destroyed_even_when_the_check_raises(self, monkeypatch):
        wrangle, scratch = _Wrangle(), MagicMock()
        monkeypatch.setattr(vex, "_scratch_wrangle", lambda near, kind: (wrangle, scratch))

        def boom(node):
            raise RuntimeError("cook failed")

        monkeypatch.setattr(vex, "_validate_vex_quick", boom)
        with pytest.raises(RuntimeError):
            vex.validate_vex(code="@P.y = 1;")
        scratch.destroy.assert_called_once()

    def test_an_unknown_context_is_refused(self):
        with pytest.raises(ValueError, match="Unknown context"):
            vex.validate_vex(code="@P.y = 1;", context="cop")

    def test_neither_node_nor_code_is_refused(self):
        with pytest.raises(ValueError, match="node_path .* or code"):
            vex.validate_vex()


###### #34 link_parameters


class TestLinkRampToPlain:
    def test_a_ramp_linked_to_a_plain_parm_says_only_the_count_follows(self, monkeypatch):
        src, dst = MagicMock(), MagicMock()
        src.path.return_value, dst.path.return_value = "/obj/a/r", "/obj/b/n"
        src.name.return_value, dst.name.return_value = "r", "n"
        dst.keyframes.return_value = []
        src.keyframes.return_value = []
        monkeypatch.setattr(
            params, "_resolve_parm", lambda path, name: src if path == "/obj/a" else dst
        )
        monkeypatch.setattr(params, "_is_multiparm_parent", lambda parm: parm is src)
        monkeypatch.setattr(params, "_relative_channel_path", lambda d, s: "../a/r")
        monkeypatch.setattr(params, "_channel_function", lambda parm: "ch")
        monkeypatch.setattr(params, "set_whole_expression", lambda *a: 0)
        reply = params._link_parameters("/obj/a", "r", "/obj/b", "n")
        assert "only the entry count is linked" in reply["warning"]


###### Client tools


class TestClientPayloads:
    @pytest.mark.asyncio
    async def test_schema_forwards_parm_names_and_patterns(self, mock_ctx, mock_bridge):
        from fxhoudinimcp.tools.parameters import get_parameter_schema

        await get_parameter_schema(
            mock_ctx, node_path="/obj/n", parm_names=["a", "b"], patterns=["qa_*"]
        )
        mock_bridge.execute.assert_called_once_with(
            "parameters.get_parameter_schema",
            {"node_path": "/obj/n", "parm_names": ["a", "b"], "patterns": ["qa_*"]},
        )

    @pytest.mark.asyncio
    async def test_set_parameter_from_file_sends_no_value(self, mock_ctx, mock_bridge):
        from fxhoudinimcp.tools.parameters import set_parameter

        await set_parameter(mock_ctx, node_path="/obj/n", parm_name="python", from_file="a.py")
        sent = mock_bridge.execute.call_args.args[1]
        assert sent["from_file"] == "a.py" and "value" not in sent

    @pytest.mark.asyncio
    async def test_set_parameter_keeps_its_old_call(self, mock_ctx, mock_bridge):
        from fxhoudinimcp.tools.parameters import set_parameter

        await set_parameter(mock_ctx, "/obj/n", "tx", 1.5)
        sent = mock_bridge.execute.call_args.args[1]
        assert sent["value"] == 1.5 and "from_file" not in sent

    @pytest.mark.asyncio
    async def test_sweep_parameter(self, mock_ctx, mock_bridge):
        from fxhoudinimcp.tools.parameters import sweep_parameter

        await sweep_parameter(
            mock_ctx, node_path="/obj/n", parm_name="t", values=[0, 1], measure={"attribs": ["P"]}
        )
        mock_bridge.execute.assert_called_once_with(
            "parameters.sweep_parameter",
            {
                "node_path": "/obj/n",
                "parm_name": "t",
                "values": [0, 1],
                "measure": {"attribs": ["P"]},
            },
        )

    @pytest.mark.asyncio
    async def test_validate_vex_code(self, mock_ctx, mock_bridge):
        from fxhoudinimcp.tools.vex import validate_vex

        await validate_vex(mock_ctx, code="@P.y = 1;", run_over="Detail")
        mock_bridge.execute.assert_called_once_with(
            "vex.validate_vex", {"code": "@P.y = 1;", "run_over": "Detail"}
        )

    @pytest.mark.asyncio
    async def test_validate_vex_by_node_is_unchanged(self, mock_ctx, mock_bridge):
        from fxhoudinimcp.tools.vex import validate_vex

        await validate_vex(mock_ctx, "/obj/g/w")
        mock_bridge.execute.assert_called_once_with("vex.validate_vex", {"node_path": "/obj/g/w"})
