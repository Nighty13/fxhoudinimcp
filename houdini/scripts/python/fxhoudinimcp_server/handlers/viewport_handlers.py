"""Viewport and UI handlers for FXHoudini-MCP.

Provides tools for inspecting and controlling Houdini's viewport panes,
network editor navigation, display modes, camera assignment, and
screenshot capture of various pane types.
"""

from __future__ import annotations

# Built-in
import contextlib
import logging
import math
import os

# Third-party
import hou

# Internal
from fxhoudinimcp_server.dispatcher import register_followup, register_handler
from fxhoudinimcp_server.ui import (
    keep_viewer_state,
    require_ui,
    selection_hidden,
    set_other_objects,
    show_viewed_object,
    viewer_context,
    viewer_stays_out_of_dops,
)

logger = logging.getLogger(__name__)


def _no_mplay(settings) -> None:
    """Keep a flipbook from launching MPlay.

    MPlay is a child process of Houdini and inherits its file descriptors, the
    hwebserver listening socket among them. After Houdini exits, that MPlay
    keeps the port open and the next Houdini's auto-start fails with "an
    existing server is running on port N". The frame is written to disk either
    way; a viewer nobody asked for is not worth losing the next session's
    server.
    """
    with contextlib.suppress(Exception):
        settings.outputToMPlay(False)


def _grab_native_surface(rect):
    """Grab the screen-space *rect* from the native window drawn there.

    The smallest visible native widget (one with its own window handle) that
    holds the whole rect is grabbed through that handle, which reads the
    surface's own content even when another application's window covers
    Houdini. Found by walking Houdini's widgets, not by widgetAt(): that
    answers None when another application is on top (measured on Houdini
    22.0.508 under a maximised Claude window). None when nothing holds the
    rect or the grab is empty, so the caller falls back to the other paths.
    """
    try:
        from PySide6 import QtCore, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtWidgets
    app = QtWidgets.QApplication.instance()
    if app is None:
        return None
    with contextlib.suppress(Exception):
        hou.ui.triggerUpdate()
    app.processEvents()
    widget = None
    smallest = None
    for top in app.topLevelWidgets():
        for candidate in [top, *top.findChildren(QtWidgets.QWidget)]:
            if candidate.windowHandle() is None or not candidate.isVisible():
                continue
            area = QtCore.QRect(candidate.mapToGlobal(QtCore.QPoint(0, 0)), candidate.size())
            if not area.contains(rect):
                continue
            size = area.width() * area.height()
            if smallest is None or size < smallest:
                widget, smallest = candidate, size
    if widget is None:
        return None
    screen = widget.screen() if hasattr(widget, "screen") else app.primaryScreen()
    local = widget.mapFromGlobal(rect.topLeft())
    pixmap = screen.grabWindow(
        int(widget.winId()), local.x(), local.y(), rect.width(), rect.height()
    )
    if pixmap is None or pixmap.isNull():
        return None
    return pixmap


def _capture_pane_tab_qt(pane_tab, output_path: str, max_size: int | None = None) -> None:
    """Capture a pane tab screenshot via Qt, scaled down to *max_size* if larger.

    Houdini 20.x exposed PaneTab.qtParentWidget(); Houdini 21 removed it
    in favor of qtParentWindow()/qtScreenGeometry(), so fall back to
    grabbing the pane's screen region.
    """
    pixmap = None

    if hasattr(pane_tab, "qtParentWidget"):
        try:
            widget = pane_tab.qtParentWidget()
        except Exception:
            widget = None
        if widget is not None:
            pixmap = widget.grab()

    if pixmap is None and hasattr(pane_tab, "qtScreenGeometry"):
        # Houdini 21+ draws its whole UI into one native OpenGL surface
        # (RE_OpenGLDrawable). QWidget.grab() cannot read it back and returns
        # an uninitialised buffer: a noise image on Windows (network editor,
        # Houdini 22.0.508). Grabbing through the surface's native window
        # handle reads what the compositor holds for it instead.
        pixmap = _grab_native_surface(pane_tab.qtScreenGeometry())
    if pixmap is None and hasattr(pane_tab, "qtScreenGeometry"):
        rect = pane_tab.qtScreenGeometry()
        try:
            from PySide6 import QtCore, QtGui
        except ImportError:
            from PySide2 import QtCore, QtGui
        # Render the pane's region from Houdini's own window. A screen grab
        # returns whatever is on top of Houdini at that moment: a terminal, a
        # browser, a white dialog (the blank capture in #38).
        window = None
        with contextlib.suppress(Exception):
            panel = pane_tab.pane().floatingPanel()
            window = hou.qt.floatingPanelWindow(panel) if panel else hou.qt.mainWindow()
        if window is not None:
            local = QtCore.QRect(window.mapFromGlobal(rect.topLeft()), rect.size())
            pixmap = window.grab(local)

    if pixmap is None and hasattr(pane_tab, "qtScreenGeometry"):
        screens = QtGui.QGuiApplication.screens()
        screen = QtGui.QGuiApplication.primaryScreen()
        for candidate in screens:
            if candidate.geometry().contains(rect.center()):
                screen = candidate
                break
        geometry = screen.geometry()
        pixmap = screen.grabWindow(
            0,
            rect.x() - geometry.x(),
            rect.y() - geometry.y(),
            rect.width(),
            rect.height(),
        )

    if pixmap is None:
        raise RuntimeError(
            f"Cannot capture pane '{pane_tab.name()}': no Qt capture API "
            f"available in this Houdini build."
        )

    if max_size:
        fitted = _fit_size(pixmap.width(), pixmap.height(), max_size)
        if fitted != (pixmap.width(), pixmap.height()):
            try:
                from PySide6 import QtCore
            except ImportError:
                from PySide2 import QtCore
            pixmap = pixmap.scaled(
                fitted[0],
                fitted[1],
                QtCore.Qt.IgnoreAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )

    if not pixmap.save(output_path):
        raise RuntimeError(
            f"Failed to save screenshot to '{output_path}'. "
            f"Ensure the path is writable and the format is supported (e.g. .png, .jpg)."
        )


def _viewer_stage(scene_viewer):
    """The USD stage a Solaris viewer is displaying, or None.

    Tried in order because which of these is a LOP depends on what the user last
    clicked: the current node, the network the viewer is in, and that network's
    display node.
    """
    candidates = []
    with contextlib.suppress(Exception):
        candidates.append(scene_viewer.currentNode())
    with contextlib.suppress(Exception):
        candidates.append(scene_viewer.pwd())
    with contextlib.suppress(Exception):
        candidates.append(scene_viewer.pwd().displayNode())
    for node in candidates:
        if node is None or not hasattr(node, "stage"):
            continue
        with contextlib.suppress(Exception):
            stage = node.stage()
            if stage is not None:
                return stage
    return None


def _camera_prims(stage, limit: int = 12) -> list[str]:
    """Camera prim paths on a stage, for error messages worth reading."""
    found: list[str] = []
    with contextlib.suppress(Exception):
        for prim in stage.Traverse():
            if str(prim.GetTypeName()) == "Camera":
                found.append(str(prim.GetPath()))
                if len(found) >= limit:
                    break
    return found


def _is_scene_graph_view(scene_viewer) -> bool:
    """Whether Hydra delegates apply at all.

    hydraRenderers() raises "Specified view is not a scene graph view" in an
    object-level viewport, which is a different situation from a build that has
    no such API, and reporting them the same way sent a caller looking for a
    Houdini upgrade instead of a Solaris viewport.
    """
    try:
        scene_viewer.hydraRenderers()
    except Exception:
        return False
    return True


###### viewport.list_panes


def list_panes() -> dict:
    """List all visible pane tabs, their types, and associated information."""
    require_ui("list Houdini's panes")
    pane_tabs = hou.ui.paneTabs()
    panes = []
    for pt in pane_tabs:
        info = {
            "name": pt.name(),
            "type": pt.type().name(),
            "is_current_tab": pt.isCurrentTab(),
        }
        # For scene viewers, add viewport info
        if pt.type() == hou.paneTabType.SceneViewer:
            try:
                cur_vp = pt.curViewport()
                info["current_viewport"] = cur_vp.name()
                info["viewport_count"] = len(pt.viewports())
            except (hou.OperationFailed, hou.ObjectWasDeleted, AttributeError) as e:
                logger.debug("Could not read viewport info for pane '%s': %s", pt.name(), e)
        # For network editors, add current path
        if pt.type() == hou.paneTabType.NetworkEditor:
            try:
                info["current_path"] = pt.pwd().path()
            except (hou.OperationFailed, hou.ObjectWasDeleted, AttributeError) as e:
                logger.debug("Could not read network editor path for pane '%s': %s", pt.name(), e)
        panes.append(info)

    return {
        "panes": panes,
        "count": len(panes),
    }


###### viewport.get_viewport_info


def get_viewport_info(pane_name: str = None) -> dict:
    """Get current viewport settings including camera, display mode, and view transform.

    Args:
        pane_name: Optional pane tab name. If None, uses the first Scene Viewer found.
    """
    scene_viewer = _find_scene_viewer(pane_name)
    viewport = scene_viewer.curViewport()

    info = {
        "pane_name": scene_viewer.name(),
        "viewport_name": viewport.name(),
    }

    # Camera
    try:
        cam = viewport.camera()
        info["camera"] = cam.path() if cam is not None else None
    except (hou.OperationFailed, hou.ObjectWasDeleted, AttributeError) as e:
        logger.debug("Could not read viewport camera: %s", e)
        info["camera"] = None

    # camera() returns a hou.Node, so it is None for a USD camera prim even when
    # the viewport is looking through one. cameraPath() answers for both, and
    # without it there was no way to ask "what am I actually looking through".
    info["camera_path"] = None
    with contextlib.suppress(Exception):
        info["camera_path"] = viewport.cameraPath() or None

    # cameraPath() is an echo of whatever was last set, and Houdini will happily
    # echo a path that does not exist. Repeating it unchecked would make this tool
    # a second source of the same false confidence, so resolve it: as a node, or
    # as a prim on the viewer's stage.
    if info["camera_path"]:
        resolved = hou.node(info["camera_path"]) is not None
        if not resolved:
            stage = _viewer_stage(scene_viewer)
            if stage is not None:
                with contextlib.suppress(Exception):
                    prim = stage.GetPrimAtPath(info["camera_path"])
                    resolved = bool(prim and prim.IsValid())
        info["camera_path_resolves"] = resolved

    # The active Hydra delegate. Nothing reported this before, so a viewport that
    # had silently reverted to GL looked identical to one rendering in Karma
    # until someone eyeballed a screenshot.
    info["renderer"] = None
    with contextlib.suppress(Exception):
        info["renderer"] = scene_viewer.currentHydraRenderer()
    with contextlib.suppress(Exception):
        info["available_renderers"] = list(scene_viewer.hydraRenderers())

    # Display mode / shading
    try:
        settings = viewport.settings()
        display_set = settings.displaySet(hou.displaySetType.SceneObject)
        info["shading_mode"] = str(display_set.shadedMode())
    except (hou.OperationFailed, AttributeError) as e:
        logger.debug("Could not read shading mode: %s", e)
        info["shading_mode"] = None

    # View transform (model-view matrix)
    try:
        xform = viewport.viewTransform()
        info["view_transform"] = [list(row) for row in xform.asTupleOfTuples()]
    except (hou.OperationFailed, AttributeError) as e:
        logger.debug("Could not read view transform: %s", e)
        info["view_transform"] = None

    # Viewport type (perspective, top, front, right, UV, etc.)
    try:
        info["viewport_type"] = str(viewport.type())
    except (hou.OperationFailed, AttributeError) as e:
        logger.debug("Could not read viewport type: %s", e)
        info["viewport_type"] = None

    return info


