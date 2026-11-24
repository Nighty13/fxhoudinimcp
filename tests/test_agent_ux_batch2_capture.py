"""Agent-UX batch 2: captures that do not depend on hidden UI state.

- capture_screenshot(node=, network=, camera=, resolution=, max_size=) sets the
  view, captures and puts the viewer back in one call; node= draws one node
  through a temporary object merge without moving a display flag (#35, #20);
- the capture reply says what was drawn (#36);
- capture_network_editor(network_path=, frame="all", max_size=) and the editor
  goes back after the follow-up grab (#37, #20);
- render_sheet renders through the viewport inside Houdini: frames, views,
  orbit and by_attribute sheets (#38);
- perspective framing from an angle with bbox/FOV distance and clearance (#39);
- render_viewport(visualize=True) for heightfields (#40);
- start_render knows HeightField Output's files, tiled ones included (#8);
- "persp" is accepted as a direction (#12).

hou is mocked here; the live checks are the user's.
"""

from __future__ import annotations

# Built-in
import contextlib
import math
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

# Third-party
import pytest

sys.modules.setdefault("hou", MagicMock())
sys.modules.setdefault("hdefereval", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "houdini", "scripts", "python"))

# Internal
import fxhoudinimcp_server.handlers.rendering_handlers as rendering  # noqa: E402
import fxhoudinimcp_server.handlers.viewport_handlers as viewport  # noqa: E402
import fxhoudinimcp_server.outputs as outputs  # noqa: E402

# Taken at import: another test replaces the module attribute for the run.
_REPORTED_OUTPUTS = outputs.reported_outputs


###### Fakes


class _Vec(tuple):
    def __new__(cls, *values):
        return super().__new__(cls, values)

    def __mul__(self, transform):
        return _Vec(*(a + b for a, b in zip(self, transform.offset, strict=True)))


class _Move:
    def __init__(self, *offset):
        self.offset = offset


class _Rotation:
    def __init__(self, angles, transposed=False):
        self.angles = tuple(angles)
        self.is_transposed = transposed

    def extractRotationMatrix3(self):
        return self

    def transposed(self):
        return _Rotation(self.angles, not self.is_transposed)

    def extractRotates(self):
        return self.angles


class _FreeView:
    def __init__(self, pivot=(0.0, 0.0, 0.0), distance=10.0):
        self._rotation = _Rotation((0.0, 0.0, 0.0), transposed=True)
        self._pivot = _Vec(*pivot)
        self._translation = _Vec(pivot[0], pivot[1], pivot[2] + distance)

    def stash(self):
        copy = _FreeView()
        copy._rotation, copy._pivot, copy._translation = (
            self._rotation,
            self._pivot,
            self._translation,
        )
        return copy

    def setRotation(self, matrix):
        self._rotation = matrix

    def rotation(self):
        return self._rotation

    def setPivot(self, pivot):
        self._pivot = _Vec(*pivot)

    def pivot(self):
        return self._pivot

    def setTranslation(self, translation):
        self._translation = _Vec(*translation)

    def translation(self):
        return self._translation

    def aperture(self):
        return 41.4214

    def focalLength(self):
        return 50.0


class _Settings:
    def __init__(self):
        self.pattern = "*"
        self.guides = {}

    def visibleObjects(self):
        return self.pattern

    def setVisibleObjects(self, pattern):
        self.pattern = pattern

    def guideEnabled(self, guide):
        return self.guides.get(guide, True)


class _Viewport:
    def __init__(self):
        self.view = _FreeView()
        self.kind = "Perspective"
        self.bound = None
        self._settings = _Settings()
        self.calls = []
        self.pixels = (800, 400)

    def name(self):
        return "persp1"

    def type(self):
        return self.kind

    def changeType(self, kind):
        self.calls.append(("changeType", kind))
        self.kind = kind

    def defaultCamera(self):
        return self.view

    def setDefaultCamera(self, view):
        self.calls.append("setDefaultCamera")
        self.view = view
        self.bound = None

    def useDefaultCamera(self):
        self.bound = None

    def camera(self):
        return self.bound if not isinstance(self.bound, str) else None

    def cameraPath(self):
        if self.bound is None:
            return ""
        return self.bound if isinstance(self.bound, str) else self.bound.path()

    def setCamera(self, camera):
        self.calls.append(("setCamera", camera))
        self.bound = camera

    def frameBoundingBox(self, box):
        self.calls.append(("frameBoundingBox", tuple(box)))

    def frameAll(self):
        self.calls.append("frameAll")

    def homeAll(self):
        self.calls.append("homeAll")

    def settings(self):
        return self._settings

    def resolutionInPixels(self):
        return self.pixels


class _Parm:
    def __init__(self, value=None, items=()):
        self.value = value
        self.items = items
        self.history = []

    def set(self, value):
        self.value = value
        self.history.append(value)

    def eval(self):
        return self.value

    def menuItems(self):
        return self.items


class _Box:
    def __init__(self, box):
        self.box = box

    def isValid(self):
        return True

    def minvec(self):
        return self.box[:3]

    def maxvec(self):
        return self.box[3:]


class _Geo:
    def __init__(self, box=(0, 0, 0, 1, 1, 1), prims=10, points=20, attribs=None):
        self.box, self.prims, self.points = box, prims, points
        self.attribs = attribs or {}

    def boundingBox(self):
        return _Box(self.box)

    def intrinsicValue(self, name):
        return {"primitivecount": self.prims, "pointcount": self.points}[name]

    def countPrimType(self, kind):
        return 0

    def findPrimAttrib(self, name):
        return self.attribs.get(("prim", name))

    def findPointAttrib(self, name):
        return self.attribs.get(("point", name))

    def primStringAttribValues(self, name):
        return self.attribs[("prim", name)].values

    def primIntAttribValues(self, name):
        return self.attribs[("prim", name)].values


