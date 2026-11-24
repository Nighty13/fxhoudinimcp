"""Agent-UX batch 2, HDA authoring (recommended-changes #14, #31a-e, #12 part).

- set_hda_interface / edit_hda_interface echo each stored parm ONCE, as a
  compact tree (name, type, label, default); verbose=True gives full templates.
  A 21-parm folder came back twice, ~8k tokens (notes 4e #5, 4f #6).
- from_parm + default_from_value takes the source's CURRENT value as default
  (tuned 0.6 came back as the stock 0.35, 4e #6); from_parm + default is
  refused with a message that names the way out (4g #1).
- inputs=[{label, optional}] writes input labels and min/max inputs (4e #7).
- create_hda(promote_from="CONTROLS") promotes a null's spare parms with their
  current values and links the null back with ch("../name") (4e #8).
- update_hda reports locked_before/after, unlock/lock doors (4f #7), lists
  instance values that differ from the definition defaults Unreal cooks
  (4c-2) and promotes them on request, and reports the library mtime as an
  ISO string and integer epoch ms: the float was trimmed to 7 digits by the
  client, so two saves minutes apart read the same 1795525000.0 (4e #10).

hou is mocked here.
"""

from __future__ import annotations

# Built-in
import os
import sys
from unittest.mock import AsyncMock, MagicMock

# Third-party
import pytest

sys.modules.setdefault("hou", MagicMock())
sys.modules.setdefault("hdefereval", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "houdini", "scripts", "python"))

# Internal
import fxhoudinimcp_server.handlers.hda_handlers as hda  # noqa: E402

###### Fakes


class _Kind:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class _T:
    """A parm template: just enough of hou.ParmTemplate."""

    def __init__(self, name, label="", kind="Float", children=(), default=(0.0,), components=1):
        self._name = name
        self._label = label or name.title()
        self._kind = kind
        self._children = list(children)
        self._default = default
        self._components = components
        self._tags = {}
        self._expression = ()

    def name(self):
        return self._name

    def setName(self, name):  # noqa: N802
        self._name = name

    def label(self):
        return self._label

    def setLabel(self, label):  # noqa: N802
        self._label = label

    def type(self):
        return _Kind(self._kind)

    def parmTemplates(self):  # noqa: N802
        return tuple(self._children)

    def numComponents(self):  # noqa: N802
        return self._components

    def isHidden(self):  # noqa: N802
        return False

    def isActualFolder(self):  # noqa: N802
        return True

    def help(self):
        return ""

    def defaultValue(self):  # noqa: N802
        return self._default

    def setDefaultValue(self, value):  # noqa: N802
        self._default = value

    def defaultExpression(self):  # noqa: N802
        return self._expression

    def tags(self):
        return dict(self._tags)

    def setTags(self, tags):  # noqa: N802
        self._tags = dict(tags)

    def setConditional(self, *args):  # noqa: N802
        pass

    def setScriptCallback(self, *args):  # noqa: N802
        pass

    def clone(self):
        copy = _T(self._name, self._label, self._kind, self._children, self._default)
        copy._components = self._components
        copy._tags = dict(self._tags)
        copy._expression = self._expression
        return copy


class _Group:
    def __init__(self, templates):
        self.templates = list(templates)

    def entries(self):
        return tuple(self.templates)

    def append(self, template):
        self.templates.append(template)

    def find(self, name):
        flat = hda._flatten_templates(self.templates)
        return next((t for t in flat if t.name() == name), None)

    def findFolder(self, label):  # noqa: N802
        return None

    def replace(self, old, new):
        def swap(entries):
            for i, entry in enumerate(entries):
                if entry is old:
                    entries[i] = new
                    return True
                if swap(entry._children):
                    return True
            return False

        assert swap(self.templates)

    def clear(self):
        self.templates = []


class _Section:
    def __init__(self, owner):
        self.owner = owner

    def contents(self):
        return self.owner.script


_SCRIPT = (
    "# Dialog script for river automatically generated\n\n{\n    name\triver\n"
    '    script\triver\n    label\tRiver\n\n    inputlabel\t1\t"Sub-Network Input #1"\n'
    '    inputlabel\t2\t"Sub-Network Input #2"\n    outputlabel\t1\t"out"\n\n'
    '    help {\n\t""\n    }\n\n    parm {\n        name    "k"\n    }\n}\n'
)


