"""Rendering handlers for FXHoudini-MCP.

Provides tools for viewport capture, render node management, and rendering
operations including Karma, OpenGL, and other Houdini renderers.
"""

from __future__ import annotations

import contextlib

# Built-in
import logging
import math
import os
import subprocess
import tempfile
import time
from typing import Any

# Third-party
import hou

# Internal
from fxhoudinimcp_server.callbacks import CallbackError, press
from fxhoudinimcp_server.config import place_new_node
from fxhoudinimcp_server.dispatcher import register_handler
from fxhoudinimcp_server.errors import readable_message
from fxhoudinimcp_server.handlers.viewport_handlers import (
    _DIRECTION_ANGLES,
    _angles,
    _apply_camera,
    _camera_spec,
    _capturable_node,
    _direction_name,
    _drawn_report,
    _flipbook_resolution,
    _flipbook_still,
    _max_size_arg,
    _no_mplay,
    _resolution_arg,
    _world_bounds,
    isolate,
    preserved_view,
    scratch_child,
    scratch_object,
)
from fxhoudinimcp_server.outputs import (
    at_frame,
    failure_verdict,
    license_error,
    reported_outputs,
    write_verdict,
)
from fxhoudinimcp_server.ui import (
    keep_viewer_state,
    require_ui,
    selection_hidden,
    viewer_context,
    viewer_stays_out_of_dops,
)

logger = logging.getLogger(__name__)


###### rendering.render_viewport