###### viewport.set_viewport_camera


def set_viewport_camera(
    camera_path: str,
    pane_name: str = None,
) -> dict:
    """Set the viewport to look through a specific camera.

    Args:
        camera_path: Path to the camera node (e.g. '/obj/cam1').
        pane_name: Optional pane tab name.
    """
    scene_viewer = _find_scene_viewer(pane_name)
    viewport = scene_viewer.curViewport()

    # A USD camera prim is not a hou.node, so resolving through hou.node() alone
    # made every Solaris shot unframeable: the tool raised "Camera node not
    # found" for a path that is perfectly valid on the stage. A recorded session
    # lost nine execute_python calls to this and still shipped the wrong framing.
    cam_node = hou.node(camera_path)
    if cam_node is not None:
        viewport.setCamera(cam_node)
        # camera() returns the node, so comparing it is real verification.
        bound = None
        with contextlib.suppress(Exception):
            bound = viewport.camera()
        if bound is None or bound.path() != cam_node.path():
            raise RuntimeError(
                f"Asked to look through '{camera_path}' but the viewport reports "
                f"'{bound.path() if bound else None}'."
            )
        return {
            "success": True,
            "verified": True,
            "camera_path": cam_node.path(),
            "is_usd_prim": False,
            "pane_name": scene_viewer.name(),
            "viewport_name": viewport.name(),
        }

    # A USD camera prim is not a hou.node, so resolving through hou.node() alone
    # made every Solaris shot unframeable. But cameraPath() cannot verify the
    # result either: it is a pure echo, and setCamera("/cameras/does_not_exist")
    # returns without error and leaves cameraPath() reporting that exact
    # nonexistent path. Verification has to be a stage lookup.
    stage = _viewer_stage(scene_viewer)
    if stage is None:
        raise ValueError(
            f"'{camera_path}' is not a node, and this viewport has no USD stage "
            f"to look it up on. A Solaris camera prim requires the viewer to be "
            f"in a LOP network."
        )
    prim = stage.GetPrimAtPath(camera_path)
    if prim is None or not prim.IsValid():
        cameras = _camera_prims(stage)
        raise ValueError(
            f"No prim at '{camera_path}' on this stage."
            + (f" Camera prims present: {cameras}" if cameras else " The stage has no cameras.")
        )
    type_name = str(prim.GetTypeName())
    if type_name != "Camera":
        raise ValueError(
            f"'{camera_path}' is a {type_name or 'typeless'} prim, not a Camera. "
            f"Camera prims present: {_camera_prims(stage)}"
        )

    viewport.setCamera(camera_path)
    echoed = None
    with contextlib.suppress(Exception):
        echoed = viewport.cameraPath() or None
    if echoed is not None and echoed.rstrip("/") != camera_path.rstrip("/"):
        raise RuntimeError(
            f"Asked to look through '{camera_path}' but the viewport is on '{echoed}'."
        )

    return {
        "success": True,
        "verified": True,
        "camera_path": camera_path,
        "is_usd_prim": True,
        "prim_type": type_name,
        "pane_name": scene_viewer.name(),
        "viewport_name": viewport.name(),
    }


###### viewport.set_viewport_display


# color_scheme's spellings, mapped to hou.viewportColorScheme names.
_COLOR_SCHEMES = {
    "dark": "Dark",
    "darkgrey": "DarkGrey",
    "dark_grey": "DarkGrey",
    "grey": "Grey",
    "gray": "Grey",
    "light": "Light",
}


def set_viewport_display(
    display_mode: str = None,
    pane_name: str = None,
    environment_background: bool = None,
    reference_plane: bool = None,
    point_size: float = None,
    color_scheme: str = None,
) -> dict:
    """Set how the viewport draws: shading, background, grid, point size, colours.

    Args:
        display_mode: One of 'wireframe', 'shaded', 'smooth', 'smooth_wire',
            'hidden_line', 'flat', 'flat_wire', 'point'.
        pane_name: Optional pane tab name.
        environment_background: Show (True) or hide (False) an environment
            light's map behind the scene, on every view of the viewer.
        reference_plane: Show (True) or hide (False) the reference plane (the
            grid).
        point_size: Diameter in pixels of particles drawn as points, on every
            view of the viewer.
        color_scheme: 'dark', 'darkgrey', 'grey' or 'light', on every view.

    Each setting is read back rather than echoed: the per-view ones as a
    ``{view name: value}`` map.
    """
    asked = (display_mode, environment_background, reference_plane, point_size, color_scheme)
    if all(value is None for value in asked):
        raise ValueError(
            "Pass display_mode, environment_background, reference_plane, point_size "
            "or color_scheme."
        )
    # Refused before anything is touched, so a bad value never half-applies a call.
    scheme = None
    if color_scheme is not None:
        scheme = _COLOR_SCHEMES.get(str(color_scheme).strip().lower().replace(" ", ""))
        if scheme is None:
            raise ValueError(
                f"Unknown color_scheme '{color_scheme}'. Supported: dark, darkgrey, grey, light."
            )
    size = None
    if point_size is not None:
        try:
            size = float(point_size)
        except (TypeError, ValueError):
            size = 0.0
        if isinstance(point_size, bool) or not size > 0:
            raise ValueError(f"point_size must be a positive number of pixels, got {point_size!r}")

    mode_map = {
        "wireframe": hou.glShadingType.Wire,
        "wire": hou.glShadingType.Wire,
        "shaded": hou.glShadingType.Smooth,
        "smooth": hou.glShadingType.Smooth,
        "smooth_wire": hou.glShadingType.SmoothWire,
        "hidden_line": hou.glShadingType.HiddenLineGhost,
        "flat": hou.glShadingType.Flat,
        "flat_wire": hou.glShadingType.FlatWire,
        "matcap": hou.glShadingType.MatCap,
        "matcap_wire": hou.glShadingType.MatCapWire,
    }

    gl_mode = None
    if display_mode is not None:
        gl_mode = mode_map.get(display_mode.lower())
        if gl_mode is None:
            raise ValueError(
                f"Unknown display mode: '{display_mode}'. Supported modes: {list(mode_map.keys())}"
            )

    scene_viewer = _find_scene_viewer(pane_name)
    result = {"success": True, "pane_name": scene_viewer.name()}

    if gl_mode is not None:
        settings = scene_viewer.curViewport().settings()
        display_set = settings.displaySet(hou.displaySetType.SceneObject)
        display_set.setShadedMode(gl_mode)
        result["display_mode"] = display_mode

    if environment_background is not None:
        # The flag is kept per view, not per viewer: a quad layout carries
        # four, and setting it on curViewport() alone leaves the map behind
        # the other three. Each view's state is read back, not echoed.
        shown = {}
        for view in scene_viewer.viewports():
            settings = view.settings()
            settings.setDisplayEnvironmentBackgroundImage(bool(environment_background))
            shown[view.name()] = settings.displayEnvironmentBackgroundImage()
        result["environment_background"] = shown

    if reference_plane is not None:
        # One plane per viewer, not per view.
        plane = scene_viewer.referencePlane()
        plane.setIsVisible(bool(reference_plane))
        result["reference_plane"] = plane.isVisible()

    if size is not None:
        # GeometryViewportSettings has particlePointSize() and no setter, so a
        # session looking for one read back 3.0 and gave up. viewdisplay -p on
        # the viewer sets it on every view. The viewer keeps one set per
        # context: .world for objects and SOPs, .solaris in a LOP network.
        # .world from /stage read back 3.0 on every view and changed the size
        # the object viewer showed later instead (22.0.368).
        in_lops = scene_viewer.pwd().childTypeCategory() == hou.lopNodeTypeCategory()
        context = "solaris" if in_lops else "world"
        viewer_path = f"{hou.ui.curDesktop().name()}.{scene_viewer.name()}.{context}"
        _, error = hou.hscript(f"viewdisplay -p {size} {viewer_path}")
        if error.strip():
            raise RuntimeError(f"viewdisplay -p failed: {error.strip()}")
        result["point_size"] = {
            view.name(): view.settings().particlePointSize() for view in scene_viewer.viewports()
        }

    if scheme is not None:
        schemes = {}
        for view in scene_viewer.viewports():
            settings = view.settings()
            settings.setColorScheme(getattr(hou.viewportColorScheme, scheme))
            schemes[view.name()] = str(settings.colorScheme()).rsplit(".", 1)[-1]
        result["color_scheme"] = schemes

    return result


###### viewport.set_viewport_direction


def _vector3(value, name: str) -> tuple:
    """Three floats from *value*, or a ValueError naming *name*."""
    try:
        numbers = tuple(float(v) for v in value)
    except (TypeError, ValueError):
        numbers = ()
    if len(numbers) != 3:
        raise ValueError(f"{name} must be three numbers, got {value!r}")
    return numbers


def _looked_through(viewport) -> tuple[str | None, bool]:
    """(camera the viewport looks through, whether it is a USD prim), or (None, False).

    A USD camera prim is no node: camera() is None there and only cameraPath()
    names it.
    """
    bound = viewport.camera()
    if bound is not None:
        return bound.path(), False
    path = viewport.cameraPath() or None
    return path, path is not None


#: Direction names -> hou.geometryViewportType attribute names.
_DIRECTION_TYPES = {
    "front": "Front",
    "back": "Back",
    "top": "Top",
    "bottom": "Bottom",
    "left": "Left",
    "right": "Right",
    "perspective": "Perspective",
}
#: Short spellings agents reach for. "persp" was refused (4b #11).
_DIRECTION_ALIASES = {"persp": "perspective", "3d": "perspective"}
#: The same directions as [rx, ry] of a perspective view looking from that side
#: (a cam object's convention: r=0 looks down -Z from the front). Used where a
#: direction frames bounds in perspective: frame_all(direction=), sheet views.
_DIRECTION_ANGLES = {
    "front": (0.0, 0.0),
    "back": (0.0, 180.0),
    "right": (0.0, 90.0),
    "left": (0.0, -90.0),
    "top": (-90.0, 0.0),
    "bottom": (90.0, 0.0),
    "perspective": (-30.0, 45.0),
}


def _direction_name(direction) -> str | None:
    """The canonical direction name for *direction*, aliases resolved, or None."""
    name = str(direction).strip().lower()
    name = _DIRECTION_ALIASES.get(name, name)
    return name if name in _DIRECTION_TYPES else None


def _unknown_direction(direction) -> ValueError:
    return ValueError(
        f"Unknown direction '{direction}'. Supported: {list(_DIRECTION_TYPES)} "
        f"(also {sorted(_DIRECTION_ALIASES)}), or [rx, ry] degrees for a perspective view "
        "framing the scene from that angle."
    )