class _Definition:
    def __init__(self, templates=(), library="Embedded", script=_SCRIPT):
        self.stored = list(templates)
        self.library = library
        self.script = script
        self.writes = 0
        self.min_inputs = 0
        self.max_inputs = 4
        self.updated_from = []

    def parmTemplateGroup(self):  # noqa: N802
        return _Group(self.stored)

    def setParmTemplateGroup(self, group):  # noqa: N802
        self.writes += 1
        self.stored = list(group.templates)

    def nodeTypeName(self):  # noqa: N802
        return "river"

    def libraryFilePath(self):  # noqa: N802
        return self.library

    def sections(self):
        return {"DialogScript": _Section(self)}

    def addSection(self, name, contents):  # noqa: N802
        assert name == "DialogScript"
        self.script = contents

    def setMinNumInputs(self, n):  # noqa: N802
        self.min_inputs = n

    def setMaxNumInputs(self, n):  # noqa: N802
        self.max_inputs = n

    def minNumInputs(self):  # noqa: N802
        return self.min_inputs

    def maxNumInputs(self):  # noqa: N802
        return self.max_inputs

    def updateFromNode(self, node):  # noqa: N802
        self.updated_from.append(node)


class _Parm:
    def __init__(self, name, value, raw=None, expression=None):
        self._name = name
        self.value = value
        self.raw = raw
        self._expr = expression
        self.tuple_ = None
        self.expression_set = None
        self.set_to = None
        self.keys_deleted = False

    def name(self):
        return self._name

    def tuple(self):
        return self.tuple_

    def parmTemplate(self):  # noqa: N802
        return self.tuple_.parmTemplate()

    def eval(self):
        return self.value

    def evalAsInt(self):  # noqa: N802
        return int(self.value)

    def evalAsString(self):  # noqa: N802
        return str(self.value)

    def unexpandedString(self):  # noqa: N802
        return self.raw if self.raw is not None else str(self.value)

    def expression(self):
        if self._expr is None:
            raise RuntimeError("no expression")
        return self._expr

    def setExpression(self, expression, language=None):  # noqa: N802
        self.expression_set = (expression, language)

    def deleteAllKeyframes(self):  # noqa: N802
        self.keys_deleted = True

    def set(self, value):
        self.set_to = value


class _ParmTuple:
    def __init__(self, name, template, parms):
        self._name = name
        self._template = template
        self.parms = list(parms)
        for parm in self.parms:
            parm.tuple_ = self

    def name(self):
        return self._name

    def parmTemplate(self):  # noqa: N802
        return self._template

    def __iter__(self):
        return iter(self.parms)


class _Asset:
    """An HDA instance: lockable, with parm tuples."""

    def __init__(self, definition, tuples=(), path="/obj/river", locked=True):
        self.definition = definition
        self.tuples = {t.name(): t for t in tuples}
        self._path = path
        self.locked = locked
        self.children = {}
        self.unlocked_calls = 0
        self.matched_calls = 0

    def path(self):
        return self._path

    def type(self):
        node_type = MagicMock()
        node_type.definition.return_value = self.definition
        node_type.name.return_value = "river"
        return node_type

    def isLockedHDA(self):  # noqa: N802
        return self.locked

    def allowEditingOfContents(self):  # noqa: N802
        self.unlocked_calls += 1
        self.locked = False

    def matchCurrentDefinition(self):  # noqa: N802
        self.matched_calls += 1
        self.locked = True

    def parmTuple(self, name):  # noqa: N802
        return self.tuples.get(name)

    def parm(self, name):
        return MagicMock(name=f"asset parm {name}")

    def parms(self):
        return [p for t in self.tuples.values() for p in t]

    def parmTuples(self):  # noqa: N802
        return list(self.tuples.values())

    def node(self, path):
        return self.children.get(path)


@pytest.fixture
def templates(monkeypatch):
    """hou's template constructors and _template_to_dict, faked."""
    hou = hda.hou
    monkeypatch.setattr(
        hou,
        "FloatParmTemplate",
        lambda n, label, c, default_value=(), **kw: _T(n, label, "Float", (), default_value, c),
        raising=False,
    )
    monkeypatch.setattr(
        hou,
        "IntParmTemplate",
        lambda n, label, c, default_value=(), **kw: _T(n, label, "Int", (), default_value, c),
        raising=False,
    )
    monkeypatch.setattr(
        hou,
        "FolderParmTemplate",
        lambda n, label, parm_templates=(), **kw: _T(n, label, "Folder", parm_templates, None),
        raising=False,
    )

    def to_dict(t):
        default = t.defaultValue()
        return {
            "name": t.name(),
            "label": t.label(),
            "type": t.type().name(),
            "num_components": t.numComponents(),
            "is_hidden": False,
            "default_value": list(default) if isinstance(default, tuple) else default,
            "min": 0.0,
            "max": 1.0,
        }

    monkeypatch.setattr(hda, "_template_to_dict", to_dict)
    monkeypatch.setattr(hda, "require_inside_project_root", lambda path, what="": path)
    return hou