def _find_flipbook_output(output_path: str, frame: float) -> str:
    """Find the actual output file after flipbook, handling frame number insertion.

    Houdini's flipbook may insert a frame number into the filename
    (e.g. "output.0001.png" instead of "output.png") even for single-frame
    captures. This helper locates the actual file and optionally renames it.
    """
    if os.path.isfile(output_path):
        return output_path

    # Try common frame-number patterns
    base, ext = os.path.splitext(output_path)
    frame_int = int(frame)
    candidates = [
        f"{base}.{frame_int:04d}{ext}",
        f"{base}.{frame_int:03d}{ext}",
        f"{base}.{frame_int}{ext}",
        f"{base}_{frame_int:04d}{ext}",
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            # Rename to the originally requested path
            try:
                os.rename(candidate, output_path)
                return output_path
            except OSError:
                return candidate

    return output_path


def _size_flipbook(settings, resolution: list | None) -> None:
    """Write *resolution* if one is asked for, else the viewport as it is.

    A stash copies the viewer's Flipbook dialog settings, 1280x720 with Use
    Resolution on until an artist changes them (22.0.466), whatever the
    viewport's shape: an 897x853 view came out cropped top and bottom (a top
    view framed to a 2020x2360 box showed 1550 of its 2360), and through an
    800x400 camera the whole view was squeezed into 16:9. Off, the frame is
    the viewport's own size. The dialog's Output Zoom (25-100%) scales either,
    so it is put back to 100.
    """
    if resolution is not None:
        settings.useResolution(True)
        settings.resolution(tuple(resolution))
    else:
        settings.useResolution(False)
    settings.outputZoom(100)


def render_viewport(
    output_path: str,
    resolution: list = None,
    camera: str = None,
    node: str = None,
    visualize: bool = False,
    instances: str = None,
    max_size: int = None,
) -> dict:
    """Capture the current viewport to an image file.

    Args:
        output_path: Destination image path (e.g. .png, .jpg, .exr).
        resolution: Optional [width, height] override.
        camera: Optional camera node path to look through before capture.
        node: Render only this SOP's (or object's) geometry, through a
            temporary object merge; no display flag moves.
        visualize: Draw a heightfield's layers (a temporary
            heightfield_visualize on *node*, default the viewer's display
            SOP), not the grey surface.
        instances: Points to draw with it (e.g. scatter points), merged in.
        max_size: Cap on the image's longer side, in pixels.

    With node/visualize/instances the viewer is put back afterwards
    (network, view, camera, Visible Objects) and the scratch nodes removed.
    """
    if node is not None or visualize or instances is not None:
        return _render_isolated(
            output_path, resolution, camera, node, visualize, instances, max_size
        )
    if max_size is not None:
        _max_size_arg(max_size)
    # Houdini variables ($JOB, $HIP) are expanded, as load_scene does: a
    # "$JOB/..." path reached os.makedirs or os.path as written and failed.
    output_path = hou.text.expandString(output_path)
    require_ui(
        "capture a viewport screenshot",
        alternative="For a rendered image without a UI, build a ROP and use start_render.",
    )
    # Ensure the output directory exists
    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    # Get the current scene viewer
    scene_viewer = None
    for pane_tab in hou.ui.paneTabs():
        if pane_tab.type() == hou.paneTabType.SceneViewer:
            scene_viewer = pane_tab
            break

    if scene_viewer is None:
        raise RuntimeError("No Scene Viewer pane found. A viewport must be open to capture.")

    viewport = scene_viewer.curViewport()

    # Optionally set the camera
    if camera is not None:
        cam_node = hou.node(camera)
        if cam_node is None:
            raise ValueError(f"Camera node not found: {camera}")
        viewport.setCamera(cam_node)

    cur_frame = hou.frame()

    # Build the flipbook settings for image capture
    settings = scene_viewer.flipbookSettings().stash()
    _no_mplay(settings)
    settings.frameRange((cur_frame, cur_frame))
    settings.output(output_path)

    if resolution is not None and len(resolution) != 2:
        raise ValueError("resolution must be a list of [width, height]")
    if max_size is not None:
        resolution = _flipbook_resolution(viewport, resolution, max_size)
    _size_flipbook(settings, resolution)

    # Use the flipbook approach for a single-frame capture
    # A selected object would be drawn with its selection outline.
    with selection_hidden():
        scene_viewer.flipbook(viewport, settings)

    # Handle frame number that flipbook may insert into the filename
    actual_path = _find_flipbook_output(output_path, cur_frame)

    return {
        "success": True,
        "output_path": actual_path,
        "file_exists": os.path.isfile(actual_path),
        "resolution": resolution,
        "camera": camera,
        "frame": cur_frame,
        # Which network the image shows, and a note when it is a DOP view.
        **viewer_context(scene_viewer),
    }


def _first_scene_viewer():
    for pane_tab in hou.ui.paneTabs():
        if pane_tab.type() == hou.paneTabType.SceneViewer:
            return pane_tab
    raise RuntimeError("No Scene Viewer pane found. A viewport must be open to capture.")


def _displayed_sop(scene_viewer):
    """The SOP the viewer draws (its network's display node), or a ValueError asking for node=."""
    with contextlib.suppress(Exception):
        network = scene_viewer.pwd()
        if network.childTypeCategory() == hou.sopNodeTypeCategory():
            shown = network.displayNode()
            if shown is not None:
                return shown
    raise ValueError(
        "The viewer is not inside a SOP network, so there is no display node to use: pass node=."
    )


def _merge_points(obj, shown, instances: str):
    """Merge the points at *instances* (world space) after *shown*; the merge SOP."""
    points = scratch_child(obj, "object_merge", "instances")
    points.parm("objpath1").set(instances)
    points.parm("xformtype").set(1)
    merged = scratch_child(obj, "merge", "with_instances")
    merged.setInput(0, shown)
    merged.setInput(1, points)
    return merged


def _render_isolated(output_path, resolution, camera, node, visualize, instances, max_size) -> dict:
    """render_viewport of one node (optionally visualised), through a scratch object."""
    output_path = hou.text.expandString(output_path)
    require_ui(
        "capture a viewport screenshot",
        alternative="For a rendered image without a UI, build a ROP and use start_render.",
    )
    size = _resolution_arg(resolution)
    cap = _max_size_arg(max_size)
    spec = _camera_spec(camera)
    if instances is not None and hou.node(instances) is None:
        raise ValueError(f"Node not found: {instances}")
    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    scene_viewer = _first_scene_viewer()
    source = _capturable_node(node) if node is not None else _displayed_sop(scene_viewer)
    cur_frame = hou.frame()
    notes: list = []
    with (
        scratch_object([source.path()]) as (obj, merge),
        keep_viewer_state(),
        preserved_view(scene_viewer) as state,
    ):
        shown = merge
        if visualize:
            try:
                shown = scratch_child(obj, "heightfield_visualize", "visualize")
            except hou.OperationFailed as e:
                raise ValueError(
                    f"No heightfield_visualize SOP in this Houdini: {readable_message(e)}"
                ) from e
            shown.setInput(0, merge)
        if instances is not None:
            shown = _merge_points(obj, shown, instances)
        shown.setDisplayFlag(True)
        shown.setRenderFlag(True)
        with contextlib.suppress(Exception):
            errors = list(shown.errors())
            if errors:
                notes.append(f"{shown.type().name()} reported: {errors[0][:200]}")
        viewport = scene_viewer.curViewport()
        isolate(scene_viewer, viewport, obj)
        box = _world_bounds(source)
        state["view_changed"] = True
        view: dict = {}
        if spec is not None:
            view = _apply_camera(scene_viewer, viewport, spec, box, state)
        elif box is not None:
            viewport.frameBoundingBox(hou.BoundingBox(*box))
        drawn = _drawn_report(scene_viewer, viewport, source=source.path(), sop=shown)
        flip_size = _flipbook_resolution(viewport, size, cap)
        actual_path = _flipbook_still(scene_viewer, viewport, output_path, cur_frame, flip_size)
    result = {
        "success": True,
        "output_path": actual_path,
        "file_exists": os.path.isfile(actual_path),
        "resolution": flip_size,
        "camera": camera if isinstance(camera, str) else None,
        "frame": cur_frame,
        "node": source.path(),
        "visualized": bool(visualize),
        "drawn": drawn,
        "restored": not state.get("not_restored"),
    }
    if view:
        result["view_applied"] = view
    if state.get("not_restored"):
        result["not_restored"] = state["not_restored"]
    if notes:
        result["note"] = " ".join(notes)
    return result


###### rendering.render_sheet

_SHEET_MAX_TILES = 64
_SHEET_TILE = [320, 240]
#: Elevation of an orbit's views: looking down 25 degrees.
_ORBIT_ELEVATION = -25.0


def _sheet_grid(count: int, columns: int | None) -> tuple:
    """(columns, rows) for *count* tiles: *columns* if given, else about square."""
    cols = int(columns) if columns else math.ceil(math.sqrt(count))
    cols = max(1, min(cols, count))
    return cols, math.ceil(count / cols)


def _sheet_tiles(
    start=None,
    end=None,
    step=1,
    views=None,
    orbit=None,
    elevation=None,
    values=None,
    attribute=None,
) -> list:
    """The tiles of a sheet, each {"label", and one of frame / angles / value}.

    Exactly one source is used: a frame range, named or [rx, ry] views, an
    orbit of N views, or the values of an attribute.
    """
    given = [
        name
        for name, value in (
            ("start/end", start if start is not None else end),
            ("views", views),
            ("orbit", orbit),
            ("by_attribute", values),
        )
        if value is not None
    ]
    if len(given) != 1:
        raise ValueError(
            "Pass exactly one of start/end (frames), views, orbit or by_attribute"
            + (f"; got {', '.join(given)}." if given else ".")
        )
    tiles: list = []
    if given[0] == "start/end":
        if start is None or end is None:
            raise ValueError("A frame sheet needs both start and end.")
        step = int(step or 1)
        frames = list(range(int(start), int(end) + 1, step)) if step >= 1 else []
        tiles = [{"frame": f, "label": f"f{f}"} for f in frames]
    elif given[0] == "views":
        if isinstance(views, str) or not views:
            raise ValueError("views must be a list of direction names or [rx, ry] pairs.")
        for index, view in enumerate(views):
            angles = _angles(view, f"views[{index}]")
            label = (
                _direction_name(view)
                if isinstance(view, str)
                else ",".join(f"{a:g}" for a in angles[:2])
            )
            tiles.append({"angles": angles, "label": label})
    elif given[0] == "orbit":
        if isinstance(orbit, bool) or not isinstance(orbit, int) or orbit < 2:
            raise ValueError(f"orbit must be a number of views >= 2, got {orbit!r}")
        tilt = _ORBIT_ELEVATION if elevation is None else float(elevation)
        for index in range(orbit):
            turn = round(index * 360.0 / orbit, 4)
            tiles.append({"angles": (tilt, turn, 0.0), "label": f"ry {turn:g}"})
    else:
        tiles = [{"value": value, "label": f"{attribute}={value}"} for value in values]
    if not 1 <= len(tiles) <= _SHEET_MAX_TILES:
        raise ValueError(f"That gives {len(tiles)} tiles; a sheet takes 1 to {_SHEET_MAX_TILES}.")
    return tiles


def _attribute_values(geo, name: str) -> tuple:
    """("prims" | "points", sorted unique values) of the int or string attribute *name*."""
    from_class = (
        ("prims", geo.findPrimAttrib, "prim"),
        ("points", geo.findPointAttrib, "point"),
    )
    for group_type, find, prefix in from_class:
        attrib = find(name)
        if attrib is None:
            continue
        if attrib.size() != 1:
            raise ValueError(
                f"by_attribute needs a single-component attribute; {name} has {attrib.size()}."
            )
        data = attrib.dataType()
        if data == hou.attribData.String:
            values = getattr(geo, f"{prefix}StringAttribValues")(name)
        elif data == hou.attribData.Int:
            values = getattr(geo, f"{prefix}IntAttribValues")(name)
        else:
            raise ValueError(
                f"by_attribute takes an integer or string attribute; {name} is {data}. "
                "Bucket a float attribute into an integer one first."
            )
        return group_type, sorted(set(values))
    raise ValueError(f"No primitive or point attribute '{name}' on the node's geometry.")


def _group_for(name: str, value) -> str:
    """An ad-hoc group selecting elements whose *name* is *value*."""
    if isinstance(value, str):
        return f'@{name}="{value}"'
    return f"@{name}=={value}"


def _set_menu(parm, token: str) -> None:
    """Set a menu parm by its token, by index when the parm is an ordinal."""
    with contextlib.suppress(Exception):
        items = list(parm.menuItems())
        if token in items:
            parm.set(items.index(token))
            return
    parm.set(token)


def _stitch_tiles(tiles: list, columns: int | None, output: str) -> dict:
    """Tile *tiles* [(path, label)] into one image at *output*.

    PIL when Houdini's Python has it, else OpenImageIO. Returns
    {"stitched_with", "grid", "tile_resolution"} or {"stitched_with": None}.
    """
    present = [(path, label) for path, label in tiles if os.path.isfile(path)]
    if not present:
        return {"stitched_with": None}
    cols, rows = _sheet_grid(len(present), columns)
    out_dir = os.path.dirname(output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        Image = None
    if Image is not None:
        images = [(Image.open(path).convert("RGB"), label) for path, label in present]
        width, height = images[0][0].size
        sheet = Image.new("RGB", (cols * width, rows * height), (32, 32, 32))
        draw = ImageDraw.Draw(sheet)
        for index, (image, label) in enumerate(images):
            if image.size != (width, height):
                image = image.resize((width, height))
            x, y = (index % cols) * width, (index // cols) * height
            sheet.paste(image, (x, y))
            draw.rectangle((x, y, x + 6 * len(label) + 8, y + 14), fill=(0, 0, 0))
            draw.text((x + 3, y + 2), label, fill=(255, 255, 255))
        sheet.save(output)
        return {"stitched_with": "PIL", "grid": [cols, rows], "tile_resolution": [width, height]}
    try:
        import OpenImageIO as oiio
    except ImportError:
        return {"stitched_with": None}
    buffers = [(oiio.ImageBuf(path), label) for path, label in present]
    spec = buffers[0][0].spec()
    width, height = spec.width, spec.height
    sheet = oiio.ImageBuf(oiio.ImageSpec(cols * width, rows * height, 3, oiio.UINT8))
    oiio.ImageBufAlgo.fill(sheet, (0.125, 0.125, 0.125))
    for index, (buf, label) in enumerate(buffers):
        x, y = (index % cols) * width, (index // cols) * height
        oiio.ImageBufAlgo.paste(sheet, x, y, 0, 0, buf)
        with contextlib.suppress(Exception):
            oiio.ImageBufAlgo.render_text(sheet, x + 3, y + 14, label, 12)
    if not sheet.write(output):
        return {"stitched_with": None}
    return {
        "stitched_with": "OpenImageIO",
        "grid": [cols, rows],
        "tile_resolution": [width, height],
    }


def render_sheet(
    output_path: str,
    start: int = None,
    end: int = None,
    step: int = 1,
    views: list = None,
    orbit: int = None,
    elevation: float = None,
    by_attribute: str = None,
    node: str = None,
    camera=None,
    resolution: list = None,
    columns: int = None,
    frame: float = None,
) -> dict:
    """Tile viewport captures into one contact sheet, inside this Houdini (no hython, no seat).

    Args:
        output_path: Sheet image path.
        start, end, step: One tile per frame of the range.
        views: One tile per view at one frame: direction names ('front',
            'top', 'persp', ...) or [rx, ry] degrees, each framing the
            subject in perspective.
        orbit: One tile per view of an orbit of this many views around the
            subject, looking down *elevation* degrees (default 25).
        by_attribute: One tile per value of this integer or string prim (or
            point) attribute on *node*: each piece is cut out with a temporary
            blast and framed on its own.
        node: Draw only this SOP's (or object's) geometry, through a temporary
            object; no display flag moves. Required with by_attribute.
        camera: A camera path to look through (frames only), or a view dict
            as capture_screenshot takes; with views/orbit/by_attribute only
            look_at, distance and clearance apply on top of each tile's angle.
        resolution: [width, height] of each tile. Default [320, 240].
        columns: Tiles per row. Default: about square.
        frame: The frame for views/orbit/by_attribute (default: the current one).

    The frame, viewer and view are put back afterwards and the scratch nodes
    removed. Tiles are stitched with PIL, else OpenImageIO; with neither, the
    reply lists the tile images instead.
    """
    output_path = hou.text.expandString(output_path)
    require_ui(
        "render a sheet through the viewport",
        alternative="render_sheet(hython=True) renders it in a separate hython instead.",
    )
    size = _resolution_arg(resolution) or list(_SHEET_TILE)
    spec = _camera_spec(camera)
    if by_attribute is not None and node is None:
        raise ValueError("by_attribute needs node=: the SOP whose pieces to show.")
    source = _capturable_node(node) if node is not None else None
    per_view = views is not None or orbit is not None or by_attribute is not None
    if per_view and isinstance(spec, str):
        raise ValueError(
            "A camera path only fits a frame sheet: views, orbit and pieces each place the view."
        )
    if per_view and isinstance(spec, dict):
        extra = sorted(set(spec) - {"look_at", "distance", "clearance", "direction"})
        if extra or (views is not None or orbit is not None) and "direction" in spec:
            raise ValueError(
                "With views/orbit/by_attribute each tile sets the angle; camera may hold only "
                "look_at, distance and clearance (and direction for by_attribute)."
            )
    if frame is not None:
        float(frame)
    values = None
    group_type = None
    if by_attribute is not None:
        with at_frame(frame):
            group_type, values = _attribute_values(source.geometry(), by_attribute)
    tiles = _sheet_tiles(
        start, end, step, views, orbit, elevation, values=values, attribute=by_attribute
    )

    scene_viewer = _first_scene_viewer()
    folder = tempfile.mkdtemp(prefix="fxhoudinimcp_sheet_")
    previous_frame = hou.frame()
    written: list = []
    with contextlib.ExitStack() as stack:
        scratch = stack.enter_context(scratch_object([source.path()])) if source else None
        stack.enter_context(keep_viewer_state())
        state = stack.enter_context(preserved_view(scene_viewer))
        state["view_changed"] = True
        viewport = scene_viewer.curViewport()
        box = None
        piece = None
        if scratch is not None:
            obj, merge = scratch
            isolate(scene_viewer, viewport, obj)
            if by_attribute is not None:
                piece = scratch_child(obj, "blast", "piece")
                piece.setInput(0, merge)
                _set_menu(piece.parm("grouptype"), group_type)
                piece.parm("negate").set(1)
                piece.setDisplayFlag(True)
                piece.setRenderFlag(True)
        try:
            if frame is not None:
                hou.setFrame(float(frame))
            if scratch is not None and piece is None:
                box = _world_bounds(source)
            for index, tile in enumerate(tiles):
                if "frame" in tile:
                    hou.setFrame(tile["frame"])
                if piece is not None:
                    piece.parm("group").set(_group_for(by_attribute, tile["value"]))
                    box = _piece_bounds(piece)
                if "angles" in tile or piece is not None:
                    view = dict(spec or {})
                    view["direction"] = (
                        tile.get("angles")
                        or view.get("direction")
                        or (
                            *_DIRECTION_ANGLES["perspective"],
                            0.0,
                        )
                    )
                    if isinstance(view["direction"], str):
                        view["direction"] = (*_DIRECTION_ANGLES[view["direction"]], 0.0)
                    _apply_camera(scene_viewer, viewport, view, box, state)
                elif index == 0:
                    if spec is not None:
                        _apply_camera(scene_viewer, viewport, spec, box, state)
                    elif box is not None:
                        viewport.frameBoundingBox(hou.BoundingBox(*box))
                path = os.path.join(folder, f"tile_{index:03d}.png")
                written.append(
                    (
                        _flipbook_still(scene_viewer, viewport, path, hou.frame(), size),
                        tile["label"],
                    )
                )
        finally:
            hou.setFrame(previous_frame)
    stitched = _stitch_tiles(written, columns, output_path)
    missing = [label for path, label in written if not os.path.isfile(path)]
    result = {
        "success": stitched["stitched_with"] is not None,
        "method": "viewport flipbook in this Houdini",
        "tiles": len(written),
        "labels": [label for _, label in written],
        "tile_folder": folder,
        "restored": not state.get("not_restored"),
        **stitched,
    }
    if stitched["stitched_with"] is not None:
        result["output_path"] = output_path
        result["file_exists"] = os.path.isfile(output_path)
    else:
        result["tile_paths"] = [path for path, _ in written]
        result["note"] = (
            "Neither PIL nor OpenImageIO is importable in this Houdini's Python, so the tiles "
            "were not stitched: open tile_paths."
        )
    if missing:
        result["missing_tiles"] = missing
    if state.get("not_restored"):
        result["not_restored"] = state["not_restored"]
    if by_attribute is not None:
        result["by_attribute"] = by_attribute
        result["values"] = values
    if source is not None:
        result["node"] = source.path()
    return result


def _piece_bounds(sop) -> list | None:
    """World bounds of *sop* in the scratch object (which sits at the origin)."""
    with contextlib.suppress(Exception):
        box = sop.geometry().boundingBox()
        if box.isValid():
            return [*box.minvec(), *box.maxvec()]
    return None


###### rendering.render_quad_view


def render_quad_view(
    output_path: str,
    resolution: list = None,
) -> dict:
    """Capture all four viewport panes (quad view) to an image file.

    Args:
        output_path: Destination image path.
        resolution: Optional [width, height] override.
    """
    # Houdini variables ($JOB, $HIP) are expanded, as load_scene does: a
    # "$JOB/..." path reached os.makedirs or os.path as written and failed.
    output_path = hou.text.expandString(output_path)
    require_ui(
        "capture a quad view",
        alternative="For a rendered image without a UI, build a ROP and use start_render.",
    )
    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    scene_viewer = None
    for pane_tab in hou.ui.paneTabs():
        if pane_tab.type() == hou.paneTabType.SceneViewer:
            scene_viewer = pane_tab
            break

    if scene_viewer is None:
        raise RuntimeError("No Scene Viewer pane found.")

    viewports = scene_viewer.viewports()
    if not viewports:
        raise RuntimeError("No viewports available in the Scene Viewer.")

    if resolution is not None and len(resolution) != 2:
        raise ValueError("resolution must be a list of [width, height]")

    saved_files = []
    base, ext = os.path.splitext(output_path)

    # A flipbook of a viewport the layout hides writes nothing, so in the
    # default Single layout three of the four "saved" files never existed.
    # Show all four for the capture and put the user's layout back.
    layout = scene_viewer.viewportLayout()
    scene_viewer.setViewportLayout(hou.geometryViewportLayout.Quad)
    try:
        for vp in viewports:
            vp_name = vp.name()
            vp_output = f"{base}_{vp_name}{ext}"

            settings = scene_viewer.flipbookSettings().stash()

            _no_mplay(settings)
            settings.frameRange((hou.frame(), hou.frame()))
            settings.output(vp_output)

            _size_flipbook(settings, resolution)

            with selection_hidden():
                scene_viewer.flipbook(vp, settings)
            vp_output = _find_flipbook_output(vp_output, hou.frame())
            saved_files.append(
                {
                    "viewport": vp_name,
                    "output_path": vp_output,
                    "file_exists": os.path.isfile(vp_output),
                }
            )
    finally:
        scene_viewer.setViewportLayout(layout)

    return {
        "success": all(entry["file_exists"] for entry in saved_files),
        "viewports": saved_files,
        "frame": hou.frame(),
        **viewer_context(scene_viewer),
    }


###### rendering.list_render_nodes


def list_render_nodes() -> dict:
    """List all ROP (render) nodes in /out and embedded in other networks.

    Searches for all nodes whose type category is 'Driver'.
    """
    render_nodes = []

    def _collect_rops(parent):
        """Recursively collect all Driver-category nodes."""
        for child in parent.children():
            try:
                cat = child.type().category().name()
            except (hou.ObjectWasDeleted, AttributeError) as e:
                logger.debug("Could not read category for node: %s", e)
                continue
            if cat == "Driver":
                info = {
                    "name": child.name(),
                    "path": child.path(),
                    "type": child.type().name(),
                    "description": child.type().description(),
                }
                # Safely retrieve common render parameters
                try:
                    cam_parm = child.parm("camera")
                    info["camera"] = cam_parm.eval() if cam_parm else None
                except (hou.OperationFailed, AttributeError) as e:
                    logger.debug("Could not read camera parm for '%s': %s", child.path(), e)
                    info["camera"] = None
                try:
                    out_parm = (
                        child.parm("vm_picture")
                        or child.parm("copoutput")
                        or child.parm("sopoutput")
                        or child.parm("picture")
                    )
                    info["output"] = out_parm.eval() if out_parm else None
                except (hou.OperationFailed, AttributeError) as e:
                    logger.debug("Could not read output parm for '%s': %s", child.path(), e)
                    info["output"] = None
                render_nodes.append(info)
            # Recurse into children regardless of category
            try:
                if child.children():
                    _collect_rops(child)
            except (hou.OperationFailed, hou.ObjectWasDeleted) as e:
                logger.debug("Could not recurse into children of '%s': %s", child.path(), e)

    _collect_rops(hou.node("/"))

    return {
        "render_nodes": render_nodes,
        "count": len(render_nodes),
    }


###### rendering.get_render_settings


def get_render_settings(node_path: str) -> dict:
    """Get key render settings from a ROP node.

    Args:
        node_path: Path to the ROP/Driver node.
    """
    node, _, _ = _renderable(node_path)

    settings = {
        "node_path": node.path(),
        "node_type": node.type().name(),
        "description": node.type().description(),
    }

    # Common render parameters to extract
    parm_names = [
        "camera",
        "vm_picture",
        "picture",
        "copoutput",
        "sopoutput",
        "res_overridex",
        "res_overridey",
        "resx",
        "resy",
        "resoverride",
        "res",
        "res_mode",
        "resolutionx",
        "resolutiony",
        "f1",
        "f2",
        "f3",  # frame range start, end, increment
        "trange",  # time range mode
        "override_camerares",
        "renderer",
        "vm_renderengine",
    ]

    for parm_name in parm_names:
        try:
            parm = node.parm(parm_name)
            if parm is not None:
                val = parm.eval()
                # Convert hou types to plain Python
                if hasattr(val, "path"):
                    val = val.path()
                settings[parm_name] = val
        except (hou.OperationFailed, AttributeError) as e:
            logger.debug("Could not read render parm '%s': %s", parm_name, e)

    # Check for parm tuples (e.g. resolution)
    for tuple_name in ["res", "t"]:
        try:
            pt = node.parmTuple(tuple_name)
            if pt is not None:
                settings[tuple_name] = [p.eval() for p in pt]
        except (hou.OperationFailed, AttributeError) as e:
            logger.debug("Could not read render parm tuple '%s': %s", tuple_name, e)

    return settings


###### rendering.set_render_settings


def set_render_settings(node_path: str, settings: dict) -> dict:
    """Set render parameters on a ROP node.

    Args:
        node_path: Path to the ROP/Driver node.
        settings: Dict of parameter_name -> value pairs to set.
    """
    node, _, _ = _renderable(node_path)
    from fxhoudinimcp_server.handlers.parameter_handlers import _set_parameters

    # A frame range on a ROP is $FSTART/$FEND keyframes: a raw set() kept
    # them, the range did not change, and this still listed it as applied.
    for name in ("f1", "f2", "f3"):
        parm = node.parm(name)
        if name in settings and parm is not None and parm.keyframes():
            parm.deleteAllKeyframes()
    # The same writer as set_parameters, which reads back and names any
    # expression that kept a value from taking.
    reply = _set_parameters(node_path=node.path(), params=settings)
    errors = {entry["parm_name"]: entry["error"] for entry in reply["errors"]}
    result = {
        "success": not errors and not reply.get("expressions_kept"),
        "node_path": node.path(),
        "applied": {entry["parm_name"]: entry.get("new_value") for entry in reply["set"]},
        "errors": errors or None,
    }
    if reply.get("warning"):
        result["warning"] = reply["warning"]
    return result


###### rendering.create_render_node


def create_render_node(
    renderer: str,
    name: str = None,
    camera: str = None,
    output_path: str = None,
) -> dict:
    """Create a new render (ROP) node in /out.

    Args:
        renderer: Renderer type. Supported values include:
            'karma' (USD Karma), 'opengl' (OpenGL), 'ifd' (Mantra),
            'rop_geometry' (Geometry ROP), 'fetch', 'merge', etc.
        name: Optional node name. Auto-generated if not provided.
        camera: Optional camera path to assign.
        output_path: Optional output image/file path.
    """
    out_context = hou.node("/out")
    if out_context is None:
        raise RuntimeError("/out context not found.")

    # Friendly names to the ROP types /out really has, in preference order. The
    # SOP-level names (rop_geometry, rop_alembic) are what the docs advertise, but
    # in /out the same nodes are "geometry" and "alembic": creating those raised a
    # bare "Invalid node type name".
    renderer_map = {
        "karma": ("karma",),
        "opengl": ("opengl",),
        "mantra": ("ifd",),
        "ifd": ("ifd",),
        "geometry": ("geometry", "rop_geometry"),
        "rop_geometry": ("geometry", "rop_geometry"),
        "alembic": ("alembic", "rop_alembic"),
        "rop_alembic": ("alembic", "rop_alembic"),
        "usd": ("usd", "usd_rop"),
        "usd_rop": ("usd", "usd_rop"),
        "fbx": ("filmboxfbx", "rop_fbx"),
        "rop_fbx": ("filmboxfbx", "rop_fbx"),
        "filmboxfbx": ("filmboxfbx", "rop_fbx"),
        "gltf": ("gltf", "rop_gltf"),
        "rop_gltf": ("gltf", "rop_gltf"),
        "fetch": ("fetch",),
        "merge": ("merge",),
        "usdrender": ("usdrender",),
        "comp": ("comp",),
        "wedge": ("wedge",),
        "baketexture": ("baketexture",),
    }

    available = set(hou.ropNodeTypeCategory().nodeTypes())
    candidates = renderer_map.get(renderer.lower(), (renderer,))
    node_type = next((c for c in candidates if c in available), None)
    if node_type is None:
        raise ValueError(
            f"No ROP type '{renderer}' in this Houdini (looked for: {', '.join(candidates)}). "
            f"Renderer names that work here: {', '.join(sorted(k for k, v in renderer_map.items() if any(c in available for c in v)))}. "
            "Other types in /out can be passed by their exact name."
        )

    try:
        node = out_context.createNode(node_type, name)
    except hou.OperationFailed as e:
        raise ValueError(
            f"Failed to create render node of type '{node_type}': {readable_message(e)}"
        ) from e

    # Set camera if provided
    if camera is not None:
        cam_parm = node.parm("camera")
        if cam_parm is not None:
            cam_parm.set(camera)

    # Set output path if provided
    if output_path is not None:
        # Try common output parameter names
        for parm_name in ("vm_picture", "picture", "copoutput", "sopoutput"):
            parm = node.parm(parm_name)
            if parm is not None:
                parm.set(output_path)
                break

    place_new_node(node)

    return {
        "success": True,
        "node_path": node.path(),
        "node_type": node.type().name(),
        "renderer": renderer,
    }


# Renderers that write their image from a separate process (husk, mantra).
# render() can return before the file lands, and a Karma CPU frame took ~3 s to
# appear on a laptop: the 2 s default called that render "nothing was written".
_OUT_OF_PROCESS_RENDERERS = ("karma", "usdrender", "usdrender_rop", "ifd", "karmarendersettings")
_OUT_OF_PROCESS_GRACE = 20.0


def _render_grace(node) -> float:
    """Seconds to keep looking for a render's file: longer for out-of-process renderers.

    The wait ends the moment a file (or an error) appears, so the extra time is
    only paid by a render that really wrote nothing.
    """
    from fxhoudinimcp_server.config import output_grace_seconds

    grace = output_grace_seconds()
    if node.type().name().split("::")[0] in _OUT_OF_PROCESS_RENDERERS:
        return max(grace, _OUT_OF_PROCESS_GRACE)
    return grace


###### rendering.start_render

# Where ROP-style nodes keep their frame range.
_RANGE_PARMS = ("f1", "f2", "f3")


def _apply_frame_range_parms(node: hou.Node, frame_range: list) -> None:
    """Set trange/f1..f3 on a node whose execution is a button press."""
    trange = node.parm("trange")
    if trange is not None:
        # 1 is "Render Frame Range" on every stock ROP-style node.
        trange.set(1)
    values = [float(frame_range[0]), float(frame_range[1])]
    values.append(float(frame_range[2]) if len(frame_range) > 2 else 1.0)
    for name, value in zip(_RANGE_PARMS, values, strict=False):
        parm = node.parm(name)
        if parm is not None:
            # A File Cache's f1/f2 are $FSTART/$FEND keyframes, and set() under
            # an expression keeps the expression: [10, 20] rendered the whole
            # playbar range. Same fix as cache_handlers._set_frame_parm.
            if parm.keyframes():
                parm.deleteAllKeyframes()
            parm.set(value)


def _renderable(node_path: str) -> tuple[hou.Node, str, bool]:
    """The node, its category, and whether it has render() (else an execute button).

    Category is the wrong test. It rejected the LOP usdrender_rop, which is how
    Solaris renders, along with SOP ROPs and a File Cache's Save to Disk -- so a
    recorded session pressed all of those by hand through execute_python ten
    times. What matters is whether the node can be executed at all. Used by
    start_render and get_render_progress alike, so the two agree: a session
    started a Karma LOP with one and was refused progress on it by the other.
    """
    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")
    category = node.type().category().name()
    can_render = hasattr(node, "render")
    if not can_render and node.parm("execute") is None:
        buttons = [
            p.name() for p in node.parms() if p.parmTemplate().type() == hou.parmTemplateType.Button
        ]
        raise ValueError(
            f"{node_path} ({category}) has neither render() nor an 'execute' "
            f"button, so there is nothing to trigger."
            + (f" Buttons it does have: {buttons[:6]}" if buttons else "")
        )
    return node, category, can_render


# node_path -> the background render launched for it. One per node: a second
# launch on the same node replaces the record (the earlier process keeps going).
_BACKGROUND_RENDERS: dict[str, dict[str, Any]] = {}

_BACKGROUND_SCRIPT = """
import sys, hou
hip, path, has_range, start, end, inc = sys.argv[1:7]
hou.hipFile.load(hip, suppress_save_prompt=True, ignore_load_warnings=True)
node = hou.node(path)
if node is None:
    raise SystemExit("node not found after load: " + path)
if hasattr(node, "render"):
    if has_range == "1":
        node.render(frame_range=(float(start), float(end), float(inc)), verbose=True, output_progress=True)
    else:
        node.render(verbose=True, output_progress=True)
else:
    # A button-pressed node (File Cache) takes its range from trange/f1..f3,
    # which this branch never set: the whole playbar range was written.
    if has_range == "1":
        if node.parm("trange") is not None:
            node.parm("trange").set(1)
        for name, value in zip(("f1", "f2", "f3"), (start, end, inc)):
            parm = node.parm(name)
            if parm is not None:
                if parm.keyframes():
                    parm.deleteAllKeyframes()
                parm.set(float(value))
    node.parm("execute").pressButton()
errors = list(node.errors())
if errors:
    sys.stderr.write("\\n".join(errors) + "\\n")
    raise SystemExit(1)
"""


def _launch_background_render(node: hou.Node, frame_range: list | None) -> dict[str, Any]:
    """Render from a fresh hython on the saved hip, like Houdini's own
    "Render in Background", and return at once with how to follow it."""
    if hou.hipFile.isNewFile():
        raise ValueError("Save the hip first (save_scene): a background render loads it from disk")
    hou.hipFile.save()
    hfs = hou.getenv("HFS") or ""
    hython = os.path.join(hfs, "bin", "hython.exe" if os.name == "nt" else "hython")
    if not os.path.exists(hython):
        raise ValueError(f"hython not found at {hython}; cannot render in the background")
    fd, log_path = tempfile.mkstemp(prefix="fxhoudinimcp_render_", suffix=".log")
    log = os.fdopen(fd, "w")
    args = [hython, "-c", _BACKGROUND_SCRIPT, hou.hipFile.path(), node.path()]
    if frame_range is not None:
        inc = frame_range[2] if len(frame_range) > 2 else 1
        args += ["1", str(frame_range[0]), str(frame_range[1]), str(inc)]
    else:
        args += ["0", "0", "0", "1"]
    env = dict(os.environ)
    # Houdini 21 hython segfaults on the bundled OpenFX plugins; harmless elsewhere.
    env.setdefault("HOUDINI_DISABLE_OPENFX_DEFAULT_PATH", "1")
    proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env=env)  # noqa: S603
    record = {
        "pid": proc.pid,
        "proc": proc,
        "log": log_path,
        "started": time.time(),
        "frame_range": frame_range,
    }
    _BACKGROUND_RENDERS[node.path()] = record
    return record


def _background_status(node_path: str) -> dict[str, Any] | None:
    record = _BACKGROUND_RENDERS.get(node_path)
    if record is None:
        return None
    proc = record["proc"]
    code = proc.poll()
    tail = ""
    with (
        contextlib.suppress(OSError),
        open(record["log"], encoding="utf-8", errors="replace") as fh,
    ):
        tail = "".join(fh.readlines()[-8:])
    return {
        "pid": record["pid"],
        "running": code is None,
        "exit_code": code,
        "elapsed_seconds": round(time.time() - record["started"], 1),
        "frame_range": record["frame_range"],
        "log": record["log"],
        "log_tail": tail,
    }


#: "Wait for Render to Complete" on a Karma LOP / usdrender ROP. Off on a fresh
#: Karma LOP (22.0.429): execute returns while husk is still rendering, and a
#: foreground render is judged before its file exists.
_FOREGROUND_PARM = "soho_foreground"


def _override_targets(node: hou.Node, overrides: dict) -> list[tuple[str, hou.Parm, Any]]:
    """(name, parm, value) for every override, all resolved before anything is set.

    A parm tuple ("t", "res") takes a list and expands to its components, the
    way set_parameters takes it.
    """
    if not isinstance(overrides, dict) or not overrides:
        raise ValueError("overrides must be a non-empty {parm_name: value} dict.")
    targets: list[tuple[str, hou.Parm, Any]] = []
    missing: list[str] = []
    for name, value in overrides.items():
        parm = node.parm(name)
        if parm is not None:
            targets.append((name, parm, value))
            continue
        parm_tuple = node.parmTuple(name)
        if parm_tuple is None:
            missing.append(name)
            continue
        if not isinstance(value, (list, tuple)) or len(value) != len(parm_tuple):
            raise ValueError(
                f"Override '{name}' on {node.path()} has {len(parm_tuple)} components; "
                f"pass a list of {len(parm_tuple)} values."
            )
        targets.extend(
            (f"{name}[{i}]", component, v)
            for i, (component, v) in enumerate(zip(parm_tuple, value, strict=True))
        )
    if missing:
        from fxhoudinimcp_server.handlers.parameter_handlers import parm_labels, suggest_parms

        labels = parm_labels(node)
        hints = {name: close for name in missing if (close := suggest_parms(name, labels))}
        hint = f" Did you mean: {hints}?" if hints else ""
        raise ValueError(
            f"No parameter(s) {missing} on {node.path()} to override; nothing was set.{hint}"
        )
    return targets


def _snapshot(parm: hou.Parm) -> dict[str, Any]:
    """What putting *parm* back needs: its keyframes (an expression is one), else its value."""
    keys = tuple(parm.keyframes())
    if keys:
        return {"keyframes": keys}
    if parm.parmTemplate().type() == hou.parmTemplateType.String:
        # "$HIP/render/$F4.exr" comes back as written, not as today's expansion.
        return {"value": parm.unexpandedString()}
    return {"value": parm.eval()}


def _restore_overrides(
    applied: list[tuple[str, hou.Parm, dict[str, Any]]],
) -> tuple[list[str], list[dict[str, str]]]:
    """Put every overridden parm back, expressions and keyframes included."""
    restored: list[str] = []
    failed: list[dict[str, str]] = []
    for name, parm, state in reversed(applied):
        try:
            with contextlib.suppress(Exception):
                parm.deleteAllKeyframes()
            if "keyframes" in state:
                for key in state["keyframes"]:
                    parm.setKeyframe(key)
            else:
                parm.set(state["value"])
            restored.append(name)
        except Exception as e:  # keep restoring the rest
            failed.append({"parm": name, "error": readable_message(e)})
    return restored, failed


def _apply_overrides(
    targets: list[tuple[str, hou.Parm, Any]],
    applied: list[tuple[str, hou.Parm, dict[str, Any]]],
) -> None:
    """Set each override, adding it to *applied* with what puts it back.

    set() over an expression keeps the expression, so the keys go first. A
    write Houdini refuses (a locked parm, a value of the wrong type) puts back
    what was already set and names the parm: the render never runs on half of
    the overrides.
    """
    for name, parm, value in targets:
        state = _snapshot(parm)
        try:
            if state.get("keyframes"):
                parm.deleteAllKeyframes()
            parm.set(value)
        except (hou.Error, TypeError) as e:
            # The refused parm is put back quietly: a write refused as locked
            # refuses the restore just the same, having changed nothing.
            _restore_overrides([(name, parm, state)])
            _, failed = _restore_overrides(applied)
            applied.clear()
            after = (
                f" These could not be put back: {failed}."
                if failed
                else " The overrides set before it were put back."
            )
            raise ValueError(
                f"Override '{name}' on {parm.node().path()} could not be set: "
                f"{readable_message(e)}. Nothing was rendered.{after}"
            ) from e
        applied.append((name, parm, state))


def start_render(
    node_path: str,
    frame_range: list = None,
    background: bool = False,
    overrides: dict = None,
) -> dict:
    """Begin rendering a ROP node.

    Args:
        node_path: Path to any node that renders or writes: a /out ROP, a
            LOP usdrender_rop, a SOP ROP Geometry or File Cache, and so on.
        frame_range: Optional [start, end] or [start, end, increment]. If not
            provided, the node's own frame range settings are used.
        background: Render from a separate hython on the saved hip and return
            at once; get_render_progress follows the process. Off by default:
            a foreground render shows the user Houdini's own progress dialog,
            and the dispatcher puts no deadline on it.
        overrides: {parm_name: value} set for this render only and put back
            afterwards, expressions and keyframes included, even when the
            render fails. A parm tuple takes a list. Foreground only.
    """
    node, category, can_render = _renderable(node_path)

    if frame_range is not None and len(frame_range) < 2:
        raise ValueError("frame_range must have at least [start, end].")

    if background and overrides:
        raise ValueError(
            "overrides apply to a foreground render only: a background render loads "
            "the saved hip, which does not carry them. Save the values first, or "
            "render in the foreground."
        )

    if background:
        before = reported_outputs(node)
        record = _launch_background_render(node, frame_range)
        return {
            "node_path": node_path,
            "category": category,
            "method": "hython subprocess on the saved hip",
            "background": True,
            "pid": record["pid"],
            "log": record["log"],
            "frame_range": frame_range,
            "success": True,
            "wrote_files": False,
            "status": "launched",
            "message": (
                "Background render launched; poll get_render_progress(node_path) "
                "for the process state, its log tail and the files on disk."
            ),
            "outputs": before,
        }

    overrides = dict(overrides or {})
    foreground = node.parm(_FOREGROUND_PARM)
    forced = foreground is not None and _FOREGROUND_PARM not in overrides and not foreground.eval()
    if forced:
        overrides[_FOREGROUND_PARM] = 1
    targets = _override_targets(node, overrides) if overrides else []
    applied: list[tuple[str, hou.Parm, dict[str, Any]]] = []
    try:
        _apply_overrides(targets, applied)
        result = _render_in_foreground(node, node_path, category, can_render, frame_range)
    finally:
        restored, not_restored = _restore_overrides(applied)
    if applied:
        result["overrides_applied"] = [name for name, _, _ in applied]
        result["overrides_restored"] = restored
        if not_restored:
            result["overrides_not_restored"] = not_restored
    if forced:
        result["foreground_forced"] = (
            f"{_FOREGROUND_PARM} (Wait for Render to Complete) was off, which lets "
            "the render return before the image is written; it was on for this "
            "render and is off again."
        )
    return result


def _render_in_foreground(
    node: hou.Node,
    node_path: str,
    category: str,
    can_render: bool,
    frame_range: list | None,
) -> dict:
    """Render *node* in this Houdini and judge what it wrote."""
    execute_parm = node.parm("execute")
    # Snapshot the outputs so "did this write anything" is answerable afterwards
    # rather than inferred from a call that did not raise. Evaluated at the first
    # frame of the range: at the playbar's frame a render of 41-43 read as
    # "nothing was written".
    first_frame = float(frame_range[0]) if frame_range else None
    with at_frame(first_frame):
        before = reported_outputs(node)

    method = None
    try:
        if can_render:
            method = "render()"
            if frame_range is not None:
                start = float(frame_range[0])
                end = float(frame_range[1])
                inc = float(frame_range[2]) if len(frame_range) > 2 else 1.0
                # RopNode.render takes the increment as the third element of
                # frame_range; there is no frame_increment keyword.
                node.render(frame_range=(start, end, inc), output_progress=True)
            else:
                node.render(output_progress=True)
        else:
            # A File Cache SOP has no render(); its Save to Disk is a button, and
            # its range lives on trange/f rather than in a render() argument.
            method = "execute button"
            if frame_range is not None:
                _apply_frame_range_parms(node, frame_range)
            # Save to Disk is a Python callback: pressButton() would answer
            # its exception with a modal window instead of raising.
            press(execute_parm)
    except (hou.OperationFailed, CallbackError) as e:
        with at_frame(first_frame):
            verdict = failure_verdict(node, before, e)
        return {"node_path": node_path, "category": category, "method": method, **verdict}

    # render() returning without raising is NOT evidence that a render happened.
    # A LOP usdrender_rop shells out to husk, and when husk exits non-zero -- no
    # license, bad scene, missing camera -- the ROP records the error and render()
    # still returns normally. Reporting that as success is how a caller ends up
    # verifying a screenshot of an image that was never written, so read the
    # node's own errors and whether the files moved.
    with at_frame(first_frame):
        verdict = write_verdict(node, before, action="Render", grace=_render_grace(node))
    return {
        "node_path": node_path,
        "category": category,
        "method": method,
        "background": False,
        "frame_range": frame_range,
        **verdict,
    }


###### rendering.render_node_network


def render_node_network(
    node_path: str,
    output_path: str,
) -> dict:
    """Take a screenshot of the network editor showing a specific node's network.

    Args:
        node_path: Path to the node whose network to capture.
        output_path: Destination image path.
    """
    # Houdini variables ($JOB, $HIP) are expanded, as load_scene does: a
    # "$JOB/..." path reached os.makedirs or os.path as written and failed.
    output_path = hou.text.expandString(output_path)
    require_ui(
        "screenshot the network editor",
        alternative="get_network_overview and list_children describe a network without a UI.",
    )
    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")

    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    # Find a network editor pane tab
    network_editor = None
    for pane_tab in hou.ui.paneTabs():
        if pane_tab.type() == hou.paneTabType.NetworkEditor:
            network_editor = pane_tab
            break

    if network_editor is None:
        raise RuntimeError("No Network Editor pane found.")

    # Capture the network editor as an image via Qt widget grab
    from fxhoudinimcp_server.handlers.viewport_handlers import _capture_pane_tab_qt

    # Navigate to the node's parent network so the node is visible, and frame
    # it, keeping the viewer's cameras and the selection. Showing a node in a
    # DOP network takes the Scene Viewer there too; the editor goes back after
    # the shot.
    with viewer_stays_out_of_dops():
        with keep_viewer_state():
            parent = node.parent()
            if parent is not None:
                network_editor.cd(parent.path())
            network_editor.setCurrentNode(node)
            network_editor.homeToSelection()

        _capture_pane_tab_qt(network_editor, output_path)

    return {
        "success": True,
        "node_path": node_path,
        "output_path": output_path,
        "file_exists": os.path.isfile(output_path),
    }


###### rendering.get_render_progress


def get_render_progress(node_path: str) -> dict:
    """Check the render status / progress of a ROP node.

    Args:
        node_path: Path to the ROP/Driver node.
    """
    node, category, _ = _renderable(node_path)

    is_cooking = node.isCooking() if hasattr(node, "isCooking") else False

    # Check cook count as a proxy for activity
    try:
        cook_count = node.cookCount()
    except (hou.OperationFailed, AttributeError) as e:
        logger.debug("Could not read cook count for '%s': %s", node_path, e)
        cook_count = None

    # Check for errors and warnings
    try:
        errors = list(node.errors()) if node.errors() else []
    except (hou.OperationFailed, AttributeError) as e:
        logger.debug("Could not read errors for '%s': %s", node_path, e)
        errors = []

    try:
        warnings = list(node.warnings()) if node.warnings() else []
    except (hou.OperationFailed, AttributeError) as e:
        logger.debug("Could not read warnings for '%s': %s", node_path, e)
        warnings = []

    # Retrieve the output file to check if it exists on disk
    output_file = None
    for parm_name in ("vm_picture", "picture", "copoutput", "sopoutput"):
        try:
            parm = node.parm(parm_name)
            if parm is not None:
                output_file = parm.eval()
                break
        except (hou.OperationFailed, AttributeError) as e:
            logger.debug("Could not read output parm '%s': %s", parm_name, e)

    output_exists = False
    if output_file:
        try:
            output_exists = os.path.isfile(output_file)
        except OSError as e:
            logger.debug("Could not check output file existence: %s", e)

    background = _background_status(node_path)
    outputs = reported_outputs(node)
    if output_file is None:
        for entry in outputs:
            if entry.get("path"):
                output_file = entry["path"]
                output_exists = bool(entry.get("exists"))
                break
    return {
        "node_path": node.path(),
        "category": category,
        "is_cooking": is_cooking,
        "cook_count": cook_count,
        "errors": errors,
        "warnings": warnings,
        "license_error": license_error(errors),
        "output_file": output_file,
        "output_exists": output_exists,
        "outputs": outputs,
        "background": background,
        "done": (background is not None and not background["running"])
        or (background is None and not is_cooking),
    }


###### Registration

register_handler("rendering.render_viewport", render_viewport)
register_handler("rendering.render_quad_view", render_quad_view)
register_handler("rendering.render_sheet", render_sheet)
register_handler("rendering.list_render_nodes", list_render_nodes)
register_handler("rendering.get_render_settings", get_render_settings)
register_handler("rendering.set_render_settings", set_render_settings)
register_handler("rendering.create_render_node", create_render_node)
register_handler("rendering.start_render", start_render)
register_handler("rendering.render_node_network", render_node_network)
register_handler("rendering.get_render_progress", get_render_progress)