def _angles(value, name: str) -> tuple:
    """[rx, ry] or [rx, ry, rz] degrees as a 3-tuple, or a ValueError naming *name*."""
    if isinstance(value, str):
        canonical = _direction_name(value)
        if canonical is None:
            raise _unknown_direction(value)
        return (*_DIRECTION_ANGLES[canonical], 0.0)
    try:
        numbers = tuple(float(v) for v in value)
    except (TypeError, ValueError):
        numbers = ()
    if len(numbers) not in (2, 3) or any(isinstance(v, bool) for v in value):
        raise ValueError(f"{name} must be [rx, ry] or [rx, ry, rz] degrees, got {value!r}")
    return numbers if len(numbers) == 3 else (*numbers, 0.0)


def _positive(value, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if isinstance(value, bool) or not number > 0:
        raise ValueError(f"{name} must be a positive number, got {value!r}")
    return number


def _number(value, name: str) -> float:
    try:
        if isinstance(value, bool):
            raise TypeError
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {value!r}") from None


def set_viewport_direction(
    direction=None,
    pane_name: str = None,
    rotation: list = None,
    pivot: list = None,
    distance: float = None,
    look_at: list = None,
    clearance: float = None,
) -> dict:
    """Set the viewport to a standard viewing direction, or place the free view.

    Args:
        direction: One of 'front', 'back', 'top', 'bottom', 'left', 'right',
            'perspective' ('persp' too), or [rx, ry] degrees: a perspective
            view that frames the scene from that angle, its distance worked
            out from the bounds and the field of view.
        pane_name: Optional pane tab name.
        rotation: [rx, ry, rz] in degrees: the free view's rotation about its
            pivot.
        pivot: [x, y, z] the free view orbits.
        distance: How far the free view sits from its pivot along its view
            axis (the dolly). A sideways pan is kept and not counted, so with
            a panned view the eye is farther from the pivot than this.
        look_at: [x, y, z] to aim a perspective view at; the distance frames
            the scene around it unless *distance* is given.
        clearance: Keep the eye at least this far above the top of the
            scene's bounds (terrain): the view is pulled back, or tilted down,
            until it is. ``framing.clearance_adjusted`` says which.

    rotation, pivot and distance place the viewport's own (non-camera) view,
    after *direction* if both are given, and the reply reads it back as
    ``view``. A viewport looking through a camera is refused: what it shows
    is the camera, so the camera is what to move.
    """
    framed = (
        (direction is not None and not isinstance(direction, str))
        or look_at is not None
        or clearance is not None
    )
    free_view = framed or rotation is not None or pivot is not None or distance is not None
    if direction is None and not free_view:
        raise ValueError("Pass direction, or rotation / pivot / distance for the free view.")
    view_type = None
    frame_angles = None
    if isinstance(direction, str):
        canonical = _direction_name(direction)
        if canonical is None:
            raise _unknown_direction(direction)
        view_type = getattr(hou.geometryViewportType, _DIRECTION_TYPES[canonical])
    elif direction is not None:
        frame_angles = _angles(direction, "direction")
        if rotation is not None:
            raise ValueError("Pass direction=[rx, ry] or rotation, not both.")
    # Checked before the viewer is touched, so a bad value never half-applies.
    angles = _vector3(rotation, "rotation") if rotation is not None else None
    centre = _vector3(pivot, "pivot") if pivot is not None else None
    target = _vector3(look_at, "look_at") if look_at is not None else None
    if framed and pivot is not None:
        raise ValueError(
            "pivot places the free view as it is; with look_at/direction=[rx, ry] "
            "the pivot is what the view frames. Pass look_at instead."
        )
    far = _positive(distance, "distance") if distance is not None else None
    lift = _number(clearance, "clearance") if clearance is not None else None

    scene_viewer = _find_scene_viewer(pane_name)
    viewport = scene_viewer.curViewport()
    if free_view:
        # setDefaultCamera() unbound a USD camera prim without a word (22.0.368).
        looks_through, is_prim = _looked_through(viewport)
        if looks_through:
            move = (
                "its LOP's parameters (set_parameters) or set_usd_attribute on the prim"
                if is_prim
                else "set_object_transform on the camera object"
            )
            raise ValueError(
                f"The viewport looks through {looks_through}: rotation/pivot/distance "
                f"place the viewport's own view. Move the camera instead, with {move}."
            )
    if view_type is not None:
        viewport.changeType(view_type)
        viewport.frameAll()

    result = {
        "success": True,
        "direction": direction,
        "pane_name": scene_viewer.name(),
        "viewport_name": viewport.name(),
    }
    if not free_view:
        return result

    if framed:
        box = _scene_bounds(scene_viewer)
        if box is None and target is None:
            raise ValueError("Nothing to frame: no displayed geometry. Pass look_at and distance.")
        turn = frame_angles or angles or _current_angles(viewport)
        result["framing"] = _apply_perspective(
            viewport, box, turn, look_at=target, clearance=lift, distance=far
        )
    else:
        _place_free_view(viewport, angles, centre, far)
    result["view"] = _read_view(viewport)
    return result


def _place_free_view(viewport, angles, centre, far) -> None:
    """Turn, re-pivot and dolly the viewport's own view; what is None is kept.

    The view's translation is a world position that the rotation turns about
    the pivot: the eye is pivot + R * (translation - pivot). Measured on
    22.0.429: moving only the pivot left the eye where it was, looking past
    the new pivot. So the eye's offset from the pivot (pan in x/y, distance
    in z) is what is kept, and the translation follows the pivot.
    """
    view = viewport.defaultCamera().stash()
    old_pivot = list(view.pivot())
    offset = [t - p for t, p in zip(view.translation(), old_pivot, strict=True)]
    if angles is not None:
        view.setRotation(_view_rotation(angles))
    if centre is not None or far is not None:
        new_pivot = list(centre) if centre is not None else old_pivot
        if far is not None:
            offset[2] = far
        view.setPivot(hou.Vector3(*new_pivot))
        view.setTranslation(hou.Vector3(*(p + o for p, o in zip(new_pivot, offset, strict=True))))
    viewport.setDefaultCamera(view)


def _view_rotation(angles):
    """The matrix setRotation() wants for [rx, ry, rz] degrees.

    setRotation() takes the transpose of what buildRotate() gives: as is, the
    view looked along the inverse turn, (0.5, 0.3, -0.8) where a cam object
    with the same r looks along (-0.47, -0.34, -0.81) (22.0.429).
    """
    rotate = hou.hmath.buildRotate(hou.Vector3(*angles))
    return rotate.extractRotationMatrix3().transposed()


def _read_view(viewport) -> dict:
    """The free view as it is now: rotation, pivot, distance (read back, not echoed)."""
    now = viewport.defaultCamera()
    now_pivot = list(now.pivot())
    return {
        "rotation": [
            round(v, 4) for v in hou.Matrix4(now.rotation().transposed()).extractRotates()
        ],
        "pivot": [round(v, 4) for v in now_pivot],
        "distance": round(now.translation()[2] - now_pivot[2], 4),
    }


def _current_angles(viewport) -> tuple:
    """The free view's [rx, ry, rz], or the default perspective angle when unreadable."""
    with contextlib.suppress(Exception):
        angles = tuple(
            float(v)
            for v in hou.Matrix4(viewport.defaultCamera().rotation().transposed()).extractRotates()
        )
        if len(angles) == 3:
            return angles
    return (*_DIRECTION_ANGLES["perspective"], 0.0)


###### Perspective framing


# A frame leaves this much room around the bounding sphere.
_FRAME_MARGIN = 1.05
# The Houdini free view's default lens (50 mm on a 41.4214 aperture): 45 degrees.
_DEFAULT_HALF_FOV = math.radians(22.5)
# Looking down at least this steeply, clearance is met by backing off.
_MIN_DROP_DEGREES = 10.0
# Never tilt steeper than this to meet clearance; back off as well instead.
_MAX_TILT_DEGREES = 60.0


def _half_fov(aperture, focal, aspect) -> float:
    """Half of the narrower field of view, in radians, of a lens on a *aspect* frame.

    The aperture is horizontal; a frame wider than tall (aspect > 1) has the
    narrower angle vertically. Falls back to 22.5 degrees when unreadable.
    """
    try:
        half_h = math.atan(float(aperture) / (2.0 * float(focal)))
    except (TypeError, ValueError, ZeroDivisionError):
        return _DEFAULT_HALF_FOV
    if not 0 < half_h < math.pi / 2:
        return _DEFAULT_HALF_FOV
    try:
        ratio = float(aspect)
    except (TypeError, ValueError):
        ratio = 1.0
    if ratio > 1.0:
        return math.atan(math.tan(half_h) / ratio)
    return half_h


def _eye_offset(rx: float, ry: float, distance: float) -> tuple:
    """Where the eye sits relative to the pivot for a view turned [rx, ry] degrees.

    A cam object's convention: r = 0 looks down -Z, so the eye is at +Z;
    rx < 0 looks down (the eye rises), ry turns about Y (90: from +X).
    """
    pitch, yaw = math.radians(rx), math.radians(ry)
    return (
        distance * math.cos(pitch) * math.sin(yaw),
        -distance * math.sin(pitch),
        distance * math.cos(pitch) * math.cos(yaw),
    )


def _box_corners(box) -> list:
    return [(x, y, z) for x in (box[0], box[3]) for y in (box[1], box[4]) for z in (box[2], box[5])]


def _perspective_plan(
    box,
    angles,
    half_fov: float,
    *,
    look_at=None,
    clearance=None,
    distance=None,
    margin: float = _FRAME_MARGIN,
) -> dict:
    """Pivot, rotation and distance for a perspective view of *box* from *angles*.

    The distance fits the box's bounding sphere (about *look_at* when given)
    inside the narrower half field of view, so the eye is always outside the
    box: a camera can no longer land inside a hill (4g #3). With *clearance*,
    the eye is also kept that far above the box top: a view looking down at
    least 10 degrees backs off; a flatter one is tilted down (to 60 degrees at
    most, backing off beyond that).
    """
    rx, ry, rz = (list(angles) + [0.0, 0.0, 0.0])[:3]
    if look_at is not None:
        centre = [float(v) for v in look_at]
    elif box is not None:
        centre = [(box[i] + box[i + 3]) / 2.0 for i in range(3)]
    else:
        raise ValueError("Nothing to frame: pass look_at or bounds.")
    radius = 0.0
    if box is not None:
        radius = max(math.dist(centre, corner) for corner in _box_corners(box))
    radius = max(radius, 0.5)
    far = float(distance) if distance is not None else radius / math.sin(half_fov) * margin
    adjusted = None
    if clearance is not None and box is not None:
        need = box[4] + float(clearance) - centre[1]
        drop = -math.sin(math.radians(rx))
        if far * drop < need:
            if drop >= math.sin(math.radians(_MIN_DROP_DEGREES)):
                far = need / drop
                adjusted = "distance"
            elif need / far <= math.sin(math.radians(_MAX_TILT_DEGREES)):
                rx = -math.degrees(math.asin(need / far))
                adjusted = "elevation"
            else:
                rx = -_MAX_TILT_DEGREES
                far = need / math.sin(math.radians(_MAX_TILT_DEGREES))
                adjusted = "elevation_and_distance"
    offset = _eye_offset(rx, ry, far)
    plan = {
        "rotation": [round(rx, 4), round(ry, 4), round(rz, 4)],
        "pivot": [round(v, 4) for v in centre],
        "distance": round(far, 4),
        "eye": [round(c + o, 4) for c, o in zip(centre, offset, strict=True)],
    }
    if box is not None:
        plan["framed_bounds"] = [round(v, 4) for v in box]
    if adjusted:
        plan["clearance_adjusted"] = adjusted
    return plan


def _viewport_pixels(viewport) -> tuple | None:
    """(width, height) of *viewport* in pixels, or None."""
    with contextlib.suppress(Exception):
        size = tuple(viewport.resolutionInPixels())
        if len(size) == 2 and all(isinstance(v, int) and v > 0 for v in size):
            return size
    return None


def _change_type(viewport, view_type, state: dict | None = None) -> None:
    """changeType(), recording the new type's own view first so a restore can put it back."""
    current = None
    with contextlib.suppress(Exception):
        current = viewport.type()
    if current is not None and current == view_type:
        return
    viewport.changeType(view_type)
    if state is not None:
        with contextlib.suppress(Exception):
            state["views"].append((view_type, viewport.defaultCamera().stash()))


def _apply_perspective(
    viewport, box, angles, *, look_at=None, clearance=None, distance=None, state=None
) -> dict:
    """Put the viewport's own view in perspective, framing *box* from *angles*."""
    _change_type(viewport, hou.geometryViewportType.Perspective, state)
    camera = viewport.defaultCamera()
    aperture = focal = None
    with contextlib.suppress(Exception):
        aperture, focal = camera.aperture(), camera.focalLength()
    pixels = _viewport_pixels(viewport)
    aspect = pixels[0] / pixels[1] if pixels else 1.0
    plan = _perspective_plan(
        box,
        angles,
        _half_fov(aperture, focal, aspect),
        look_at=look_at,
        clearance=clearance,
        distance=distance,
    )
    view = camera.stash()
    view.setRotation(_view_rotation(plan["rotation"]))
    pivot = plan["pivot"]
    view.setPivot(hou.Vector3(*pivot))
    view.setTranslation(hou.Vector3(pivot[0], pivot[1], pivot[2] + plan["distance"]))
    viewport.setDefaultCamera(view)
    return plan


def _scene_bounds(scene_viewer) -> list | None:
    """World bounds of what the viewer shows: the object it is in, else every displayed object."""
    network = None
    with contextlib.suppress(Exception):
        network = scene_viewer.pwd()
    owner = network
    with contextlib.suppress(Exception):
        while owner is not None and not isinstance(owner, hou.ObjNode):
            owner = owner.parent()
    if owner is not None:
        with contextlib.suppress(Exception):
            found = _world_bounds(owner)
            if found is not None:
                return found
    box = None
    with contextlib.suppress(Exception):
        for child in hou.node("/obj").children()[:500]:
            if not isinstance(child, hou.ObjNode) or not child.isObjectDisplayed():
                continue
            found = _world_bounds(child)
            if found is not None:
                box = found if box is None else _union(box, found)
    return box


def _union(a: list, b: list) -> list:
    return [min(a[i], b[i]) for i in range(3)] + [max(a[i], b[i]) for i in range(3, 6)]


###### viewport.set_viewport_renderer


def set_viewport_renderer(
    renderer: str,
    pane_name: str = None,
) -> dict:
    """Set the viewport's Hydra rendering delegate.

    In LOPs/Solaris the viewport can render through different Hydra delegates
    (GL, Storm, Karma CPU, Karma XPU, etc.) without writing to disk.

    Args:
        renderer: Renderer name — e.g. "GL", "Storm", "Karma CPU",
            "Karma XPU", "Houdini GL". Case-insensitive partial match.
        pane_name: Optional pane tab name.
    """
    scene_viewer = _find_scene_viewer(pane_name)
    viewport = scene_viewer.curViewport()

    # hou.SceneViewer.hydraRenderers/setHydraRenderer/currentHydraRenderer is the
    # documented pair for this. The previous implementation went through
    # viewport.settings().setRenderer() with an hscript fallback and, crucially,
    # never read the renderer back: it returned success whenever a setter did not
    # raise. A recorded session spent nine execute_python calls discovering that
    # the viewport was still on GL while this tool reported Karma, and every
    # screenshot it verified against was therefore the wrong image.
    available: list[str] = []
    with contextlib.suppress(Exception):
        available = list(scene_viewer.hydraRenderers())
    if not available:
        with contextlib.suppress(Exception):
            settings = viewport.settings()
            if hasattr(settings, "rendererNames"):
                available = list(settings.rendererNames())

    target = renderer.strip().lower()
    matched_name = next((n for n in available if n.lower() == target), None)
    if matched_name is None:
        matched_name = next((n for n in available if target in n.lower()), None)
    if matched_name is None and not available:
        matched_name = renderer
    if matched_name is None:
        raise ValueError(f"Renderer '{renderer}' not found. Available renderers: {available}")

    def _current() -> str | None:
        with contextlib.suppress(Exception):
            return scene_viewer.currentHydraRenderer()
        return None

    before = _current()
    attempts: list[str] = []
    for label, apply in (
        ("setHydraRenderer", lambda: scene_viewer.setHydraRenderer(matched_name)),
        (
            "settings.setRenderer",
            lambda: viewport.settings().setRenderer(matched_name),
        ),
        (
            "hscript viewdisplay",
            lambda: hou.hscript(f'viewdisplay -R "{matched_name}" {viewport.name()}'),
        ),
    ):
        try:
            apply()
        except Exception as exc:  # noqa: BLE001 - each route is best-effort
            attempts.append(f"{label}: {type(exc).__name__}")
            continue
        attempts.append(f"{label}: no error")
        if (_current() or "").lower() == matched_name.lower():
            break

    active = _current()
    # The renderer Houdini reports is the answer. Anything else is a guess about
    # whether a setter worked.
    applied = active is not None and active.lower() == matched_name.lower()
    if not applied and active is None:
        if not _is_scene_graph_view(scene_viewer):
            # Not a missing API: Hydra delegates only exist for a scene graph
            # view, so this request is meaningless in an object-level viewport
            # and saying "unverifiable" would hide a real mistake.
            raise ValueError(
                "This viewport is not a scene graph view, so it has no Hydra "
                "delegate to set. Hydra renderers (Karma, Storm) apply to a "
                "Solaris viewport: put the viewer in a LOP network first."
            )
        # A scene graph view with no readback: state genuinely unknown, so say so
        # rather than claiming the requested renderer is live.
        return {
            "success": True,
            "verified": False,
            "requested": matched_name,
            "renderer": None,
            "note": (
                "This Houdini exposes no currentHydraRenderer(), so the active "
                "renderer could not be confirmed. Capture a screenshot before "
                "trusting the viewport."
            ),
            "available_renderers": available,
            "attempts": attempts,
            "pane_name": scene_viewer.name(),
            "viewport_name": viewport.name(),
        }
    if not applied:
        raise RuntimeError(
            f"Asked for renderer '{matched_name}' but the viewport is still on "
            f"'{active}' (was '{before}'). Tried: {'; '.join(attempts)}. "
            f"Available renderers: {available}"
        )

    return {
        "success": True,
        "verified": True,
        "renderer": active,
        "previous_renderer": before,
        "available_renderers": available,
        "pane_name": scene_viewer.name(),
        "viewport_name": viewport.name(),
    }


###### viewport.frame_selection


def frame_selection(pane_name: str = None) -> dict:
    """Frame the current selection in the viewport.

    Args:
        pane_name: Optional pane tab name.
    """
    scene_viewer = _find_scene_viewer(pane_name)
    viewport = scene_viewer.curViewport()

    viewport.frameSelected()

    return {
        "success": True,
        "pane_name": scene_viewer.name(),
        "viewport_name": viewport.name(),
    }


###### viewport.frame_all


def _world_bounds(node: hou.Node) -> list | None:
    """[xmin, ymin, zmin, xmax, ymax, zmax] of what *node* shows, in world space.

    An object is its display SOP; a SOP's geometry is in its object's space,
    so it goes through that object's world transform. None without geometry.
    """
    owner, target = node, node
    if isinstance(node, hou.ObjNode):
        target = node.displayNode()
    else:
        while owner is not None and not isinstance(owner, hou.ObjNode):
            owner = owner.parent()
    try:
        box = target.geometry().boundingBox()
    except Exception:
        return None
    if not box.isValid():
        return None
    low, high = box.minvec(), box.maxvec()
    corners = [
        (x, y, z) for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])
    ]
    if owner is not None:
        transform = owner.worldTransform()
        corners = [tuple(hou.Vector3(*corner) * transform) for corner in corners]
    return [
        *(min(corner[i] for corner in corners) for i in range(3)),
        *(max(corner[i] for corner in corners) for i in range(3)),
    ]