def _install(monkeypatch, asset):
    monkeypatch.setattr(hda, "_get_node", lambda path: asset)
    monkeypatch.setattr(hda.hou, "node", lambda path: asset, raising=False)


CONTROLS = {
    "name": "controls",
    "label": "Controls",
    "type": "folder",
    "children": [
        {"name": "waterfall", "type": "float", "default": 0.35},
        {"name": "steps", "type": "int", "default": 4},
    ],
}


###### #14 the interface is echoed once


class TestInterfaceIsEchoedOnce:
    def test_a_folder_insert_echoes_one_compact_tree(self, templates, monkeypatch):
        asset = _Asset(_Definition())
        _install(monkeypatch, asset)

        result = hda.set_hda_interface("/obj/river", [CONTROLS])

        stored = result["ops"][0]["stored"]
        assert stored == [
            {
                "name": "controls",
                "type": "Folder",
                "label": "Controls",
                "children": [
                    {"name": "waterfall", "type": "Float", "label": "Waterfall", "default": 0.35},
                    {"name": "steps", "type": "Int", "label": "Steps", "default": 4},
                ],
            }
        ]
        # The record still names every parm it added.
        assert result["ops"][0]["names"] == ["controls", "steps", "waterfall"]
        assert "_full" not in result["ops"][0]

    def test_verbose_gives_full_templates_still_once(self, templates, monkeypatch):
        asset = _Asset(_Definition())
        _install(monkeypatch, asset)

        result = hda.set_hda_interface("/obj/river", [CONTROLS], verbose=True)

        stored = result["ops"][0]["stored"]
        assert len(stored) == 1
        assert stored[0]["children"][0]["num_components"] == 1
        assert stored[0]["children"][0]["max"] == 1.0

    def test_edit_verb_is_compact_too(self, templates, monkeypatch):
        asset = _Asset(_Definition())
        _install(monkeypatch, asset)

        result = hda.edit_hda_interface("/obj/river", [{"op": "insert", "spec": CONTROLS}])

        assert [s["name"] for s in result["ops"][0]["stored"]] == ["controls"]
        assert "num_components" not in result["ops"][0]["stored"][0]["children"][0]

    def test_top_level_filter_keeps_unrelated_siblings(self):
        infos = [
            {"name": "f", "children": [{"name": "a"}, {"name": "g", "children": [{"name": "b"}]}]},
            {"name": "a"},
            {"name": "b"},
            {"name": "z"},
        ]
        assert [i["name"] for i in hda._top_level_descriptions(infos)] == ["f", "z"]

    def test_compact_keeps_a_default_expression_and_hidden(self):
        info = {
            "name": "p",
            "type": "Float",
            "label": "P",
            "default_value": [1.0, 2.0],
            "default_expression": ["$F"],
            "is_hidden": True,
            "num_components": 2,
        }
        assert hda._compact_description(info) == {
            "name": "p",
            "type": "Float",
            "label": "P",
            "default": [1.0, 2.0],
            "default_expression": ["$F"],
            "hidden": True,
        }


###### #31b default_from_value, and from_parm + default refused


def _source(monkeypatch, template, values, raw=None):
    parms = [_Parm(f"p{i}", v, raw=raw) for i, v in enumerate(values)]
    parm_tuple = _ParmTuple("src", template, parms)
    monkeypatch.setattr(hda.hou, "parm", lambda path: parms[0], raising=False)
    return parm_tuple