class _Category(str):
    def name(self):
        return str(self)


class _Node:
    def __init__(self, path, parent=None, category="Sop", geo=None, network=False):
        self._path, self._parent, self.category = path, parent, category
        self.geo = geo
        self.network = network
        self.children_made = []
        self.parms = {}
        self.inputs = {}
        self.flags = []
        self.destroyed = False
        self.display = None
        self.type_name = ""

    def path(self):
        return self._path

    def name(self):
        return self._path.rsplit("/", 1)[-1]

    def parent(self):
        return self._parent

    def isNetwork(self):
        return self.network

    def type(self):
        node = self
        return types.SimpleNamespace(
            category=lambda: _Category(node.category),
            name=lambda: node.type_name,
        )

    def childTypeCategory(self):
        return self.child_category

    def geometry(self):
        return self.geo

    def displayNode(self):
        return self.display

    def worldTransform(self):
        return _Move(0.0, 0.0, 0.0)

    def parm(self, name):
        return self.parms.setdefault(name, _Parm())

    def createNode(self, type_name, name=None):
        child = _Node(f"{self._path}/{name or type_name}", parent=self)
        child.type_name = type_name
        child.geo = _Geo()
        if type_name == "blast":
            child.parms["grouptype"] = _Parm(
                items=("guess", "breakpoints", "edges", "points", "prims")
            )
        self.children_made.append(child)
        return child

    def children(self):
        return list(self.children_made)

    def destroy(self):
        self.destroyed = True

    def setDisplayFlag(self, on):
        self.flags.append(("display", on))

    def setRenderFlag(self, on):
        self.flags.append(("render", on))

    def setInput(self, index, node):
        self.inputs[index] = node

    def errors(self):
        return ()


class _Obj(_Node):
    pass


class _Viewer:
    def __init__(self, pwd):
        self.view = _Viewport()
        self._pwd = pwd
        self.flipbooks = []
        self.pwd_history = []

    def name(self):
        return "panetab1"

    def type(self):
        return "SceneViewer"

    def curViewport(self):
        return self.view

    def viewports(self):
        return [self.view]

    def pwd(self):
        return self._pwd

    def setPwd(self, node):
        self.pwd_history.append(node.path())
        self._pwd = node

    def flipbookSettings(self):
        return MagicMock()

    def flipbook(self, view, settings):
        self.flipbooks.append(dict(self.snapshot()))

    def snapshot(self):
        return {
            "pwd": self._pwd.path(),
            "pattern": self.view._settings.pattern,
            "bound": self.view.cameraPath(),
        }


@pytest.fixture
def scene(monkeypatch, tmp_path):
    """/obj with geo1/box1 (a SOP), a viewer inside /obj/geo1, hou maths as tuples."""
    hou = viewport.hou
    root = _Node("/obj", category="Object", network=True)
    root.child_category = "Object"
    geo1 = _Obj("/obj/geo1", parent=root, category="Object", network=True)
    geo1.child_category = "Sop"
    box1 = _Node("/obj/geo1/box1", parent=geo1, geo=_Geo((-1, 0, -1, 1, 2, 1)))
    geo1.display = box1
    cam = _Obj("/obj/cam1", parent=root, category="Object")
    nodes = {n.path(): n for n in (root, geo1, box1, cam)}
    viewer = _Viewer(geo1)
    editor = MagicMock()
    editor.type.return_value = "NetworkEditor"
    editor.pwd.return_value = geo1

    def lookup(path):
        if path in nodes:
            return nodes[path]
        for made in root.children_made:
            if made.path() == path:
                return made
        return None

    for module in (viewport, rendering):
        monkeypatch.setattr(module, "keep_viewer_state", contextlib.nullcontext)
        monkeypatch.setattr(
            module, "viewer_context", lambda pane: {"viewer_network": pane.pwd().path()}
        )
    monkeypatch.setattr(viewport, "selection_hidden", contextlib.nullcontext)
    monkeypatch.setattr(viewport, "_find_scene_viewer", lambda pane_name=None: viewer)
    monkeypatch.setattr(rendering, "_find_flipbook_output", lambda path, frame: path)
    monkeypatch.setattr(rendering, "require_ui", lambda *a, **k: None)
    monkeypatch.setattr(hou, "node", lookup, raising=False)
    monkeypatch.setattr(hou, "ObjNode", _Obj, raising=False)
    monkeypatch.setattr(hou, "Vector3", _Vec, raising=False)
    monkeypatch.setattr(hou, "Matrix4", lambda rotation: rotation, raising=False)
    monkeypatch.setattr(hou.hmath, "buildRotate", _Rotation, raising=False)
    monkeypatch.setattr(hou, "BoundingBox", lambda *box: box, raising=False)
    monkeypatch.setattr(hou, "BoundingRect", lambda *box: box, raising=False)
    monkeypatch.setattr(hou, "sopNodeTypeCategory", lambda: "Sop", raising=False)
    monkeypatch.setattr(hou, "objNodeTypeCategory", lambda: "Object", raising=False)
    monkeypatch.setattr(hou.paneTabType, "SceneViewer", "SceneViewer", raising=False)
    monkeypatch.setattr(hou.paneTabType, "NetworkEditor", "NetworkEditor", raising=False)
    for name in ("Perspective", "Top", "Front", "Back", "Left", "Right", "Bottom"):
        monkeypatch.setattr(hou.geometryViewportType, name, name, raising=False)
    monkeypatch.setattr(hou.ui, "paneTabs", lambda: [viewer, editor], raising=False)
    monkeypatch.setattr(hou.text, "expandString", lambda s: s, raising=False)
    monkeypatch.setattr(hou.undos, "disabler", contextlib.nullcontext, raising=False)
    monkeypatch.setattr(hou, "frame", lambda: 1.0, raising=False)
    frames = []
    monkeypatch.setattr(hou, "setFrame", frames.append, raising=False)
    return types.SimpleNamespace(
        root=root,
        geo1=geo1,
        box1=box1,
        cam=cam,
        viewer=viewer,
        editor=editor,
        frames=frames,
        out=str(tmp_path / "shot.png"),
        tmp=tmp_path,
    )