def frame_all(
    pane_name: str = None,
    node_paths: list = None,
    bounds: list = None,
    direction=None,
    clearance: float = None,
) -> dict:
    """Frame all geometry in the viewport (home all), or only some of it.

    Args:
        pane_name: Optional pane tab name.
        node_paths: Frame the world bounds of these objects or SOPs only.
        bounds: Frame [xmin, ymin, zmin, xmax, ymax, zmax], in world space.
        direction: [rx, ry] degrees (or a direction name: 'top', 'persp'...):
            frame in perspective from that angle, the distance worked out
            from the bounds and the field of view, so the eye is always
            outside them. Without it the view keeps its angle.
        clearance: With *direction*: keep the eye this far above the top of
            the bounds (terrain); ``framing.clearance_adjusted`` says how.

    With *node_paths* or *bounds* (both: the box around all of them), the
    reply carries ``framed_bounds``, and ``no_geometry`` names any node that
    had nothing to frame. An object frames what it draws: a null its marker,
    a camera or a light its icon. A box with no size (a point) is framed as a
    1-unit cube around it, and ``padded`` says so.
    """
    turn = _angles(direction, "direction") if direction is not None else None
    lift = _number(clearance, "clearance") if clearance is not None else None
    if lift is not None and turn is None:
        raise ValueError("clearance applies to a perspective framing: pass direction=[rx, ry] too.")
    box = None
    empty: list = []
    if bounds is not None:
        try:
            box = [float(v) for v in bounds]
        except (TypeError, ValueError):
            box = []
        if len(box) != 6 or any(box[i] > box[i + 3] for i in range(3)):
            raise ValueError(f"bounds must be [xmin, ymin, zmin, xmax, ymax, zmax], got {bounds!r}")
    if node_paths is not None:
        if isinstance(node_paths, str):
            node_paths = [node_paths]
        missing = [path for path in node_paths if hou.node(str(path)) is None]
        if missing:
            raise ValueError(f"Node(s) not found: {missing}")
        for path in node_paths:
            found = _world_bounds(hou.node(str(path)))
            if found is None:
                empty.append(path)
            elif box is None:
                box = found
            else:
                box = [min(box[i], found[i]) for i in range(3)] + [
                    max(box[i], found[i]) for i in range(3, 6)
                ]
        if box is None:
            raise ValueError(f"Nothing to frame: no geometry on {empty}")

    scene_viewer = _find_scene_viewer(pane_name)
    viewport = scene_viewer.curViewport()
    result = {
        "success": True,
        "pane_name": scene_viewer.name(),
        "viewport_name": viewport.name(),
    }
    # Framing moves the viewport's own view, so a camera it looked through is
    # dropped one UI tick later: both homeAll() and frameBoundingBox() did
    # that to /obj/refcam on 22.0.368, and the reply said nothing. The camera
    # itself does not move. Say so rather than refuse: no tool unbinds a
    # camera, so a refusal would leave framing impossible.
    looks_through, _ = _looked_through(viewport)
    if looks_through:
        result["camera_released"] = looks_through
        result["note"] = (
            f"The viewport stopped looking through {looks_through} to frame; the camera "
            f"did not move. set_viewport_camera looks through it again."
        )
    if turn is not None:
        if box is None:
            box = _scene_bounds(scene_viewer)
        if box is None:
            raise ValueError("Nothing to frame: no displayed geometry. Pass node_paths or bounds.")
        framing = _apply_perspective(viewport, box, turn, clearance=lift)
        result["framed_bounds"] = framing.pop("framed_bounds")
        result["framing"] = framing
        result["view"] = _read_view(viewport)
        if empty:
            result["no_geometry"] = empty
        return result
    if box is None:
        viewport.homeAll()
        return result

    if all(box[i + 3] - box[i] < 1e-6 for i in range(3)):
        # A point: frameBoundingBox() put the eye 0.019 from it (22.0.368).
        box = [box[i] - 0.5 for i in range(3)] + [box[i + 3] + 0.5 for i in range(3)]
        result["padded"] = True
    viewport.frameBoundingBox(hou.BoundingBox(*box))
    result["framed_bounds"] = [round(v, 4) for v in box]
    if empty:
        result["no_geometry"] = empty
    return result