class TestDefaultFromValue:
    @pytest.mark.parametrize(
        ("kind", "values", "template_default", "expected"),
        [
            ("Float", [0.6], (0.35,), (0.6,)),
            ("Float", [1.0, 2.0, 3.0], (0.0, 0.0, 0.0), (1.0, 2.0, 3.0)),
            ("Int", [7], (4,), (7,)),
            ("Toggle", [1], False, True),
            ("Menu", [2], 0, 2),
        ],
    )
    def test_the_current_value_becomes_the_default(
        self, monkeypatch, kind, values, template_default, expected
    ):
        source = _T("waterfall", kind=kind, default=template_default, components=len(values))
        _source(monkeypatch, source, values)

        template = hda._cloned_template(
            {"from_parm": "/obj/river/CONTROLS/waterfall", "default_from_value": True}, "wf"
        )

        assert template.defaultValue() == expected
        assert template.name() == "wf"
        assert source.defaultValue() == template_default  # the source is untouched

    def test_a_string_keeps_its_raw_text(self, monkeypatch):
        source = _T("tex", kind="String", default=("",))
        _source(monkeypatch, source, ["C:/proj/tex.png"], raw="$HIP/tex.png")
        template = hda._cloned_template({"from_parm": "/x/tex", "default_from_value": True}, "t")
        assert template.defaultValue() == ("$HIP/tex.png",)

    def test_without_the_flag_the_template_default_is_kept(self, monkeypatch):
        source = _T("waterfall", default=(0.35,))
        _source(monkeypatch, source, [0.6])
        template = hda._cloned_template({"from_parm": "/x/waterfall"}, "wf")
        assert template.defaultValue() == (0.35,)

    def test_a_ramp_takes_its_current_keys(self, monkeypatch):
        source = _T("shape", kind="Ramp", default=2)
        ramp = MagicMock()
        ramp.isColor.return_value = False
        ramp.keys.return_value = (0.0, 0.5, 1.0)
        ramp.values.return_value = (0.0, 0.8, 1.0)
        ramp.basis.return_value = ("rampBasis.Linear", "rampBasis.CatmullRom", "rampBasis.Linear")
        _source(monkeypatch, source, [ramp])

        template = hda._cloned_template({"from_parm": "/x/shape", "default_from_value": True}, "s")

        assert template.defaultValue() == 3
        tag = template.tags()["rampfloatdefault"]
        assert "2pos ( 0.5 ) 2value ( 0.8 ) 2interp ( catmull-rom )" in tag

    def test_from_parm_with_default_is_refused_with_a_message(self):
        with pytest.raises(ValueError) as caught:
            hda._build_parm_template(
                {"name": "wf", "from_parm": "/x/waterfall", "default": 0.6}, set()
            )
        message = str(caught.value)
        assert message  # 4g #1: the refusal reached the client empty
        assert "from_parm copies the template whole, so ['default']" in message
        assert "default_from_value=True" in message
        assert "Nothing was written" in message

    def test_default_from_value_without_from_parm_is_refused(self):
        with pytest.raises(ValueError, match="has no from_parm"):
            hda._build_parm_template({"name": "x", "default_from_value": True}, set())

    def test_the_top_level_flag_reaches_nested_from_parm_specs(self, templates, monkeypatch):
        source = _T("waterfall", default=(0.35,))
        _source(monkeypatch, source, [0.6])
        asset = _Asset(_Definition())
        _install(monkeypatch, asset)

        result = hda.set_hda_interface(
            "/obj/river",
            [
                {
                    "name": "controls",
                    "type": "folder",
                    "children": [{"name": "waterfall", "from_parm": "/x/waterfall"}],
                }
            ],
            default_from_value=True,
        )

        child = result["ops"][0]["stored"][0]["children"][0]
        assert child["name"] == "waterfall" and child["default"] == 0.6

    def test_the_edit_verb_takes_the_flag(self, templates, monkeypatch):
        source = _T("waterfall", default=(0.35,))
        _source(monkeypatch, source, [0.6])
        asset = _Asset(_Definition())
        _install(monkeypatch, asset)

        result = hda.edit_hda_interface(
            "/obj/river",
            [{"op": "insert", "spec": {"name": "wf", "from_parm": "/x/waterfall"}}],
            default_from_value=True,
        )

        assert result["ops"][0]["stored"][0]["default"] == 0.6

    def test_a_spec_can_opt_out_of_the_top_level_flag(self):
        spec = hda._with_default_from_value({"from_parm": "/x", "default_from_value": False})
        assert spec["default_from_value"] is False


###### #31c inputs