###### #39 Perspective framing


class TestPerspectiveMaths:
    def test_half_fov_of_the_default_lens_is_22_5_degrees(self):
        assert math.degrees(viewport._half_fov(41.4214, 50.0, 1.0)) == pytest.approx(22.5, abs=0.01)

    def test_a_wide_frame_uses_the_narrower_vertical_angle(self):
        wide = viewport._half_fov(41.4214, 50.0, 2.0)
        assert math.tan(wide) == pytest.approx(math.tan(math.radians(22.5)) / 2.0, abs=1e-5)

    def test_an_unreadable_lens_falls_back(self):
        assert viewport._half_fov(None, 0, 1.0) == viewport._DEFAULT_HALF_FOV

    @pytest.mark.parametrize(
        ("angles", "eye"),
        [((0, 0), (0, 0, 10)), ((0, 90), (10, 0, 0)), ((-90, 0), (0, 10, 0))],
    )
    def test_the_eye_offset_follows_a_cam_objects_convention(self, angles, eye):
        assert viewport._eye_offset(*angles, 10.0) == pytest.approx(eye, abs=1e-9)

    def test_the_eye_is_outside_the_box(self):
        box = [-50, 0, -50, 50, 20, 50]  # a terrain tile
        plan = viewport._perspective_plan(box, (-10, 30), math.radians(22.5))
        radius = math.dist([0, 10, 0], [50, 20, 50])
        assert plan["distance"] >= radius / math.sin(math.radians(22.5))
        assert math.dist(plan["eye"], plan["pivot"]) == pytest.approx(plan["distance"], abs=1e-3)
        assert plan["pivot"] == [0.0, 10.0, 0.0]
        assert plan["framed_bounds"] == box

    def test_clearance_backs_off_a_view_that_looks_down(self):
        box = [-10, 0, -10, 10, 100, 10]
        plan = viewport._perspective_plan(box, (-20, 0), math.radians(22.5), clearance=50)
        assert plan["clearance_adjusted"] == "distance"
        assert plan["eye"][1] == pytest.approx(150.0, abs=1e-3)

    def test_clearance_tilts_a_level_view_down(self):
        box = [-10, 0, -10, 10, 10, 10]
        plan = viewport._perspective_plan(box, (0, 0), math.radians(22.5), clearance=5)
        assert plan["clearance_adjusted"] == "elevation"
        assert plan["rotation"][0] < 0
        assert plan["eye"][1] == pytest.approx(15.0, abs=1e-3)

    def test_clearance_already_met_changes_nothing(self):
        box = [-1, 0, -1, 1, 1, 1]
        plan = viewport._perspective_plan(box, (-60, 0), math.radians(22.5), clearance=0.1)
        assert "clearance_adjusted" not in plan
        assert plan["rotation"][0] == -60

    def test_look_at_sets_the_pivot(self):
        plan = viewport._perspective_plan(
            [0, 0, 0, 2, 2, 2], (0, 0), math.radians(22.5), look_at=(0, 0, 0)
        )
        assert plan["pivot"] == [0.0, 0.0, 0.0]


class TestDirectionAliases:
    @pytest.mark.parametrize("spelling", ["persp", "PERSP", " perspective ", "3d"])
    def test_persp_is_perspective(self, spelling):
        assert viewport._direction_name(spelling) == "perspective"

    def test_set_viewport_direction_accepts_persp(self, scene):
        result = viewport.set_viewport_direction("persp")
        assert result["direction"] == "persp"
        assert ("changeType", "Perspective") in scene.viewer.view.calls or scene.viewer.view.kind

    def test_the_refusal_names_the_angle_form(self, scene):
        with pytest.raises(ValueError, match=r"\[rx, ry\]"):
            viewport.set_viewport_direction("sideways")


class TestFramingFromAnAngle:
    def test_frame_all_with_a_direction_frames_in_perspective(self, scene):
        scene.viewer.view.kind = "Top"
        result = viewport.frame_all(bounds=[-1, 0, -1, 1, 2, 1], direction=[-30, 45])
        view = scene.viewer.view.view
        assert ("changeType", "Perspective") in scene.viewer.view.calls
        assert tuple(view.pivot()) == (0.0, 1.0, 0.0)
        assert view.translation()[2] == pytest.approx(result["framing"]["distance"])
        assert view.rotation().angles == (-30.0, 45.0, 0.0)
        assert view.rotation().is_transposed
        assert result["framed_bounds"] == [-1.0, 0.0, -1.0, 1.0, 2.0, 1.0]

    def test_frame_all_clearance_needs_a_direction(self, scene):
        with pytest.raises(ValueError, match="direction"):
            viewport.frame_all(bounds=[0, 0, 0, 1, 1, 1], clearance=2)

    def test_set_viewport_direction_angle_frames_the_viewed_object(self, scene):
        result = viewport.set_viewport_direction([-20, 0], clearance=1)
        # The viewer is in /obj/geo1, whose display SOP spans y 0..2.
        assert result["framing"]["framed_bounds"] == [-1.0, 0.0, -1.0, 1.0, 2.0, 1.0]
        assert result["framing"]["eye"][1] >= 3.0 - 1e-6
        assert "view" in result

    def test_direction_angles_and_rotation_are_refused_together(self, scene):
        with pytest.raises(ValueError, match="not both"):
            viewport.set_viewport_direction([-20, 0], rotation=[0, 0, 0])

    def test_a_camera_looked_through_is_still_refused(self, scene):
        scene.viewer.view.bound = scene.cam
        with pytest.raises(ValueError, match="looks through /obj/cam1"):
            viewport.set_viewport_direction([-20, 0])