###### Capture helpers


def _fit_size(width: int, height: int, max_size: int | None) -> tuple:
    """(width, height) scaled down to fit *max_size* on its longer side; as is when it fits."""
    width, height = int(width), int(height)
    if not max_size or max(width, height) <= max_size:
        return (width, height)
    scale = max_size / float(max(width, height))
    return (max(1, round(width * scale)), max(1, round(height * scale)))


def _resolution_arg(resolution) -> list | None:
    """[width, height] of positive ints, or a ValueError."""
    if resolution is None:
        return None
    try:
        size = [int(v) for v in resolution]
    except (TypeError, ValueError):
        size = []
    if len(size) != 2 or min(size) < 1 or any(isinstance(v, bool) for v in resolution):
        raise ValueError(f"resolution must be [width, height] in pixels, got {resolution!r}")
    return size


def _max_size_arg(max_size) -> int | None:
    if max_size is None:
        return None
    if isinstance(max_size, bool) or not isinstance(max_size, (int, float)) or max_size < 16:
        raise ValueError(f"max_size must be a number of pixels >= 16, got {max_size!r}")
    return int(max_size)


def _max_size_kw(max_size) -> dict:
    """Keyword for _capture_pane_tab_qt, only when a cap was asked for."""
    return {"max_size": max_size} if max_size else {}


def _flipbook_resolution(viewport, resolution, max_size) -> list | None:
    """The flipbook's [width, height]: *resolution*, capped by *max_size*; None = the viewport's own.

    max_size alone scales the viewport's own size down, keeping its shape.
    """
    if resolution is not None:
        return list(_fit_size(resolution[0], resolution[1], max_size))
    if max_size:
        pixels = _viewport_pixels(viewport)
        if pixels is not None:
            fitted = _fit_size(pixels[0], pixels[1], max_size)
            if fitted != tuple(pixels):
                return list(fitted)
    return None


def _flipbook_still(scene_viewer, viewport, output_path: str, frame: float, resolution) -> str:
    """One flipbook frame of *viewport* to *output_path*; the path actually written."""
    from fxhoudinimcp_server.handlers.rendering_handlers import (
        _find_flipbook_output,
        _size_flipbook,
    )

    settings = scene_viewer.flipbookSettings().stash()
    settings.frameRange((frame, frame))
    settings.output(output_path)
    _no_mplay(settings)
    # The viewport as it is, not the stash's 1280x720, unless a size is asked.
    _size_flipbook(settings, resolution)
    # A selected object would be drawn with its selection outline.
    with selection_hidden():
        scene_viewer.flipbook(viewport, settings)
    # Handle frame number that flipbook may insert
    return _find_flipbook_output(output_path, frame)


_CAMERA_KEYS = ("direction", "rotation", "pivot", "distance", "look_at", "clearance")


def _camera_spec(camera):
    """A capture's *camera*, checked: a camera path (str), a view dict, or None."""
    if camera is None or isinstance(camera, str):
        if isinstance(camera, str) and not camera.strip():
            raise ValueError("camera must be a camera path or a view dict, got ''")
        return camera
    if not isinstance(camera, dict):
        raise ValueError(
            f"camera must be a camera path or a dict of {list(_CAMERA_KEYS)}, got {camera!r}"
        )
    unknown = sorted(set(camera) - set(_CAMERA_KEYS))
    if unknown:
        raise ValueError(f"Unknown camera key(s) {unknown}. Known: {list(_CAMERA_KEYS)}")
    spec: dict = {}
    direction = camera.get("direction")
    if isinstance(direction, str):
        canonical = _direction_name(direction)
        if canonical is None:
            raise _unknown_direction(direction)
        spec["direction"] = canonical
    elif direction is not None:
        spec["direction"] = _angles(direction, "camera.direction")
    if camera.get("rotation") is not None:
        spec["rotation"] = _vector3(camera["rotation"], "camera.rotation")
    if camera.get("pivot") is not None:
        spec["pivot"] = _vector3(camera["pivot"], "camera.pivot")
    if camera.get("look_at") is not None:
        spec["look_at"] = _vector3(camera["look_at"], "camera.look_at")
    if camera.get("distance") is not None:
        spec["distance"] = _positive(camera["distance"], "camera.distance")
    if camera.get("clearance") is not None:
        spec["clearance"] = _number(camera["clearance"], "camera.clearance")
    if not spec:
        raise ValueError(f"camera names nothing to set; pass any of {list(_CAMERA_KEYS)}")
    if isinstance(spec.get("direction"), tuple) and "rotation" in spec:
        raise ValueError("camera: pass direction=[rx, ry] or rotation, not both.")
    if "pivot" in spec and ("look_at" in spec or isinstance(spec.get("direction"), tuple)):
        raise ValueError(
            "camera: pivot places the free view as it is; with look_at or "
            "direction=[rx, ry] pass look_at instead."
        )
    return spec


def _apply_camera(scene_viewer, viewport, spec, box, state: dict) -> dict:
    """Put the view where *spec* says, for one capture; *state* restores it afterwards."""
    state["view_changed"] = True
    if isinstance(spec, str):
        node = hou.node(spec)
        viewport.setCamera(node if node is not None else spec)
        return {"camera": spec}
    applied: dict = {}
    direction = spec.get("direction")
    if isinstance(direction, str):
        _change_type(
            viewport, getattr(hou.geometryViewportType, _DIRECTION_TYPES[direction]), state
        )
        if box is not None:
            viewport.frameBoundingBox(hou.BoundingBox(*box))
        else:
            viewport.frameAll()
        applied["direction"] = direction
    # A rotation alone kept the old pivot and distance, so a node= capture
    # looked at wherever the view happened to be (live check): with neither
    # pivot nor distance, the rotation is the angle to frame the target from.
    rotation_only = "rotation" in spec and "pivot" not in spec and "distance" not in spec
    framed = (
        isinstance(direction, tuple) or "look_at" in spec or "clearance" in spec or rotation_only
    )
    if framed:
        turn = direction if isinstance(direction, tuple) else spec.get("rotation")
        target = box if box is not None else _scene_bounds(scene_viewer)
        applied["framing"] = _apply_perspective(
            viewport,
            target,
            turn or _current_angles(viewport),
            look_at=spec.get("look_at"),
            clearance=spec.get("clearance"),
            distance=spec.get("distance"),
            state=state,
        )
    elif any(key in spec for key in ("rotation", "pivot", "distance")):
        _place_free_view(viewport, spec.get("rotation"), spec.get("pivot"), spec.get("distance"))
    with contextlib.suppress(Exception):
        applied["view"] = _read_view(viewport)
    return applied


_MISSING = object()


def _save_view(scene_viewer) -> dict:
    """What a capture may change on *scene_viewer*, for _restore_view."""
    viewport = scene_viewer.curViewport()
    state: dict = {"viewport": viewport, "views": [], "view_changed": False}
    readers = (
        ("pwd", lambda: scene_viewer.pwd().path()),
        ("type", viewport.type),
        ("view", lambda: viewport.defaultCamera().stash()),
        ("camera", lambda: _looked_through(viewport)[0]),
        ("visible_objects", lambda: viewport.settings().visibleObjects()),
        (
            "editors",
            lambda: [
                (tab, tab.pwd().path())
                for tab in hou.ui.paneTabs()
                if tab.type() == hou.paneTabType.NetworkEditor
            ],
        ),
    )
    for key, read in readers:
        try:
            state[key] = read()
        except Exception:
            state[key] = _MISSING
    return state


def _restore_view(scene_viewer, state: dict) -> list:
    """Put back what _save_view recorded; the names of what could not be put back."""
    failed: list = []
    viewport = state["viewport"]

    def attempt(name, action):
        try:
            action()
        except Exception:
            failed.append(name)

    pwd = state.get("pwd", _MISSING)
    if pwd is not _MISSING:

        def _pwd():
            if scene_viewer.pwd().path() != pwd:
                scene_viewer.setPwd(hou.node(pwd))

        attempt("network", _pwd)
    editors = state.get("editors", _MISSING)
    if editors is not _MISSING:
        # The Network Editor follows the viewer's network when the panes are linked.
        with keep_viewer_state():
            for tab, path in editors:
                try:
                    if tab.pwd().path() != path:
                        tab.cd(path)
                except Exception:
                    failed.append(f"network_editor:{path}")
    if state.get("view_changed"):
        for view_type, view in reversed(state["views"]):
            attempt("view", lambda t=view_type, v=view: _put_view(viewport, t, v))
        if state.get("type", _MISSING) is not _MISSING:
            attempt("view_type", lambda: _change_type(viewport, state["type"]))
        if state.get("view", _MISSING) is not _MISSING:
            attempt("view", lambda: viewport.setDefaultCamera(state["view"]))
    camera = state.get("camera", _MISSING)
    if camera is not _MISSING:

        def _camera():
            now = _looked_through(viewport)[0]
            if camera and now != camera:
                viewport.setCamera(hou.node(camera) or camera)
            elif not camera and now:
                viewport.useDefaultCamera()

        attempt("camera", _camera)
    pattern = state.get("visible_objects", _MISSING)
    if pattern is not _MISSING:

        def _pattern():
            settings = viewport.settings()
            if settings.visibleObjects() != pattern:
                settings.setVisibleObjects(pattern)

        attempt("visible_objects", _pattern)
    return sorted(set(failed))