class TestInputs:
    def test_labels_and_counts_are_written_and_read_back(self):
        definition = _Definition()
        result = hda._apply_inputs(
            definition,
            [
                {"label": "Terrain"},
                {"label": "River curve"},
                {"label": 'Mask "a"', "optional": True},
            ],
        )
        assert result == {
            "min_num_inputs": 2,
            "max_num_inputs": 3,
            "labels": [
                {"index": 1, "label": "Terrain"},
                {"index": 2, "label": "River curve"},
                {"index": 3, "label": 'Mask "a"'},
            ],
        }
        lines = definition.script.splitlines()
        assert "Sub-Network Input" not in definition.script
        # Written where the old lines were, before the output label.
        assert lines.index('    inputlabel\t1\t"Terrain"') < lines.index(
            '    outputlabel\t1\t"out"'
        )
        assert '    inputlabel\t3\t"Mask \\"a\\""' in lines
        assert "parm {" in definition.script

    def test_a_script_without_input_labels_gets_them_after_the_label(self):
        definition = _Definition(script="{\n    name\tx\n    label\tX\n\n    parm {\n    }\n}\n")
        hda._apply_inputs(definition, ["In"])
        lines = definition.script.splitlines()
        assert lines.index('    inputlabel\t1\t"In"') == lines.index("    label\tX") + 1
        assert definition.min_inputs == 1 and definition.max_inputs == 1

    def test_a_required_input_after_an_optional_one_is_refused(self):
        definition = _Definition()
        with pytest.raises(ValueError, match="FIRST N inputs required"):
            hda._apply_inputs(definition, [{"label": "a", "optional": True}, {"label": "b"}])
        assert definition.script == _SCRIPT

    def test_unknown_keys_are_named(self):
        with pytest.raises(ValueError, match=r"unknown key\(s\) \['name'\]"):
            hda._normalise_inputs([{"label": "a", "name": "x"}])

    def test_dry_run_writes_nothing(self):
        definition = _Definition()
        result = hda._apply_inputs(definition, [{"label": "a"}], dry_run=True)
        assert result["planned"]["max_num_inputs"] == 1
        assert definition.script == _SCRIPT and definition.max_inputs == 4

    def test_a_library_outside_the_project_root_is_refused(self, monkeypatch):
        definition = _Definition(library="/shared/river.hda")

        def outside(path, what=""):
            raise PermissionError(f"{what} outside")

        monkeypatch.setattr(hda, "require_inside_project_root", outside)
        with pytest.raises(PermissionError, match="HDA library"):
            hda._apply_inputs(definition, [{"label": "a"}])

    def test_a_cop_asset_relabels_its_typed_inputs(self):
        script = (
            '{\n    name\te1\n    label\tE1\n\n    input\tinput1\t"src"\n'
            '    output\toutput1\t"dst"\n    signature\tdefault\t"Default"\t{ RGBA }\t{ Mono }\n}\n'
        )
        definition = _Definition(script=script)
        result = hda._apply_inputs(definition, [{"label": "Height"}])
        assert result["labels"] == [{"index": 1, "label": "Height"}]
        assert "{ RGBA }\t{ Mono }" in definition.script
        with pytest.raises(ValueError, match="1 typed input"):
            hda._apply_inputs(definition, ["a", "b"])

    def test_set_hda_interface_takes_inputs_alone(self, templates, monkeypatch):
        definition = _Definition()
        _install(monkeypatch, _Asset(definition))
        result = hda.set_hda_interface("/obj/river", inputs=[{"label": "Terrain"}])
        assert result["inputs"]["labels"] == [{"index": 1, "label": "Terrain"}]
        assert definition.writes == 0  # the parameter interface was not touched

    def test_set_hda_interface_still_needs_something(self, templates, monkeypatch):
        _install(monkeypatch, _Asset(_Definition()))
        with pytest.raises(ValueError, match="non-empty list"):
            hda.set_hda_interface("/obj/river", [])

    def test_bad_inputs_are_refused_before_the_interface_is_written(self, templates, monkeypatch):
        definition = _Definition()
        _install(monkeypatch, _Asset(definition))
        with pytest.raises(ValueError, match="FIRST N"):
            hda.set_hda_interface(
                "/obj/river", [CONTROLS], inputs=[{"optional": True}, {"label": "b"}]
            )
        assert definition.writes == 0


###### #31a create_hda(promote_from=...)


def _subnet(monkeypatch, controls_path="/obj/river/CONTROLS", inner="CONTROLS", locked=True):
    """A SOP subnet with a CONTROLS null, and the asset createDigitalAsset makes of it."""
    waterfall = _ParmTuple("waterfall", _T("waterfall", default=(0.35,)), [_Parm("waterfall", 0.6)])
    size = _ParmTuple(
        "size",
        _T("size", default=(1.0, 1.0), components=2),
        [_Parm("sizex", 3.0), _Parm("sizey", 4.0)],
    )
    tex = _ParmTuple(
        "tex", _T("tex", kind="String", default=("",)), [_Parm("tex", "x", raw="$HIP/a.png")]
    )
    sep = _ParmTuple("sep1", _T("sep1", kind="Separator"), [_Parm("sep1", 0)])
    taken = _ParmTuple("seed", _T("seed", default=(0.0,)), [_Parm("seed", 5.0)])
    controls = MagicMock()
    controls.path.return_value = controls_path
    controls.spareParms.return_value = [p for t in (waterfall, size, tex, sep, taken) for p in t]

    definition = _Definition([_T("seed")], library="/proj/hda/river.hda")
    asset = _Asset(definition, locked=locked)
    asset.children[inner] = controls

    subnet = MagicMock()
    subnet.path.return_value = "/obj/river"
    subnet.isSubNetwork.return_value = True
    subnet.inputs.return_value = ()
    subnet.indirectInputs.return_value = ()
    subnet.type.return_value.category.return_value.name.return_value = "Sop"
    subnet.node.side_effect = lambda path: controls if path == inner else None
    subnet.createDigitalAsset.return_value = asset
    monkeypatch.setattr(hda, "_get_node", lambda path: subnet)
    monkeypatch.setattr(hda, "require_inside_project_root", lambda *a, **k: None)
    monkeypatch.setattr(hda, "_promote_spares_to_definition", lambda n: [])
    monkeypatch.setattr(hda.hou.text, "expandString", lambda s: s, raising=False)
    parms = {"waterfall": waterfall, "size": size, "tex": tex}
    return subnet, asset, definition, parms