###### #20 sizes


class TestSizes:
    @pytest.mark.parametrize(
        ("size", "cap", "fitted"),
        [
            ((1920, 1080), 960, (960, 540)),
            ((400, 800), 200, (100, 200)),
            ((300, 200), 800, (300, 200)),
        ],
    )
    def test_fit_size_scales_down_only(self, size, cap, fitted):
        assert viewport._fit_size(*size, cap) == fitted

    def test_max_size_alone_scales_the_viewports_own_size(self):
        view = _Viewport()
        assert viewport._flipbook_resolution(view, None, 400) == [400, 200]
        assert viewport._flipbook_resolution(view, None, 1000) is None
        assert viewport._flipbook_resolution(view, None, None) is None
        assert viewport._flipbook_resolution(view, [1000, 500], 500) == [500, 250]

    @pytest.mark.parametrize("bad", [[100], [0, 10], "big", [True, 3]])
    def test_bad_resolutions_are_refused(self, bad):
        with pytest.raises(ValueError, match="resolution"):
            viewport._resolution_arg(bad)

    @pytest.mark.parametrize("bad", [0, 8, True, "512"])
    def test_bad_max_sizes_are_refused(self, bad):
        with pytest.raises(ValueError, match="max_size"):
            viewport._max_size_arg(bad)

    def test_capture_screenshot_resolution_reaches_the_flipbook(self, scene, monkeypatch):
        sized = []
        monkeypatch.setattr(rendering, "_size_flipbook", lambda settings, res: sized.append(res))
        result = viewport.capture_screenshot(scene.out, max_size=200)
        assert sized == [[200, 100]]
        assert result["resolution"] == [200, 100]


###### #35 / #36 capture_screenshot


class TestCaptureOneNode:
    def test_the_node_is_drawn_alone_and_everything_is_put_back(self, scene):
        before = scene.viewer.snapshot()
        result = viewport.capture_screenshot(scene.out, node="/obj/geo1/box1")

        scratch = scene.root.children_made[0]
        merge = scratch.children_made[0]
        assert scratch.type_name == "geo" and merge.type_name == "object_merge"
        assert merge.parms["objpath1"].value == "/obj/geo1/box1"
        assert merge.parms["xformtype"].value == 1
        # During the flipbook: the viewer at /obj, drawing the scratch object only.
        assert scene.viewer.flipbooks == [{"pwd": "/obj", "pattern": scratch.name(), "bound": ""}]
        assert ("frameBoundingBox", (-1, 0, -1, 1, 2, 1)) in scene.viewer.view.calls
        # After: scratch gone, the viewer as it was, the source untouched.
        assert scratch.destroyed
        assert scene.viewer.snapshot() == before
        assert scene.box1.flags == []
        assert result["restored"] is True
        assert result["drawn"]["node"] == "/obj/geo1/box1"
        assert result["drawn"]["prims"] == 10 and result["drawn"]["points"] == 20

    def test_a_camera_set_for_the_shot_is_released_after(self, scene):
        viewport.capture_screenshot(scene.out, camera="/obj/cam1")
        assert scene.viewer.flipbooks[0]["bound"] == "/obj/cam1"
        assert scene.viewer.view.cameraPath() == ""

    def test_the_users_camera_comes_back_after_a_free_view_shot(self, scene):
        scene.viewer.view.bound = scene.cam
        result = viewport.capture_screenshot(
            scene.out, camera={"rotation": [-30, 20, 0], "pivot": [0, 1, 0], "distance": 5}
        )
        assert scene.viewer.flipbooks[0]["bound"] == ""
        assert scene.viewer.view.cameraPath() == "/obj/cam1"
        assert result["view_applied"]["view"]["distance"] == 5.0

    def test_network_pins_the_viewer_for_the_shot_only(self, scene):
        viewport.capture_screenshot(scene.out, network="/obj")
        assert scene.viewer.flipbooks[0]["pwd"] == "/obj"
        assert scene.viewer.pwd().path() == "/obj/geo1"

    def test_a_sop_passed_as_network_shows_its_network(self, scene):
        viewport.capture_screenshot(scene.out, network="/obj/geo1/box1")
        assert scene.viewer.flipbooks[0]["pwd"] == "/obj/geo1"

    def test_the_view_is_put_back_after_an_angle_shot(self, scene):
        scene.viewer.view.kind = "Top"
        old = scene.viewer.view.view
        viewport.capture_screenshot(
            scene.out, node="/obj/geo1/box1", camera={"direction": "persp", "clearance": 1}
        )
        now = scene.viewer.view.view
        assert scene.viewer.view.kind == "Top"
        assert (tuple(now.pivot()), tuple(now.translation())) == (
            tuple(old.pivot()),
            tuple(old.translation()),
        )
        assert now.rotation().angles == old.rotation().angles

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"node": "/obj/geo1/box1", "network": "/obj"}, "not both"),
            ({"camera": {"zoom": 2}}, "Unknown camera key"),
            ({"camera": {"direction": "sideways"}}, "Unknown direction"),
            ({"camera": 3}, "camera must be"),
            ({"node": "/nowhere"}, "Node not found"),
            ({"resolution": [10]}, "resolution"),
        ],
    )
    def test_bad_arguments_are_refused_before_anything_moves(self, scene, kwargs, match):
        with pytest.raises(ValueError, match=match):
            viewport.capture_screenshot(scene.out, **kwargs)
        assert scene.root.children_made == []
        assert scene.viewer.pwd_history == []

    def test_a_lop_node_is_refused(self, scene):
        scene.root.children_made.append(_Node("/obj/lopnet/x", category="Lop"))
        with pytest.raises(ValueError, match="SOP or object"):
            viewport.capture_screenshot(scene.out, node="/obj/lopnet/x")