def _put_view(viewport, view_type, view) -> None:
    _change_type(viewport, view_type)
    viewport.setDefaultCamera(view)


@contextlib.contextmanager
def preserved_view(scene_viewer):
    """The viewer's network, view, camera and Visible Objects, put back after the block.

    Yields the saved state; after the block its ``not_restored`` lists what
    could not be put back (empty when everything was).
    """
    state = _save_view(scene_viewer)
    try:
        yield state
    finally:
        state["not_restored"] = _restore_view(scene_viewer, state)


@contextlib.contextmanager
def _undo_off():
    """No undo entries for the scratch nodes a capture makes and removes."""
    disabler = None
    with contextlib.suppress(Exception):
        disabler = hou.undos.disabler()
    if disabler is None:
        yield
        return
    with disabler:
        yield


def scratch_child(parent, type_name: str, name: str | None = None):
    """A node a capture makes and destroys in the same call, so it is never placed."""
    with _undo_off():
        return parent.createNode(type_name, name)


#: Name of the temporary object a capture draws one node through.
SCRATCH_NAME = "mcp_capture_tmp"


@contextlib.contextmanager
def scratch_object(sources: list, name: str = SCRATCH_NAME):
    """A temporary /obj geo that object-merges *sources* in world space; destroyed after.

    Yields (object, object_merge SOP). Nothing else in the scene changes: no
    display flag moves and the source nodes are only read.
    """
    obj_net = hou.node("/obj")
    if obj_net is None:
        raise RuntimeError("/obj not found: cannot make the temporary capture object.")
    obj = scratch_child(obj_net, "geo", name)
    try:
        with _undo_off():
            for child in list(obj.children()):
                child.destroy()
            merge = scratch_child(obj, "object_merge", "sources")
            merge.parm("numobj").set(len(sources))
            for index, path in enumerate(sources, 1):
                merge.parm(f"objpath{index}").set(path)
            # "Into This Object": the object is at the origin, so this is world space.
            merge.parm("xformtype").set(1)
            merge.setDisplayFlag(True)
            merge.setRenderFlag(True)
        yield obj, merge
    finally:
        with _undo_off(), contextlib.suppress(Exception):
            obj.destroy()


def isolate(scene_viewer, viewport, obj) -> None:
    """Draw only *obj*: the viewer at its network, Visible Objects set to it."""
    scene_viewer.setPwd(obj.parent())
    viewport.settings().setVisibleObjects(obj.name())


def _capturable_node(path: str):
    """The SOP or object *path* names, or a ValueError saying what node= takes."""
    node = hou.node(path)
    if node is None:
        raise ValueError(f"Node not found: {path}")
    is_obj = isinstance(node, hou.ObjNode)
    is_sop = False
    with contextlib.suppress(Exception):
        is_sop = node.type().category() == hou.sopNodeTypeCategory()
    if not (is_obj or is_sop):
        kind = "unknown"
        with contextlib.suppress(Exception):
            kind = node.type().category().name()
        raise ValueError(
            f"node= draws SOP or object geometry; {path} is a {kind} node. For a LOP "
            "stage use set_viewer_context, for a DOP network network=."
        )
    return node


def _network_arg(path: str):
    """The network *path* names; a node that is not one stands for its parent."""
    node = hou.node(path)
    if node is None:
        raise ValueError(f"Network not found: {path}")
    with contextlib.suppress(Exception):
        if not node.isNetwork() and node.parent() is not None:
            return node.parent()
    return node


def _plain(value):
    """*value* if it is a JSON-plain str/int/float/bool, else None."""
    return value if isinstance(value, (str, int, float, bool)) else None


def _count(geo, intrinsic: str, method: str) -> int | None:
    for read in (lambda: geo.intrinsicValue(intrinsic), lambda: getattr(geo, method)()):
        try:
            value = read()
        except Exception:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return None


#: Primitive kinds worth naming in a capture reply, by hou.primType attribute.
_NOTED_PRIM_TYPES = (
    ("volumes", "Volume"),
    ("vdbs", "VDB"),
    ("packed", "PackedPrim"),
    ("agents", "Agent"),
)


def _geometry_summary(sop) -> dict | None:
    """{prims, points, prim_types} of what *sop* cooks to, or None."""
    try:
        geo = sop.geometry()
    except Exception:
        return None
    if geo is None:
        return None
    summary = {
        "prims": _count(geo, "primitivecount", "primCount"),
        "points": _count(geo, "pointcount", "pointCount"),
    }
    types: dict = {}
    for label, attr in _NOTED_PRIM_TYPES:
        with contextlib.suppress(Exception):
            number = geo.countPrimType(getattr(hou.primType, attr))
            if isinstance(number, int) and not isinstance(number, bool) and number:
                types[label] = number
    if types:
        summary["prim_types"] = types
    return summary


#: Guides whose being off hides geometry from a capture, by hou.viewportGuide name.
_HIDING_GUIDES = (
    ("DisplayNodes", "display_nodes"),
    ("TemplateGeometry", "template_geometry"),
    ("CurrentGeometry", "current_geometry"),
    ("NodeGuides", "node_guides"),
)
_MAX_DRAWN_OBJECTS = 50


def _drawn_report(scene_viewer, viewport, source: str | None = None, sop=None) -> dict:
    """What a capture draws: which node(s), how many prims/points, what is switched off.

    Best effort: a value the viewer cannot give is left out. *sop* is the
    node actually drawn when a capture isolated one (*source* is what it shows).
    """
    drawn: dict = {}
    sources: list = []
    if sop is not None:
        drawn["node"] = source
        drawn["via"] = "a temporary object merge; no display flag was moved"
        sources = [sop]
    else:
        with contextlib.suppress(Exception):
            network = scene_viewer.pwd()
            category = network.childTypeCategory()
            if category == hou.sopNodeTypeCategory():
                display = network.displayNode()
                drawn["display_node"] = _plain(display.path()) if display is not None else None
                sources = [display] if display is not None else []
            elif category == hou.objNodeTypeCategory():
                from fxhoudinimcp_server.ui import pattern_hides

                pattern = ""
                with contextlib.suppress(Exception):
                    pattern = viewport.settings().visibleObjects()
                shown = [
                    child
                    for child in network.children()
                    if isinstance(child, hou.ObjNode)
                    and child.isObjectDisplayed()
                    and not (isinstance(pattern, str) and pattern_hides(pattern, child.path()))
                ]
                drawn["objects"] = [_plain(child.path()) for child in shown[:12]]
                drawn["object_count"] = len(shown)
                for child in shown[:_MAX_DRAWN_OBJECTS]:
                    with contextlib.suppress(Exception):
                        display = child.displayNode()
                        if display is not None:
                            sources.append(display)
    prims = points = 0
    counted = False
    kinds: dict = {}
    for node in sources:
        summary = _geometry_summary(node)
        if summary is None:
            continue
        if summary["prims"] is not None:
            prims += summary["prims"]
            counted = True
        if summary["points"] is not None:
            points += summary["points"]
            counted = True
        for label, number in summary.get("prim_types", {}).items():
            kinds[label] = kinds.get(label, 0) + number
    if counted:
        drawn["prims"], drawn["points"] = prims, points
    if kinds:
        drawn["prim_types"] = kinds
    settings = None
    with contextlib.suppress(Exception):
        settings = viewport.settings()
    off = []
    for attr, label in _HIDING_GUIDES:
        with contextlib.suppress(Exception):
            if settings.guideEnabled(getattr(hou.viewportGuide, attr)) is False:
                off.append(label)
    if off:
        drawn["guides_off"] = off
    with contextlib.suppress(Exception):
        pattern = settings.visibleObjects()
        if isinstance(pattern, str) and pattern.strip() not in ("", "*"):
            drawn["visible_objects"] = pattern
    with contextlib.suppress(Exception):
        looked, _ = _looked_through(viewport)
        if isinstance(looked, str) and looked:
            drawn["camera"] = looked
    notes = []
    if counted and prims == 0 and points == 0:
        notes.append("The viewer draws no geometry here: the frame shows only the grid.")
    if "display_nodes" in off:
        notes.append("The Display Nodes guide is off, so no SOP display geometry is drawn.")
    if notes:
        drawn["note"] = " ".join(notes)
    return drawn


###### viewport.capture_screenshot


def capture_screenshot(
    output_path: str,
    pane_name: str = None,
    node: str = None,
    network: str = None,
    camera=None,
    resolution: list = None,
    max_size: int = None,
    frame: bool = None,
) -> dict:
    """Capture a screenshot of a specific pane tab, or the active viewport.

    Args:
        output_path: Destination image path.
        pane_name: Name of the pane tab to capture. If not provided,
            captures the first Scene Viewer found.
        node: Draw only this SOP's (or object's) geometry, through a
            temporary object merge: no display flag moves. Framed on it
            unless *camera* places the view or *frame* is False.
        network: Show the viewer at this network for the capture only.
        camera: A camera path to look through, or a view dict with any of
            direction (a name or [rx, ry]), rotation, pivot, distance,
            look_at, clearance: set and captured in one call.
        resolution: [width, height] of the image.
        max_size: Cap on the longer side, in pixels (scales down only).
        frame: Frame *node* (default True when *node* is given).

    Everything moved for the capture (network, view, camera, Visible
    Objects) is put back afterwards; ``restored`` says so, ``not_restored``
    names anything that could not be. ``drawn`` reports what the image
    holds: display node or objects, prim and point counts, guides switched
    off, the Visible Objects pattern, the camera looked through.
    """
    # Houdini variables ($JOB, $HIP) are expanded, as load_scene does: a
    # "$JOB/..." path reached os.makedirs or os.path as written and failed.
    output_path = hou.text.expandString(output_path)
    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    # Checked before anything moves, so a bad value never half-applies.
    spec = _camera_spec(camera)
    size = _resolution_arg(resolution)
    cap = _max_size_arg(max_size)
    if node is not None and network is not None:
        raise ValueError("Pass node or network, not both: node= draws one node on its own.")
    source = _capturable_node(node) if node is not None else None
    pinned = _network_arg(network) if network is not None else None

    # Named pane if asked for, otherwise the first Scene Viewer.
    pane_tab = _find_pane_by_name(pane_name) if pane_name is not None else _find_scene_viewer()

    cur_frame = hou.frame()

    if pane_tab.type() != hou.paneTabType.SceneViewer:
        if source is not None or pinned is not None or spec is not None or size is not None:
            raise ValueError(
                f"node, network, camera and resolution need a Scene Viewer; pane "
                f"'{pane_tab.name()}' is not one. max_size works on any pane."
            )
        # For other pane types, use Qt widget grab
        _capture_pane_tab_qt(pane_tab, output_path, **_max_size_kw(cap))
        return {
            "success": True,
            "pane_name": pane_tab.name(),
            "output_path": output_path,
            "file_exists": os.path.isfile(output_path),
        }

    moves = source is not None or pinned is not None or spec is not None
    reply: dict = {}
    with contextlib.ExitStack() as stack:
        scratch = None
        state = None
        if source is not None:
            scratch = stack.enter_context(scratch_object([source.path()]))
        if moves:
            stack.enter_context(keep_viewer_state())
            state = stack.enter_context(preserved_view(pane_tab))
            viewport = pane_tab.curViewport()
            box = None
            if scratch is not None:
                isolate(pane_tab, viewport, scratch[0])
                box = _world_bounds(source)
            elif pinned is not None:
                pane_tab.setPwd(pinned)
            if spec is not None:
                reply["view_applied"] = _apply_camera(pane_tab, viewport, spec, box, state)
            elif box is not None and frame is not False:
                state["view_changed"] = True
                viewport.frameBoundingBox(hou.BoundingBox(*box))
                reply["framed_bounds"] = [round(v, 4) for v in box]
        viewport = pane_tab.curViewport()
        # Which network the image shows, and a note when it is a DOP view.
        viewer = viewer_context(pane_tab)
        reply["drawn"] = _drawn_report(
            pane_tab,
            viewport,
            source=source.path() if source is not None else None,
            sop=scratch[1] if scratch is not None else None,
        )
        flip_size = _flipbook_resolution(viewport, size, cap)
        actual_path = _flipbook_still(pane_tab, viewport, output_path, cur_frame, flip_size)
        if flip_size is not None:
            reply["resolution"] = flip_size
    if state is not None:
        reply["restored"] = not state.get("not_restored")
        if state.get("not_restored"):
            reply["not_restored"] = state["not_restored"]

    return {
        "success": True,
        "pane_name": pane_tab.name(),
        "output_path": actual_path,
        "file_exists": os.path.isfile(actual_path),
        **viewer,
        **reply,
    }