class TestPromoteFrom:
    def test_spares_become_the_interface_with_current_values(self, monkeypatch):
        subnet, asset, definition, parms = _subnet(monkeypatch)

        result = hda.create_hda(
            "/obj/river", "/proj/hda/river.hda", "river", "River", promote_from="CONTROLS"
        )

        stored = {t.name(): t.defaultValue() for t in definition.stored}
        assert stored["waterfall"] == (0.6,)
        assert stored["size"] == (3.0, 4.0)
        assert stored["tex"] == ("$HIP/a.png",)
        assert "sep1" not in stored
        promoted = result["promoted_from"]
        assert [p["name"] for p in promoted["parameters"]] == ["waterfall", "size", "tex"]
        assert promoted["parameters"][0]["default"] == 0.6
        assert promoted["skipped"] == [
            {"name": "seed", "reason": "already on the asset's interface"}
        ]

    def test_the_null_is_linked_to_the_asset_parms(self, monkeypatch):
        subnet, asset, definition, parms = _subnet(monkeypatch)
        result = hda.create_hda("/obj/river", "/p/r.hda", "river", "R", promote_from="CONTROLS")

        hscript = hda.hou.exprLanguage.Hscript
        assert parms["waterfall"].parms[0].expression_set == ('ch("../waterfall")', hscript)
        assert parms["size"].parms[1].expression_set == ('ch("../sizey")', hscript)
        assert parms["tex"].parms[0].expression_set == ('chs("../tex")', hscript)
        assert parms["waterfall"].parms[0].keys_deleted
        assert result["promoted_from"]["linked"] == ["waterfall", "sizex", "sizey", "tex"]
        # The links live inside the asset: its contents were saved.
        assert definition.updated_from == [asset]
        assert result["promoted_from"]["contents_saved"] is True

    def test_a_locked_asset_is_unlocked_first(self, monkeypatch):
        subnet, asset, definition, parms = _subnet(monkeypatch, locked=True)
        result = hda.create_hda("/obj/river", "/p/r.hda", "river", "R", promote_from="CONTROLS")
        assert asset.unlocked_calls == 1
        assert result["promoted_from"]["locked_before"] is True
        assert result["promoted_from"]["locked_after"] is False

    def test_an_unlocked_asset_is_left_alone(self, monkeypatch):
        subnet, asset, definition, parms = _subnet(monkeypatch, locked=False)
        hda.create_hda("/obj/river", "/p/r.hda", "river", "R", promote_from="CONTROLS")
        assert asset.unlocked_calls == 0

    def test_a_deeper_null_gets_one_dotdot_per_level(self, monkeypatch):
        subnet, asset, definition, parms = _subnet(
            monkeypatch, controls_path="/obj/river/ui/CONTROLS", inner="ui/CONTROLS"
        )
        result = hda.create_hda("/obj/river", "/p/r.hda", "river", "R", promote_from="ui/CONTROLS")
        assert parms["waterfall"].parms[0].expression_set[0] == 'ch("../../waterfall")'
        assert result["promoted_from"]["reference"] == 'ch("../../<name>")'

    def test_a_ramp_is_linked_by_reference(self, monkeypatch):
        subnet, asset, definition, parms = _subnet(monkeypatch)
        ramp = MagicMock()
        ramp.isColor.return_value = False
        ramp.keys.return_value = (0.0, 1.0)
        ramp.values.return_value = (0.0, 1.0)
        ramp.basis.return_value = ("rampBasis.Linear", "rampBasis.Linear")
        shape_parm = _Parm("shape", ramp)
        _ParmTuple("shape", _T("shape", kind="Ramp", default=2), [shape_parm])
        controls = asset.children["CONTROLS"]
        controls.spareParms.return_value = [shape_parm]

        hda.create_hda("/obj/river", "/p/r.hda", "river", "R", promote_from="CONTROLS")

        assert shape_parm.set_to is not None
        assert shape_parm.expression_set is None

    def test_a_missing_node_is_refused_before_the_asset_is_made(self, monkeypatch):
        subnet, asset, definition, parms = _subnet(monkeypatch)
        with pytest.raises(ValueError, match="'KNOBS' is not a node inside /obj/river"):
            hda.create_hda("/obj/river", "/p/r.hda", "river", "R", promote_from="KNOBS")
        subnet.createDigitalAsset.assert_not_called()

    def test_a_node_outside_the_subnet_is_refused(self, monkeypatch):
        subnet, asset, definition, parms = _subnet(
            monkeypatch, controls_path="/obj/other/CONTROLS", inner="../other/CONTROLS"
        )
        with pytest.raises(ValueError, match="not inside /obj/river"):
            hda.create_hda("/obj/river", "/p/r.hda", "river", "R", promote_from="../other/CONTROLS")
        subnet.createDigitalAsset.assert_not_called()

    def test_a_null_without_spares_is_refused(self, monkeypatch):
        subnet, asset, definition, parms = _subnet(monkeypatch)
        asset.children["CONTROLS"].spareParms.return_value = []
        with pytest.raises(ValueError, match="no spare parameters"):
            hda.create_hda("/obj/river", "/p/r.hda", "river", "R", promote_from="CONTROLS")

    def test_create_hda_sets_inputs(self, monkeypatch):
        subnet, asset, definition, parms = _subnet(monkeypatch)
        result = hda.create_hda(
            "/obj/river",
            "/p/r.hda",
            "river",
            "R",
            inputs=[{"label": "Terrain"}, {"label": "Curve", "optional": True}],
        )
        assert result["inputs"]["min_num_inputs"] == 1
        assert result["max_num_inputs"] == 2
        assert "promoted_from" not in result

    def test_bad_inputs_are_refused_before_the_asset_is_made(self, monkeypatch):
        subnet, asset, definition, parms = _subnet(monkeypatch)
        with pytest.raises(ValueError, match="FIRST N"):
            hda.create_hda("/obj/river", "/p/r.hda", "river", "R", inputs=[{"optional": True}, "b"])
        subnet.createDigitalAsset.assert_not_called()


