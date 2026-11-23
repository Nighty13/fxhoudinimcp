"""tidy_network: one call that lays a network out and documents it.

Graphs the agent built were unreadable: no layout (auto-layout off by default)
and nothing told the agent to box and annotate its stages. tidy_network boxes
each section with a title and a note, replaces its own boxes on re-run, and
names the nodes left undocumented.

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
import fxhoudinimcp_server.handlers.node_handlers as nodes  # noqa: E402


class _Item:
    def __init__(self, name, parent, pos=(0.0, 0.0)):
        self._name, self._parent, self._pos = name, parent, pos

    def name(self):
        return self._name

    def path(self):
        return f"{self._parent.path()}/{self._name}"

    def parent(self):
        return self._parent

    def position(self):
        return self._pos

    def setPosition(self, p):
        self._pos = (p[0], p[1])


class _Box:
    def __init__(self, parent):
        self._parent, self._comment, self.items, self.destroyed = parent, "", [], False

    def setComment(self, c):
        self._comment = c

    def comment(self):
        return self._comment

    def setColor(self, c):
        self.color = c

    def addItem(self, item):
        self.items.append(item)

    def fitAroundContents(self):
        pass

    def stickyNotes(self):
        return [i for i in self.items if isinstance(i, _Note)]

    def destroy(self):
        self.destroyed = True
        self._parent.boxes.remove(self)


class _Note(_Item):
    def __init__(self, name, parent):
        super().__init__(name, parent)
        self.destroyed = False

    def setText(self, t):
        self.text = t

    def setPosition(self, p):
        pass

    def setSize(self, s):
        pass

    def setColor(self, c):
        pass

    def destroy(self):
        self.destroyed = True


class _Net:
    def __init__(self, names):
        self.kids = {n: _Item(n, self, (i * 2.0, -i * 1.0)) for i, n in enumerate(names)}
        self.boxes, self.laid_out = [], False

    def path(self):
        return "/obj/geo1"

    def node(self, ref):
        return self.kids.get(ref.split("/")[-1])

    def children(self):
        return list(self.kids.values())

    def layoutChildren(self, items=()):
        self.laid_out = True

    def networkBoxes(self):
        return list(self.boxes)

    def createNetworkBox(self):
        box = _Box(self)
        self.boxes.append(box)
        return box

    def createStickyNote(self, name):
        return _Note(name, self)


@pytest.fixture
def net(monkeypatch):
    n = _Net(["CONTROLS", "box1", "fracture", "solver", "OUT"])
    monkeypatch.setattr(nodes, "_get_node", lambda path: n)
    fake = MagicMock()
    fake.node.return_value = None
    fake.Vector2.side_effect = lambda x, y: (x, y)
    monkeypatch.setattr(nodes, "hou", fake)
    monkeypatch.setattr(nodes, "auto_layout_enabled", lambda: False)
    return n


SECTIONS = [
    {"title": "Inputs", "nodes": ["CONTROLS", "box1"], "note": "Sliders live on CONTROLS."},
    {"title": "Destruction", "nodes": ["fracture", "solver"], "note": "Fracture + Bullet."},
]


def test_sections_become_titled_boxes_with_notes(net):
    result = nodes.tidy_network("/obj/geo1", SECTIONS)
    assert [b["title"] for b in result["boxes"]] == ["Inputs", "Destruction"]
    assert all(b["note"] for b in result["boxes"])
    assert len(net.boxes) == 2
    assert any(isinstance(i, _Note) for i in net.boxes[0].items)


def test_undocumented_nodes_are_named(net):
    result = nodes.tidy_network("/obj/geo1", SECTIONS)
    assert result["undocumented"] == ["OUT"]


def test_rerun_replaces_its_own_boxes(net):
    nodes.tidy_network("/obj/geo1", SECTIONS)
    result = nodes.tidy_network("/obj/geo1", SECTIONS)
    assert result["replaced_boxes"] == 2
    assert len(net.boxes) == 2


def test_layout_follows_setting_unless_overridden(net):
    assert nodes.tidy_network("/obj/geo1", SECTIONS)["laid_out"] is False
    assert net.laid_out is False
    assert nodes.tidy_network("/obj/geo1", SECTIONS, layout=True)["laid_out"] is True
    assert net.laid_out is True


def test_unknown_nodes_are_reported(net):
    result = nodes.tidy_network("/obj/geo1", [{"title": "X", "nodes": ["nope", "OUT"]}])
    assert result["not_found"] == ["nope"]


def test_layout_places_sections_left_to_right(net):
    nodes.tidy_network("/obj/geo1", SECTIONS, layout=True)
    inputs_x = max(net.kids[n].position()[0] for n in ("CONTROLS", "box1"))
    destruction_x = min(net.kids[n].position()[0] for n in ("fracture", "solver"))
    assert destruction_x > inputs_x