###### viewport.capture_network_editor

# Network units of context around the framed node, each side: enough for its
# immediate neighbours to show, few enough that the node is still readable.
_FRAME_PAD_X = 5.0
_FRAME_PAD_Y = 3.0
# Below this many pixels per network unit (about one node width) node names
# cannot be read in the image.
_READABLE_PX_PER_UNIT = 30.0


def _bounds_list(bounds) -> list | None:
    """``hou.BoundingRect`` -> ``[xmin, ymin, xmax, ymax]``, rounded."""
    try:
        return [round(v, 3) for v in (*bounds.min(), *bounds.max())]
    except Exception:
        return None


def _frame_node(network_editor, node) -> list:
    """Put *node* in the middle of the editor at once, not after a flight.

    ``homeToSelection()`` animates the view over the next event-loop ticks,
    while the capture runs in this same tick, so the image came out mid-flight:
    the whole network, or an empty stretch between the previous node and this
    one. ``setVisibleBounds(transition_time=0)`` moves the view before the grab.

    ``set_center_when_scale_rejected``: a rect at the zoom the editor already
    has counts as a rejected scale change, and without the flag the call then
    changes nothing at all, so every capture after the first stayed on the
    first node (measured on 22.0.429).

    Returns the node's own rect in network space.
    """
    pos = node.position()
    size = node.size()
    xmin, ymin = pos[0], pos[1]
    xmax, ymax = xmin + size[0], ymin + size[1]
    network_editor.setVisibleBounds(
        hou.BoundingRect(
            xmin - _FRAME_PAD_X,
            ymin - _FRAME_PAD_Y,
            xmax + _FRAME_PAD_X,
            ymax + _FRAME_PAD_Y,
        ),
        transition_time=0.0,
        set_center_when_scale_rejected=True,
    )
    return [round(v, 3) for v in (xmin, ymin, xmax, ymax)]


def _item_rects(network) -> list:
    """[xmin, ymin, xmax, ymax] of every node, network box and sticky note in *network*."""
    items: list = []
    for reader in ("children", "networkBoxes", "stickyNotes"):
        with contextlib.suppress(Exception):
            items.extend(getattr(network, reader)())
    rects = []
    for item in items:
        with contextlib.suppress(Exception):
            pos, size = item.position(), item.size()
            rects.append([pos[0], pos[1], pos[0] + size[0], pos[1] + size[1]])
    return rects


def _items_frame(rects: list, margin: float = 0.05) -> list | None:
    """The union of *rects* grown by *margin* of its extent (at least one unit) each side."""
    if not rects:
        return None
    box = [
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[2] for r in rects),
        max(r[3] for r in rects),
    ]
    pad = max(1.0, margin * max(box[2] - box[0], box[3] - box[1]))
    return [box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad]


def _readability(network_editor, visible: list | None) -> dict:
    """Pixels per network unit in the image, and a note when names will not be readable."""
    if not visible:
        return {}
    width_px = None
    with contextlib.suppress(Exception):
        screen = network_editor.screenBounds()
        width_px = float(screen.max()[0] - screen.min()[0])
    units = visible[2] - visible[0]
    if not width_px or units <= 0:
        return {}
    density = round(width_px / units, 1)
    found: dict = {"pixels_per_unit": density}
    if density < _READABLE_PX_PER_UNIT:
        found["readability_note"] = (
            f"{density} pixels per node width: node names will not be readable. Capture part "
            "of the network with node_path, or widen the Network Editor pane."
        )
    return found


#: The editor's place before a capture that moved it, put back by the follow-up
#: after its repaint grab (restoring in the handler would be grabbed instead).
_EDITOR_RESTORE: dict = {}


def _editor_snapshot(network_editor) -> dict:
    snap: dict = {"editor": network_editor}
    with contextlib.suppress(Exception):
        snap["pwd"] = network_editor.pwd().path()
    with contextlib.suppress(Exception):
        snap["bounds"] = network_editor.visibleBounds()
    with contextlib.suppress(Exception):
        snap["current"] = network_editor.currentNode()
    return snap


def _restore_editor() -> list | None:
    """Put the editor back where the capture found it; None when nothing was pending.

    Returns what could not be put back (empty when everything was).
    """
    snap = _EDITOR_RESTORE.pop("pending", None)
    if snap is None:
        return None
    editor = snap["editor"]
    failed: list = []
    if "pwd" in snap:
        with keep_viewer_state():
            try:
                if editor.pwd().path() != snap["pwd"]:
                    editor.cd(snap["pwd"])
            except Exception:
                failed.append("network")
    current = snap.get("current")
    if current is not None:
        with contextlib.suppress(Exception):
            if current.parent().path() == snap.get("pwd"):
                editor.setCurrentNode(current, False)
    if "bounds" in snap:
        try:
            editor.setVisibleBounds(
                snap["bounds"], transition_time=0.0, set_center_when_scale_rejected=True
            )
        except Exception:
            failed.append("view")
    return failed


def capture_network_editor(
    output_path: str,
    node_path: str = None,
    network_path: str = None,
    frame: str = None,
    max_size: int = None,
) -> dict:
    """Capture a screenshot of the network editor.

    Args:
        output_path: Destination image path.
        node_path: Optional node path to frame before capture.
        network_path: Show this network for the capture (default: the
            node's network, else where the editor is).
        frame: "all" frames every node, network box and sticky note of the
            network; "node" (the default with *node_path*) frames the node.
        max_size: Cap on the image's longer side, in pixels (scales down).

    The reply carries ``visible_bounds`` (the network space the image shows)
    and, with *node_path*, ``node_bounds`` and ``node_in_view``, so what the
    image holds can be checked without opening it. A capture that moved the
    editor puts it back (network, view, current node) once the image is
    taken.
    """
    # Houdini variables ($JOB, $HIP) are expanded, as load_scene does: a
    # "$JOB/..." path reached os.makedirs or os.path as written and failed.
    output_path = hou.text.expandString(output_path)
    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    if frame not in (None, "all", "node"):
        raise ValueError(f"frame must be 'all' or 'node', got {frame!r}")
    cap = _max_size_arg(max_size)

    network_editor = None
    for pane_tab in hou.ui.paneTabs():
        if pane_tab.type() == hou.paneTabType.NetworkEditor:
            network_editor = pane_tab
            break

    if network_editor is None:
        raise RuntimeError("No Network Editor pane found.")

    # Navigate to the specified node if provided
    node_bounds = None
    node = None
    if node_path is not None:
        node = hou.node(node_path)
        if node is None:
            raise ValueError(f"Node not found: {node_path}")
    if frame == "node" and node is None:
        raise ValueError("frame='node' needs node_path.")
    network = None
    if network_path is not None:
        network = hou.node(network_path)
        if network is None:
            raise ValueError(f"Network not found: {network_path}")
        if node is not None and node.parent() is not None and node.parent() != network:
            raise ValueError(f"{node_path} is not in {network_path}.")
    elif node is not None:
        network = node.parent()

    moves = node is not None or network is not None or frame is not None
    if moves and "pending" not in _EDITOR_RESTORE:
        _EDITOR_RESTORE["pending"] = _editor_snapshot(network_editor)
    framed_all = None
    try:
        # Showing a node in a DOP network takes the Scene Viewer there too;
        # the editor goes back after the shot.
        with viewer_stays_out_of_dops():
            with keep_viewer_state():
                if network is not None:
                    network_editor.cd(network.path())
                if node is not None:
                    network_editor.setCurrentNode(node)
            if frame == "all":
                shown = network if network is not None else network_editor.pwd()
                framed_all = _items_frame(_item_rects(shown))
                if framed_all is not None:
                    network_editor.setVisibleBounds(
                        hou.BoundingRect(*framed_all),
                        transition_time=0.0,
                        set_center_when_scale_rejected=True,
                    )
                if node is not None:
                    pos, size = node.position(), node.size()
                    node_bounds = [
                        round(v, 3) for v in (pos[0], pos[1], pos[0] + size[0], pos[1] + size[1])
                    ]
            elif node is not None:
                node_bounds = _frame_node(network_editor, node)

            # Capture the network editor via Qt widget grab
            _capture_pane_tab_qt(network_editor, output_path, **_max_size_kw(cap))
            # What the image shows, read before the editor can go back.
            visible_bounds = None
            with contextlib.suppress(Exception):
                visible_bounds = _bounds_list(network_editor.visibleBounds())
    except Exception:
        _restore_editor()
        raise

    result = {
        "success": True,
        "output_path": output_path,
        "node_path": node_path,
        "file_exists": os.path.isfile(output_path),
        "visible_bounds": visible_bounds,
    }
    if network is not None:
        result["network_path"] = network.path()
    if frame == "all":
        result["framed"] = "all"
        result["items_bounds"] = (
            [round(v, 3) for v in framed_all] if framed_all is not None else None
        )
    if node_bounds is not None:
        result["node_bounds"] = node_bounds
    _set_node_in_view(result)
    result.update(_readability(network_editor, visible_bounds))
    return result


def _set_node_in_view(result: dict) -> None:
    """Whether the framed node lies inside what the image shows."""
    node_bounds = result.get("node_bounds")
    if node_bounds is None:
        return
    view = result.get("visible_bounds")
    result["node_in_view"] = (
        None
        if view is None
        else (
            view[0] <= node_bounds[0]
            and view[1] <= node_bounds[1]
            and node_bounds[2] <= view[2]
            and node_bounds[3] <= view[3]
        )
    )