class TestDrawnReport:
    def test_a_sop_network_names_its_display_node(self, scene):
        drawn = viewport._drawn_report(scene.viewer, scene.viewer.view)
        assert drawn["display_node"] == "/obj/geo1/box1"
        assert (drawn["prims"], drawn["points"]) == (10, 20)

    def test_guides_that_hide_geometry_are_named(self, scene):
        guides = viewport.hou.viewportGuide
        scene.viewer.view._settings.guides[guides.DisplayNodes] = False
        drawn = viewport._drawn_report(scene.viewer, scene.viewer.view)
        assert "display_nodes" in drawn["guides_off"]
        assert "Display Nodes guide is off" in drawn["note"]

    def test_an_empty_display_is_called_out(self, scene):
        scene.box1.geo = _Geo(prims=0, points=0)
        drawn = viewport._drawn_report(scene.viewer, scene.viewer.view)
        assert "no geometry" in drawn["note"]

    def test_object_level_lists_shown_objects_and_the_pattern(self, scene, monkeypatch):
        other = _Obj("/obj/rock", parent=scene.root, category="Object")
        other.display = _Node("/obj/rock/out", parent=other, geo=_Geo(prims=5, points=6))
        scene.geo1.isObjectDisplayed = lambda: True
        other.isObjectDisplayed = lambda: True
        scene.root.children_made.extend([scene.geo1, other])
        scene.viewer._pwd = scene.root
        scene.viewer.view._settings.pattern = "rock"
        drawn = viewport._drawn_report(scene.viewer, scene.viewer.view)
        assert drawn["objects"] == ["/obj/rock"]
        assert drawn["prims"] == 5
        assert drawn["visible_objects"] == "rock"