###### #31d / #31e / #12 update_hda


def _tuned_asset(monkeypatch, locked=True, mtimes=(100.0, 101.0)):
    waterfall_t = _T("waterfall", default=(0.35,))
    steps_t = _T("steps", kind="Int", default=(4,))
    lake_t = _T("lake", kind="Toggle", default=False)
    driven_t = _T("driven", default=(0.0,))
    driven_t._expression = ("$F",)
    folder = _T("controls", kind="Folder", children=[waterfall_t, steps_t, lake_t, driven_t])
    definition = _Definition([folder], library="/proj/hda/river.hda")
    tuples = [
        _ParmTuple("waterfall", waterfall_t, [_Parm("waterfall", 0.6)]),
        _ParmTuple("steps", steps_t, [_Parm("steps", 4)]),
        _ParmTuple("lake", lake_t, [_Parm("lake", 1, expression='ch("../x")')]),
        _ParmTuple("driven", driven_t, [_Parm("driven", 12.0)]),
    ]
    asset = _Asset(definition, tuples, locked=locked)
    monkeypatch.setattr(hda, "_get_node", lambda path: asset)
    monkeypatch.setattr(hda, "require_inside_project_root", lambda *a, **k: None)
    stamps = iter(mtimes)
    monkeypatch.setattr(hda.os.path, "getmtime", lambda path: next(stamps))
    return asset, definition


class TestUpdateHdaLock:
    def test_lock_state_is_reported(self, monkeypatch):
        asset, definition = _tuned_asset(monkeypatch, locked=False)
        result = hda.update_hda("/obj/river")
        assert result["locked_before"] is False and result["locked_after"] is False
        assert definition.updated_from == [asset]

    def test_unlock_allows_editing_and_says_so(self, monkeypatch):
        asset, definition = _tuned_asset(monkeypatch, locked=True)
        result = hda.update_hda("/obj/river", unlock=True)
        assert asset.unlocked_calls == 1
        assert result["locked_before"] is True and result["locked_after"] is False
        assert "was locked" not in result["message"]

    def test_unlock_without_save_is_the_bare_door(self, monkeypatch):
        asset, definition = _tuned_asset(monkeypatch, locked=True)
        result = hda.update_hda("/obj/river", unlock=True, save=False)
        assert definition.updated_from == []
        assert result["locked_after"] is False
        assert "unlocked for editing; nothing was saved" in result["message"]

    def test_lock_matches_the_definition_after_saving(self, monkeypatch):
        asset, definition = _tuned_asset(monkeypatch, locked=False)
        result = hda.update_hda("/obj/river", lock=True)
        assert asset.matched_calls == 1
        assert definition.updated_from == [asset]
        assert result["locked_after"] is True

    def test_unlock_and_lock_together_are_refused(self, monkeypatch):
        _tuned_asset(monkeypatch)
        with pytest.raises(ValueError, match="contradict"):
            hda.update_hda("/obj/river", unlock=True, lock=True)

    def test_saving_a_locked_instance_says_it_saved_nothing_new(self, monkeypatch):
        _tuned_asset(monkeypatch, locked=True)
        result = hda.update_hda("/obj/river")
        assert "pass unlock=True" in result["message"]