def _recapture_network_editor(data: dict, params: dict) -> dict:
    """Capture the editor again once Houdini has repainted it, then put it back.

    The handler's own grab ran in the same main-thread call that moved the
    editor, before Houdini could redraw it, and on Houdini 22 that image
    showed the editor's previous framing (measured on 22.0.508: framing OUT
    then CONTROLS gave CONTROLS then OUT). The dispatcher calls this after
    leaving the main thread idle, so this grab shows what was asked for. The
    editor goes back to where the capture found it only after this grab.
    """
    try:
        network_editor = next(
            (tab for tab in hou.ui.paneTabs() if tab.type() == hou.paneTabType.NetworkEditor),
            None,
        )
        if network_editor is None:
            return data
        _capture_pane_tab_qt(
            network_editor, data["output_path"], **_max_size_kw(params.get("max_size"))
        )
        with contextlib.suppress(Exception):
            data["visible_bounds"] = _bounds_list(network_editor.visibleBounds())
        data["file_exists"] = os.path.isfile(data["output_path"])
        _set_node_in_view(data)
        return data
    finally:
        failed = _restore_editor()
        if failed is not None:
            data["editor_restored"] = not failed
            if failed:
                data["editor_not_restored"] = failed


###### viewport.set_current_network


def set_current_network(network_path: str, other_objects: str | None = "hide") -> dict:
    """Navigate the network editor to a specific network path.

    The viewer follows the network editor into the object, and *other_objects*
    sets what it draws of every other object (the viewport's Y hotkey): "hide"
    by default, so work in one object is not drawn over, and slowed by, the
    rest of the scene. No display flag changes.

    A DOP network becomes the current one, where DOP shelf tools build, as
    Houdini does when the editor enters it (measured on 22.0.429; it stays
    current after the editor leaves). model_popnet does not make its new
    network current, and a session set it through execute_python, not
    knowing this did: it is set here explicitly and named in
    `current_dop_network`.

    The viewport's Visible Objects pattern is saved with the hip, and one
    left at another object drew this one not at all: a session's
    screenshots came out black. Unless *other_objects* is None, a
    pattern that leaves this object out is set back to "*" and named in
    `visible_objects_reset`; other objects stay hidden by *other_objects*.

    Args:
        network_path: Path to the network to navigate to (e.g. '/obj/geo1').
        other_objects: "hide", "ghost", "show", or None to leave the viewer as is.
    """
    node = hou.node(network_path)
    if node is None:
        raise ValueError(f"Network path not found: {network_path}")

    require_ui(
        "navigate the network editor",
        alternative="Without a UI the network can still be read with list_children.",
    )
    network_editor = None
    for pane_tab in hou.ui.paneTabs():
        if pane_tab.type() == hou.paneTabType.NetworkEditor:
            network_editor = pane_tab
            break

    if network_editor is None:
        raise RuntimeError("No Network Editor pane found.")

    # cd() to a node that is not a network (a SOP) left the editor where it
    # was and the reply said success: from /obj/fern/popnet to
    # /obj/geo1/box1 nothing moved (22.0.466). Such a node is made
    # current in its own network, as the editor does when it is clicked.
    network = node
    with contextlib.suppress(Exception):
        if not node.isNetwork() and node.parent() is not None:
            network = node.parent()
    # The viewer follows the editor; its cameras and the selection stay.
    with keep_viewer_state():
        network_editor.cd(network.path())
        if network is not node:
            with contextlib.suppress(Exception):
                network_editor.setCurrentNode(node)
    reached = network_editor.pwd()
    if reached is None or reached.path() != network.path():
        raise RuntimeError(
            f"The network editor did not go into {network.path()}; it is at "
            f"{reached.path() if reached is not None else 'no network'}."
        )

    reply = {
        "success": True,
        "network_path": network_path,
        "pane_name": network_editor.name(),
        "other_objects": set_other_objects(other_objects),
    }
    if network is not node:
        reply["entered"] = network.path()
        reply["current_node"] = node.path()
    if other_objects is not None:
        reset = show_viewed_object(network)
        if reset:
            reply["visible_objects_reset"] = reset
    is_dop = False
    with contextlib.suppress(Exception):
        is_dop = network.childTypeCategory() == hou.dopNodeTypeCategory()
    if is_dop:
        hou.setCurrentDopNet(network)
        with contextlib.suppress(Exception):
            reply["current_dop_network"] = hou.currentDopNet().path()
    return reply


###### viewport.find_error_nodes


_ERROR_NODE_CAP = 50


def find_error_nodes(root_path: str = "/") -> dict:
    """Find all nodes with errors or warnings, recursively from a root path.

    Args:
        root_path: Root node path to start searching from. Defaults to '/'.
    """
    root = hou.node(root_path)
    if root is None:
        raise ValueError(f"Root path not found: {root_path}")

    from fxhoudinimcp_server.handlers.graph_handlers import _condensed

    error_nodes = []
    warning_nodes = []
    counts = {"errors": 0, "warnings": 0}

    def _check_node(node):
        """Recursively check nodes for errors and warnings.

        Messages are condensed (one upstream error repeats on every node
        downstream of it) and each list holds the first 50 nodes; the counts
        stay complete. `name` is gone: it is the tail of `path`.
        """
        for kind, reader, bucket in (
            ("errors", node.errors, error_nodes),
            ("warnings", node.warnings, warning_nodes),
        ):
            try:
                messages = reader()
            except (hou.OperationFailed, hou.ObjectWasDeleted, AttributeError) as e:
                logger.debug("Could not read %s for node '%s': %s", kind, node.path(), e)
                continue
            if messages:
                counts[kind] += 1
                if len(bucket) < _ERROR_NODE_CAP:
                    bucket.append(
                        {
                            "path": node.path(),
                            "type": node.type().name(),
                            kind: [_condensed(m) for m in messages],
                        }
                    )

        # Recurse into children
        try:
            for child in node.children():
                _check_node(child)
        except (hou.OperationFailed, hou.ObjectWasDeleted) as e:
            logger.debug("Could not iterate children of node '%s': %s", node.path(), e)

    _check_node(root)

    return {
        "error_nodes": error_nodes,
        "warning_nodes": warning_nodes,
        "error_count": counts["errors"],
        "warning_count": counts["warnings"],
        "root_path": root_path,
    }


###### Helpers


def _find_scene_viewer(pane_name: str = None):
    """Find a Scene Viewer pane tab by name, or the first one available.

    Args:
        pane_name: Optional specific pane tab name.

    Returns:
        A hou.SceneViewer pane tab.

    Raises:
        RuntimeError: If no Scene Viewer is found.
        ValueError: If the named pane is not a Scene Viewer.
    """
    require_ui(
        "find a Scene Viewer",
        alternative="Geometry can still be inspected with get_geometry_info and sample_geometry.",
    )
    if pane_name is not None:
        pane_tab = _find_pane_by_name(pane_name)
        if pane_tab.type() != hou.paneTabType.SceneViewer:
            raise ValueError(
                f"Pane '{pane_name}' is a {pane_tab.type().name()}, not a Scene Viewer."
            )
        return pane_tab

    for pane_tab in hou.ui.paneTabs():
        if pane_tab.type() == hou.paneTabType.SceneViewer:
            return pane_tab

    raise RuntimeError("No Scene Viewer pane found.")


def _find_pane_by_name(pane_name: str):
    """Find a pane tab by its name.

    Args:
        pane_name: The pane tab name.

    Returns:
        The matching hou.PaneTab.

    Raises:
        ValueError: If no pane with the given name exists.
    """
    require_ui(f"find the pane {pane_name!r}")
    for pane_tab in hou.ui.paneTabs():
        if pane_tab.name() == pane_name:
            return pane_tab

    available = [pt.name() for pt in hou.ui.paneTabs()]
    raise ValueError(f"Pane tab not found: '{pane_name}'. Available panes: {available}")


###### viewport.log_status


def log_status(message: str, severity: str = "message") -> dict:
    """Display a status message in Houdini's status bar.

    Args:
        message: The status message to display.
        severity: Severity level — "message" (default), "important",
            "warning", or "error".
    """
    severity_map = {
        "message": hou.severityType.Message,
        "important": hou.severityType.ImportantMessage,
        "warning": hou.severityType.Warning,
        "error": hou.severityType.Error,
    }
    sev = severity_map.get(severity.lower(), hou.severityType.Message)
    if hou.isUIAvailable():
        hou.ui.setStatusMessage(message, severity=sev)
    else:
        # Headless sessions (hython/hbatch) have no status bar; stay a
        # harmless no-op so instruction-following clients never error.
        print(f"[status] {message}")
    return {"message": message, "severity": severity}


###### Registration

register_handler("viewport.list_panes", list_panes)
register_handler("viewport.get_viewport_info", get_viewport_info)
register_handler("viewport.set_viewport_camera", set_viewport_camera)
register_handler("viewport.set_viewport_display", set_viewport_display)
register_handler("viewport.set_viewport_direction", set_viewport_direction)
register_handler("viewport.set_viewport_renderer", set_viewport_renderer)
register_handler("viewport.frame_selection", frame_selection)
register_handler("viewport.frame_all", frame_all)
register_handler("viewport.capture_screenshot", capture_screenshot)
register_handler("viewport.capture_network_editor", capture_network_editor)
register_followup("viewport.capture_network_editor", _recapture_network_editor)
register_handler("viewport.set_current_network", set_current_network)
register_handler("viewport.find_error_nodes", find_error_nodes)
register_handler("viewport.log_status", log_status)


###### viewport.set_viewer_context


def set_viewer_context(
    network_path: str,
    current_node: str = None,
    pane_name: str = None,
) -> dict:
    """Point the Scene Viewer at a network, and optionally a node within it.

    set_current_network moves the network EDITOR. This moves the VIEWER, which is
    a different pane and the one that decides whether a scene graph view exists at
    all. Without it there was no way to enter Solaris, so no way to preview a USD
    stage or set a Hydra delegate: a recorded session burned several
    execute_python calls on exactly this, and even this project's own GUI checks
    had to reach for Python.

    Args:
        network_path: Network for the viewer to display, e.g. "/stage".
        current_node: Optional node inside it to make current, which is what
            selects the stage a Solaris viewport shows.
        pane_name: Optional pane tab name.
    """
    network = hou.node(network_path)
    if network is None:
        raise ValueError(f"Network not found: {network_path}")

    scene_viewer = _find_scene_viewer(pane_name)
    scene_viewer.setPwd(network)

    if current_node is not None:
        # "karma" means the node of that name inside the network; hou.node()
        # alone reads it as relative to /, and the documented usage failed.
        node = network.node(current_node) or hou.node(current_node)
        if node is None:
            raise ValueError(f"Node not found: {current_node}")
        scene_viewer.setCurrentNode(node)

    result = {
        "success": True,
        "network_path": scene_viewer.pwd().path(),
        "pane_name": scene_viewer.name(),
        # Whether Hydra delegates apply here, which is the question the caller is
        # usually really asking.
        "is_scene_graph_view": _is_scene_graph_view(scene_viewer),
    }
    with contextlib.suppress(Exception):
        current = scene_viewer.currentNode()
        result["current_node"] = current.path() if current else None
    return result


register_handler("viewport.set_viewer_context", set_viewer_context)