class TestPreservedView:
    def test_a_failed_capture_still_puts_the_viewer_back(self, scene, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("flipbook failed")

        monkeypatch.setattr(viewport, "_flipbook_still", boom)
        before = scene.viewer.snapshot()
        with pytest.raises(RuntimeError):
            viewport.capture_screenshot(scene.out, node="/obj/geo1/box1")
        assert scene.viewer.snapshot() == before
        assert scene.root.children_made[0].destroyed

    def test_what_cannot_be_put_back_is_named(self, scene):
        with viewport.preserved_view(scene.viewer) as state:
            scene.viewer.view._settings.setVisibleObjects("x")
            scene.viewer.view._settings.setVisibleObjects = None  # restore will fail
        assert state["not_restored"] == ["visible_objects"]


###### #37 capture_network_editor


def _item(x, y, w=1.0, h=0.3):
    item = MagicMock()
    item.position.return_value = (x, y)
    item.size.return_value = (w, h)
    return item


class TestNetworkEditorFrameAll:
    @pytest.fixture
    def editor(self, scene, monkeypatch):
        net = MagicMock()
        net.path.return_value = "/obj/geo1"
        net.children.return_value = [_item(0, 0), _item(10, -20)]
        net.networkBoxes.return_value = [_item(-5, 2, 4, 3)]
        net.stickyNotes.return_value = []
        scene.editor.pwd.return_value = MagicMock(**{"path.return_value": "/obj/elsewhere"})

        def cd(path):
            scene.editor.pwd.return_value = MagicMock(**{"path.return_value": path})

        scene.editor.cd.side_effect = cd
        rect = MagicMock()
        rect.min.return_value = (-6.0, -21.0)
        rect.max.return_value = (12.0, 6.0)
        scene.editor.visibleBounds.return_value = rect
        screen = MagicMock()
        screen.min.return_value = (0, 0)
        screen.max.return_value = (612, 400)
        scene.editor.screenBounds.return_value = screen
        monkeypatch.setattr(viewport, "viewer_stays_out_of_dops", contextlib.nullcontext)
        grabs = []
        monkeypatch.setattr(
            viewport, "_capture_pane_tab_qt", lambda pane, path, **kw: grabs.append(kw)
        )
        monkeypatch.setattr(viewport.hou, "node", lambda path: net if path == "/obj/geo1" else None)
        viewport._EDITOR_RESTORE.clear()
        yield types.SimpleNamespace(net=net, editor=scene.editor, rect=rect, grabs=grabs)
        viewport._EDITOR_RESTORE.clear()

    def test_every_item_is_framed_with_a_margin(self, editor, scene):
        reply = viewport.capture_network_editor(scene.out, network_path="/obj/geo1", frame="all")
        editor.editor.cd.assert_called_once_with("/obj/geo1")
        (box,), kwargs = editor.editor.setVisibleBounds.call_args
        assert kwargs["transition_time"] == 0.0
        # Union is (-5, -20) .. (11, 5); 5 % of 25 = 1.25 each side.
        assert box == pytest.approx((-6.25, -21.25, 12.25, 6.25))
        assert reply["framed"] == "all"
        assert reply["network_path"] == "/obj/geo1"

    def test_a_small_pane_warns_that_names_are_unreadable(self, editor, scene):
        reply = viewport.capture_network_editor(scene.out, network_path="/obj/geo1", frame="all")
        assert reply["pixels_per_unit"] == 34.0
        editor.rect.max.return_value = (60.0, 6.0)
        reply = viewport.capture_network_editor(scene.out, network_path="/obj/geo1", frame="all")
        assert "not be readable" in reply["readability_note"]

    def test_the_followup_grabs_then_puts_the_editor_back(self, editor, scene):
        old_bounds = editor.editor.visibleBounds.return_value
        reply = viewport.capture_network_editor(
            scene.out, network_path="/obj/geo1", frame="all", max_size=300
        )
        editor.editor.cd.reset_mock()
        data = viewport._recapture_network_editor(reply, {"max_size": 300})
        assert editor.grabs == [{"max_size": 300}, {"max_size": 300}]
        editor.editor.cd.assert_called_once_with("/obj/elsewhere")
        (box,), _ = editor.editor.setVisibleBounds.call_args
        assert box is old_bounds
        assert data["editor_restored"] is True
        assert viewport._EDITOR_RESTORE == {}

    def test_the_first_snapshot_wins_until_the_followup(self, editor, scene):
        viewport.capture_network_editor(scene.out, network_path="/obj/geo1", frame="all")
        first = viewport._EDITOR_RESTORE["pending"]
        viewport.capture_network_editor(scene.out, network_path="/obj/geo1", frame="all")
        assert viewport._EDITOR_RESTORE["pending"] is first

    def test_bad_frame_is_refused(self, editor, scene):
        with pytest.raises(ValueError, match="frame"):
            viewport.capture_network_editor(scene.out, frame="everything")


###### #38 render_sheet


class TestSheetTiles:
    def test_frames(self):
        tiles = rendering._sheet_tiles(1, 9, 4)
        assert [t["frame"] for t in tiles] == [1, 5, 9]
        assert tiles[0]["label"] == "f1"

    def test_views_take_names_and_angles(self):
        tiles = rendering._sheet_tiles(views=["front", "persp", [-45, 90]])
        assert [t["label"] for t in tiles] == ["front", "perspective", "-45,90"]
        assert tiles[2]["angles"] == (-45.0, 90.0, 0.0)

    def test_orbit(self):
        tiles = rendering._sheet_tiles(orbit=4)
        assert [t["angles"][1] for t in tiles] == [0.0, 90.0, 180.0, 270.0]
        assert all(t["angles"][0] == -25.0 for t in tiles)

    def test_values(self):
        tiles = rendering._sheet_tiles(values=["a", "b"], attribute="variant")
        assert [t["label"] for t in tiles] == ["variant=a", "variant=b"]

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({}, "exactly one"),
            ({"start": 1, "end": 2, "orbit": 3}, "exactly one"),
            ({"start": 1}, "both start and end"),
            ({"orbit": 1}, "orbit"),
            ({"views": "front"}, "views must be"),
            ({"start": 1, "end": 100}, "1 to 64"),
        ],
    )
    def test_refusals(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            rendering._sheet_tiles(**kwargs)

    def test_grid(self):
        assert rendering._sheet_grid(5, None) == (3, 2)
        assert rendering._sheet_grid(5, 10) == (5, 1)

    def test_group_expressions(self):
        assert rendering._group_for("variant", 3) == "@variant==3"
        assert rendering._group_for("name", "rock a") == '@name="rock a"'


class _Attrib:
    def __init__(self, data, values, size=1):
        self.data, self.values, self._size = data, values, size

    def dataType(self):
        return self.data

    def size(self):
        return self._size


class TestAttributeValues:
    def test_string_prim_values_are_unique_and_sorted(self, monkeypatch):
        monkeypatch.setattr(rendering.hou.attribData, "String", "String", raising=False)
        monkeypatch.setattr(rendering.hou.attribData, "Int", "Int", raising=False)
        geo = _Geo(attribs={("prim", "variant"): _Attrib("String", ["b", "a", "b"])})
        assert rendering._attribute_values(geo, "variant") == ("prims", ["a", "b"])

    def test_float_and_vector_attributes_are_refused(self, monkeypatch):
        monkeypatch.setattr(rendering.hou.attribData, "String", "String", raising=False)
        monkeypatch.setattr(rendering.hou.attribData, "Int", "Int", raising=False)
        geo = _Geo(attribs={("prim", "w"): _Attrib("Float", [0.5])})
        with pytest.raises(ValueError, match="integer or string"):
            rendering._attribute_values(geo, "w")
        geo = _Geo(attribs={("prim", "Cd"): _Attrib("Float", [], size=3)})
        with pytest.raises(ValueError, match="single-component"):
            rendering._attribute_values(geo, "Cd")

    def test_a_missing_attribute_is_named(self):
        with pytest.raises(ValueError, match="'variant'"):
            rendering._attribute_values(_Geo(), "variant")


class TestRenderSheetInHoudini:
    @pytest.fixture
    def stitched(self, scene, monkeypatch):
        calls = []

        def stitch(tiles, columns, output):
            calls.append((tiles, columns, output))
            return {"stitched_with": "PIL", "grid": [2, 1], "tile_resolution": [320, 240]}

        monkeypatch.setattr(rendering, "_stitch_tiles", stitch)
        monkeypatch.setattr(rendering, "_flipbook_still", self._flip(scene))
        return calls

    @staticmethod
    def _flip(scene):
        def flip(viewer, view, path, frame, resolution):
            scene.viewer.flipbooks.append(
                {**scene.viewer.snapshot(), "resolution": resolution, "view": view.view}
            )
            return path

        return flip

    def test_views_tile_from_each_angle_at_one_frame(self, scene, stitched):
        before = scene.viewer.snapshot()
        result = rendering.render_sheet(scene.out, views=["front", [-45, 90]], frame=12)
        assert result["tiles"] == 2 and result["labels"] == ["front", "-45,90"]
        rotations = [shot["view"].rotation().angles for shot in scene.viewer.flipbooks]
        assert rotations == [(0.0, 0.0, 0.0), (-45.0, 90.0, 0.0)]
        assert scene.frames == [12.0, 1.0]  # the frame went back
        assert scene.viewer.snapshot() == before
        assert result["success"] is True and result["stitched_with"] == "PIL"
        assert scene.viewer.flipbooks[0]["resolution"] == [320, 240]

    def test_frames_set_each_frame_and_restore_it(self, scene, stitched):
        rendering.render_sheet(scene.out, start=3, end=5)
        assert scene.frames == [3, 4, 5, 1.0]

    def test_by_attribute_cuts_each_piece_out_and_cleans_up(self, scene, stitched, monkeypatch):
        monkeypatch.setattr(rendering.hou.attribData, "String", "String", raising=False)
        monkeypatch.setattr(rendering.hou.attribData, "Int", "Int", raising=False)
        scene.box1.geo.attribs[("prim", "variant")] = _Attrib("String", ["pine", "oak", "pine"])
        result = rendering.render_sheet(scene.out, by_attribute="variant", node="/obj/geo1/box1")
        scratch = scene.root.children_made[0]
        blast = next(c for c in scratch.children_made if c.type_name == "blast")
        assert blast.parms["group"].history == ['@variant="oak"', '@variant="pine"']
        assert blast.parms["grouptype"].value == 4  # "prims", by index
        assert blast.parms["negate"].value == 1
        assert scratch.destroyed
        assert result["values"] == ["oak", "pine"]
        assert [shot["pattern"] for shot in scene.viewer.flipbooks] == [scratch.name()] * 2
        assert scene.viewer.snapshot()["pattern"] == "*"

    def test_by_attribute_needs_a_node(self, scene):
        with pytest.raises(ValueError, match="node="):
            rendering.render_sheet(scene.out, by_attribute="variant")

    def test_a_camera_path_does_not_fit_views(self, scene):
        with pytest.raises(ValueError, match="camera path"):
            rendering.render_sheet(scene.out, views=["front"], camera="/obj/cam1")

    def test_unstitched_tiles_are_listed(self, scene, monkeypatch):
        monkeypatch.setattr(rendering, "_flipbook_still", self._flip(scene))
        monkeypatch.setitem(sys.modules, "PIL", None)
        monkeypatch.setitem(sys.modules, "OpenImageIO", None)
        result = rendering.render_sheet(scene.out, orbit=2)
        assert result["success"] is False
        assert len(result["tile_paths"]) == 2
        assert "Neither PIL nor OpenImageIO" in result["note"]


class TestStitchWithPil:
    def test_tiles_are_pasted_in_a_grid_with_labels(self, tmp_path, monkeypatch):
        pasted, texts, saved = [], [], []

        class _Image:
            size = (4, 3)

            def convert(self, mode):
                return self

        class _Sheet:
            def paste(self, image, where):
                pasted.append(where)

            def save(self, path):
                saved.append(path)

        pil = types.ModuleType("PIL")
        pil.Image = types.SimpleNamespace(open=lambda path: _Image(), new=lambda *a: _Sheet())
        pil.ImageDraw = types.SimpleNamespace(
            Draw=lambda sheet: types.SimpleNamespace(
                rectangle=lambda *a, **k: None, text=lambda xy, label, **k: texts.append(label)
            )
        )
        monkeypatch.setitem(sys.modules, "PIL", pil)
        paths = []
        for index in range(3):
            path = tmp_path / f"t{index}.png"
            path.write_bytes(b"x")
            paths.append((str(path), f"f{index}"))
        out = str(tmp_path / "sheet.png")
        result = rendering._stitch_tiles(paths, None, out)
        assert result == {"stitched_with": "PIL", "grid": [2, 2], "tile_resolution": [4, 3]}
        assert pasted == [(0, 0), (4, 0), (0, 3)]
        assert texts == ["f0", "f1", "f2"] and saved == [out]


###### #40 render_viewport(visualize=True)


class TestVisualize:
    def test_a_heightfield_is_drawn_through_a_temporary_visualize(self, scene, monkeypatch):
        monkeypatch.setattr(rendering, "_flipbook_still", TestRenderSheetInHoudini._flip(scene))
        before = scene.viewer.snapshot()
        result = rendering.render_viewport(scene.out, visualize=True, instances="/obj/geo1")
        scratch = scene.root.children_made[0]
        made = [c.type_name for c in scratch.children_made]
        assert made == ["object_merge", "heightfield_visualize", "object_merge", "merge"]
        visualize = scratch.children_made[1]
        assert visualize.inputs[0] is scratch.children_made[0]
        assert ("display", True) in scratch.children_made[3].flags
        # The viewer's display SOP was used, and nothing of the user's moved.
        assert scratch.children_made[0].parms["objpath1"].value == "/obj/geo1/box1"
        assert scene.box1.flags == []
        assert scratch.destroyed and scene.viewer.snapshot() == before
        assert result["visualized"] is True and result["restored"] is True

    def test_outside_a_sop_network_a_node_is_asked_for(self, scene):
        scene.viewer._pwd = scene.root
        with pytest.raises(ValueError, match="pass node="):
            rendering.render_viewport(scene.out, visualize=True)


###### #8 HeightField Output files


class TestHeightfieldOutputs:
    def test_tile_names_match_houdinis_fileutils(self):
        names = outputs.tile_filenames("/t/h.png", 2, 2, naming=0)
        assert names == [
            "/t/h.tile_1001.png",
            "/t/h.tile_1002.png",
            "/t/h.tile_1011.png",
            "/t/h.tile_1012.png",
        ]
        assert outputs.tile_filenames("/t/h.png", 2, 1, naming=1, pad=2) == [
            "/t/h.u01_v01.png",
            "/t/h.u02_v01.png",
        ]
        assert outputs.tile_filenames("/t/h.png", 2, 1, naming=2) == [
            "/t/h.tile_1.png",
            "/t/h.tile_2.png",
        ]
        assert outputs.tile_filenames("/t/h.png", 1, 2, naming=3, flop=True) == [
            "/t/h_x0_y0.png",
            "/t/h_x1_y0.png",
        ]

    @staticmethod
    def _node(parms):
        node = MagicMock()
        node.type.return_value.nameComponents.return_value = ("", "", "heightfield_output", "")
        values = {}
        for name, value in parms.items():
            parm = MagicMock()
            parm.eval.return_value = value
            values[name] = parm
        node.parm.side_effect = values.get
        return node

    def test_the_output_parm_is_read_by_type(self, tmp_path):
        image = tmp_path / "height.png"
        image.write_bytes(b"png")
        entries = _REPORTED_OUTPUTS(self._node({"output": str(image), "tile_output": 0}))
        assert [(e["parm"], e["exists"]) for e in entries] == [("output", True)]

    def test_tiled_output_lists_the_tiles_on_disk(self, tmp_path):
        base = tmp_path / "height.png"
        (tmp_path / "height.tile_1001.png").write_bytes(b"a")
        (tmp_path / "height.tile_1002.png").write_bytes(b"b")
        node = self._node(
            {
                "output": str(base),
                "tile_output": 1,
                "actual_num_tilesx": 2,
                "actual_num_tilesy": 2,
                "file_naming": 0,
                "udim_stride": 10,
                "tile_padding": 1,
                "flop": 0,
            }
        )
        entries = _REPORTED_OUTPUTS(node)
        assert [os.path.basename(e["path"]) for e in entries] == [
            "height.tile_1001.png",
            "height.tile_1002.png",
        ]
        assert all(e["parm"] == "output[tile]" for e in entries)

    def test_an_empty_output_is_named(self):
        entries = _REPORTED_OUTPUTS(self._node({"output": ""}))
        assert entries == [{"parm": "output", "path": None, "exists": False, "empty": True}]


###### Client tools


class TestClientTools:
    async def test_capture_screenshot_sends_only_what_was_given(self, mock_ctx, mock_bridge):
        from fxhoudinimcp.tools.viewport import capture_screenshot

        await capture_screenshot(
            mock_ctx, output_path="a.png", node="/obj/g/box1", camera={"direction": [-30, 45]}
        )
        mock_bridge.execute.assert_called_once_with(
            "viewport.capture_screenshot",
            {"output_path": "a.png", "node": "/obj/g/box1", "camera": {"direction": [-30, 45]}},
        )

    async def test_capture_network_editor_sends_frame_all(self, mock_ctx, mock_bridge):
        from fxhoudinimcp.tools.viewport import capture_network_editor

        await capture_network_editor(
            mock_ctx, output_path="n.png", network_path="/obj/g", frame="all", max_size=800
        )
        mock_bridge.execute.assert_called_once_with(
            "viewport.capture_network_editor",
            {"output_path": "n.png", "network_path": "/obj/g", "frame": "all", "max_size": 800},
        )

    async def test_render_sheet_renders_in_houdini_by_default(self, mock_ctx, mock_bridge):
        from fxhoudinimcp.tools.rendering import render_sheet

        await render_sheet(mock_ctx, views=["front", "top"], node="/obj/g/out", output_path="s.png")
        args, kwargs = mock_bridge.execute.call_args
        assert args == (
            "rendering.render_sheet",
            {"output_path": "s.png", "views": ["front", "top"], "node": "/obj/g/out"},
        )
        assert kwargs["timeout"] >= 300

    async def test_the_hython_path_takes_frames_only(self, mock_ctx):
        from fxhoudinimcp.tools.rendering import render_sheet

        with pytest.raises(ValueError, match="frame range only"):
            await render_sheet(mock_ctx, start=1, end=2, views=["front"], hython=True)
        with pytest.raises(ValueError, match="1 to 64"):
            await render_sheet(mock_ctx, start=1, end=200)

    async def test_the_hython_path_still_runs_the_child(self, mock_ctx, monkeypatch, tmp_path):
        import json

        from fxhoudinimcp.tools import rendering as tool

        snapshot = tmp_path / "snap.hip"
        snapshot.write_text("hip")
        bridge = MagicMock()
        bridge.execute = AsyncMock(return_value={"snapshot": str(snapshot), "hip_file": "a.hip"})
        monkeypatch.setattr(tool, "_get_bridge", lambda ctx: bridge)
        monkeypatch.setattr(tool, "_hython_for", lambda version: "hython")
        process = MagicMock(returncode=0)
        line = "__MCP_RESULT__ " + json.dumps({"frames": [1, 2]})
        process.communicate = AsyncMock(return_value=(line.encode(), b""))
        monkeypatch.setattr(tool.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
        result = await tool.render_sheet(mock_ctx, start=1, end=2, hython=True)
        assert result["frames"] == [1, 2]
        bridge.execute.assert_called_once_with("scene.write_snapshot")

    async def test_frame_all_and_direction_send_angles(self, mock_ctx, mock_bridge):
        from fxhoudinimcp.tools.viewport import frame_all, set_viewport_direction

        await frame_all(mock_ctx, node_paths=["/obj/t"], direction=[-30, 45], clearance=5)
        mock_bridge.execute.assert_called_with(
            "viewport.frame_all",
            {"node_paths": ["/obj/t"], "direction": [-30, 45], "clearance": 5},
        )
        await set_viewport_direction(mock_ctx, look_at=[0, 1, 0], clearance=2)
        mock_bridge.execute.assert_called_with(
            "viewport.set_viewport_direction", {"look_at": [0, 1, 0], "clearance": 2}
        )