class TestInstanceValuesDiffer:
    def test_differing_values_are_listed(self, monkeypatch):
        asset, definition = _tuned_asset(monkeypatch, locked=False)
        result = hda.update_hda("/obj/river")
        assert result["instance_values_differ"] == [
            {"parm": "waterfall", "instance": 0.6, "default": 0.35},
            {"parm": "lake", "instance": True, "default": False, "expression": True},
        ]
        assert "Unreal" in result["warning"]

    def test_promote_current_values_writes_them_as_defaults(self, monkeypatch):
        asset, definition = _tuned_asset(monkeypatch, locked=False)
        result = hda.update_hda("/obj/river", promote_current_values=True)
        defaults = {t.name(): t.defaultValue() for t in hda._flatten_templates(definition.stored)}
        assert defaults["waterfall"] == (0.6,)
        assert defaults["lake"] is True
        assert defaults["steps"] == (4,)
        assert defaults["driven"] == (0.0,)  # a default expression is left alone
        assert result["promoted_defaults"] == ["waterfall", "lake"]
        assert result["instance_values_differ"] == []
        assert "warning" not in result
        assert definition.writes == 1

    def test_a_float_within_tolerance_is_the_same(self):
        assert hda._same_value((0.1 + 0.2,), (0.3,))
        assert not hda._same_value((0.31,), (0.3,))


class TestMtimeIsExact:
    def test_iso_and_epoch_ms_carry_the_real_stat(self, monkeypatch):
        _tuned_asset(monkeypatch, locked=False, mtimes=(1795525000.0, 1795525375.123456))
        result = hda.update_hda("/obj/river")
        assert result["saved_to_disk"] is True
        assert result["library_file_mtime_epoch_ms"] == 1795525375123
        assert result["library_file_mtime_iso"].endswith(".123456+00:00")

    def test_they_survive_the_clients_float_trim(self, monkeypatch):
        from fxhoudinimcp.server import _trim

        _tuned_asset(monkeypatch, locked=False, mtimes=(1795525000.0, 1795525375.123456))
        result = _trim(hda.update_hda("/obj/river"))
        assert result["library_file_mtime_epoch_ms"] == 1795525375123
        assert ".123456" in result["library_file_mtime_iso"]

    def test_embedded_has_none(self, monkeypatch):
        asset, definition = _tuned_asset(monkeypatch, locked=False)
        definition.library = "Embedded"
        result = hda.update_hda("/obj/river")
        assert result["library_file_mtime_iso"] is None
        assert result["library_file_mtime_epoch_ms"] is None


###### Client tools send the new arguments


@pytest.fixture
def bridge(monkeypatch):
    from fxhoudinimcp.tools import hda as tools

    fake = MagicMock()
    fake.execute = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(tools, "_get_bridge", lambda _ctx: fake)
    return tools, fake


class TestClientTools:
    async def test_update_hda_sends_only_what_was_asked(self, bridge):
        tools, fake = bridge
        await tools.update_hda(None, "/obj/river")
        fake.execute.assert_awaited_with("hda.update_hda", {"node_path": "/obj/river"})
        await tools.update_hda(None, "/obj/river", unlock=True, save=False)
        fake.execute.assert_awaited_with(
            "hda.update_hda", {"node_path": "/obj/river", "unlock": True, "save": False}
        )

    async def test_create_hda_passes_promote_from_and_inputs(self, bridge):
        tools, fake = bridge
        await tools.create_hda(
            None, "/obj/r", "/p/r.hda", "r", "R", promote_from="CONTROLS", inputs=[{"label": "a"}]
        )
        payload = fake.execute.await_args.args[1]
        assert payload["promote_from"] == "CONTROLS"
        assert payload["inputs"] == [{"label": "a"}]

    async def test_set_hda_interface_passes_the_flags(self, bridge):
        tools, fake = bridge
        await tools.set_hda_interface(
            None, "/obj/r", inputs=[{"label": "a"}], default_from_value=True, verbose=True
        )
        payload = fake.execute.await_args.args[1]
        assert payload["parameters"] == []
        assert payload["default_from_value"] is True and payload["verbose"] is True

    async def test_edit_hda_interface_passes_the_flags(self, bridge):
        tools, fake = bridge
        await tools.edit_hda_interface(None, "/obj/r", [], default_from_value=True)
        payload = fake.execute.await_args.args[1]
        assert payload["default_from_value"] is True and "verbose" not in payload
