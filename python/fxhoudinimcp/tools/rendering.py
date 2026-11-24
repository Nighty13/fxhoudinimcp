"""MCP tool definitions for Houdini rendering operations.

Provides tools for viewport capture, render node management, render execution,
and render progress monitoring.
"""

from __future__ import annotations

# Built-in
import asyncio
import contextlib
import json
import os
from pathlib import Path
from typing import Any

# Third-party
from fxhoudinimcp._sdk import Context

# Internal
from fxhoudinimcp.bridge import NO_TIMEOUT
from fxhoudinimcp.houdini_discovery import find_all_hython, find_hython
from fxhoudinimcp.server import _get_bridge, mcp
from fxhoudinimcp.tools.viewport import capture_path


@mcp.tool()
async def render_viewport(
    ctx: Context,
    output_path: str | None = None,
    resolution: list[int] | None = None,
    camera: str | None = None,
    settle_seconds: float = 0,
    node: str | None = None,
    visualize: bool = False,
    instances: str | None = None,
    max_size: int | None = None,
) -> dict:
    """Capture the current 3D viewport to an image file.

    Args:
        output_path: Image file path; variables ($JOB) are expanded. Default: a
            new PNG in the temp dir.
        resolution: [width, height] in pixels. Default: the viewport's own size.
        camera: Camera node path.
        settle_seconds: Wait this long before capturing, without blocking
            Houdini, so a Karma viewport can converge after a change. Use
            this instead of a shell sleep between calls. Capped at 120.
        node: Render only this SOP or object (a temporary object merge; no
            display flag moves), framed on it.
        visualize: For a heightfield: draw its layers through a temporary
            heightfield_visualize instead of the grey surface. Uses node, or
            the viewer's display SOP.
        instances: Points to draw with it, e.g. scatter/instance points.
        max_size: Cap on the longer side in pixels; scales down, keeps shape.

    With node/visualize/instances everything is put back afterwards
    (`restored`) and the scratch nodes are removed; `drawn` says what the
    image holds.
    """
    bridge = _get_bridge(ctx)
    if settle_seconds > 0:
        await asyncio.sleep(min(settle_seconds, 120))
    params: dict[str, Any] = {"output_path": capture_path(output_path, "viewport")}
    for key, value in (
        ("resolution", resolution),
        ("camera", camera),
        ("node", node),
        ("instances", instances),
        ("max_size", max_size),
    ):
        if value is not None:
            params[key] = value
    if visualize:
        params["visualize"] = True
    return await bridge.execute("rendering.render_viewport", params)


@mcp.tool()
async def render_quad_view(
    ctx: Context,
    output_path: str | None = None,
    resolution: list[int] | None = None,
) -> dict:
    """Capture all four viewport panes to separate images.

    Args:
        output_path: Base image path; viewport names are appended, variables
            ($JOB) are expanded.
            Default: a new PNG in the temp dir.
        resolution: [width, height] in pixels. Default: the viewport's own size.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"output_path": capture_path(output_path, "quad")}
    if resolution is not None:
        params["resolution"] = resolution
    return await bridge.execute("rendering.render_quad_view", params)


@mcp.tool()
async def list_render_nodes(ctx: Context) -> dict:
    """List all render (ROP/Driver) nodes in the scene."""
    bridge = _get_bridge(ctx)
    return await bridge.execute("rendering.list_render_nodes", {})


@mcp.tool()
async def get_render_settings(ctx: Context, node_path: str) -> dict:
    """Get render settings from a ROP node.

    Args:
        node_path: ROP node path.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute("rendering.get_render_settings", {"node_path": node_path})


@mcp.tool()
async def set_render_settings(
    ctx: Context,
    node_path: str,
    settings: dict[str, Any] | None = None,
) -> dict:
    """Set render parameters on a ROP node.

    Args:
        node_path: ROP node path.
        settings: Parameter name-value pairs.
    """
    bridge = _get_bridge(ctx)
    # The default was a mutable {} literal. None is the safe default, but the
    # handler is still sent a dict, so the wire format does not change.
    return await bridge.execute(
        "rendering.set_render_settings",
        {"node_path": node_path, "settings": settings or {}},
    )


@mcp.tool()
async def create_render_node(
    ctx: Context,
    renderer: str,
    name: str | None = None,
    camera: str | None = None,
    output_path: str | None = None,
) -> dict:
    """Create a new render (ROP) node in /out.

    Args:
        renderer: Renderer type ('karma', 'opengl', 'mantra', 'rop_geometry', 'rop_alembic', 'usdrender', 'fetch', 'merge', 'rop_fbx', 'rop_gltf').
        name: Node name.
        camera: Camera node path.
        output_path: Output file path.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"renderer": renderer}
    if name is not None:
        params["name"] = name
    if camera is not None:
        params["camera"] = camera
    if output_path is not None:
        params["output_path"] = output_path
    return await bridge.execute("rendering.create_render_node", params)


# How long a render that wrote nothing is watched for the error husk posts late.
_ERROR_POLLS = 10
_ERROR_POLL_SECONDS = 0.5


@mcp.tool()
async def start_render(
    ctx: Context,
    node_path: str,
    frame_range: list[float] | None = None,
    background: bool = False,
    overrides: dict[str, Any] | None = None,
) -> dict:
    """Execute any node that renders or writes files.

    Foreground by default: Houdini shows its own progress dialog and the
    user can cancel. The call holds until the render finishes, however
    long that is; a client that hands a long call to a background task
    notifies you with the verdict. Do nothing else in Houdini meanwhile and never poll
    the disk.

    Not just /out ROPs: a LOP usdrender_rop (which is how Solaris renders), a
    SOP ROP Geometry, or a File Cache's Save to Disk all work, because what
    matters is whether the node can be executed rather than its category.

    The result reports the output path it wrote to and whether anything is
    actually on disk there, so a render that succeeds and writes nowhere is
    visible instead of silent.

    Args:
        node_path: Any node with a render() or an 'execute' button.
        frame_range: [start, end] or [start, end, increment].
        background: Render in a separate hython on the saved hip and return
            at once with status "launched"; get_render_progress reports the
            process, its log tail and the files. The user sees no progress
            in Houdini, so use it only when asked to keep working while a
            render runs.
        overrides: {parm_name: value} for this render only, e.g. a draft
            {"resolutionx": 640, "resolutiony": 360}; a parm tuple takes a
            list. Put back afterwards, expressions and keyframes included,
            even if the render fails (`overrides_applied`,
            `overrides_restored`). Foreground only. A Karma LOP's "Wait for
            Render to Complete" is switched on for the call when it is off
            (`foreground_forced`).
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"node_path": node_path, "background": background}
    if frame_range is not None:
        params["frame_range"] = frame_range
    if overrides:
        params["overrides"] = overrides
    result = await bridge.execute("rendering.start_render", params, timeout=NO_TIMEOUT)
    if isinstance(result, dict) and result.get("success") is False and not result.get("errors"):
        # A usdrender_rop gets husk's exit error (a missing license, a bad
        # scene) some time after render() returns: measured on 22.0.368, an
        # immediate follow-up still saw none. Poll briefly without blocking
        # Houdini, so "reported no errors" is not said about a render that
        # failed on one.
        progress: Any = None
        for _ in range(_ERROR_POLLS):
            await asyncio.sleep(_ERROR_POLL_SECONDS)
            progress = await bridge.execute(
                "rendering.get_render_progress", {"node_path": node_path}
            )
            if isinstance(progress, dict) and progress.get("errors"):
                break
        if isinstance(progress, dict) and progress.get("errors"):
            result["errors"] = progress["errors"]
            result["license_error"] = progress.get("license_error")
            result["message"] = (
                "Render failed: "
                + (
                    "no license for the renderer"
                    if progress.get("license_error")
                    else "the ROP reported errors"
                )
                + "; nothing was written."
            )
    return result


@mcp.tool()
async def render_node_network(
    ctx: Context,
    node_path: str,
    output_path: str | None = None,
) -> dict:
    """Capture a screenshot of a node's network editor view.

    Args:
        node_path: Node path to focus on.
        output_path: Image file path; variables ($JOB) are expanded. Default: a
            new PNG in the temp dir.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "rendering.render_node_network",
        {"node_path": node_path, "output_path": capture_path(output_path, "network")},
    )


@mcp.tool()
async def get_render_progress(ctx: Context, node_path: str) -> dict:
    """Progress of a render or write started with start_render.

    Accepts every node start_render accepts (a LOP usdrender_rop or Karma
    LOP, a SOP ROP, a File Cache), not only /out ROPs. Reports the node's
    errors with `license_error` singled out, the output files on disk, and
    for a background render the process state and the tail of its log.
    `done` is true when there is nothing left to wait for.

    Args:
        node_path: The node given to start_render.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute("rendering.get_render_progress", {"node_path": node_path})


# A 64-tile sheet through the viewport: a flipbook per tile on Houdini's main thread.
_SHEET_TIMEOUT = 600.0


def _hython_for(version: str | None):
    """The installed hython of the running Houdini's build, else the default one."""
    for hython in find_all_hython():
        if version and version in str(hython):
            return hython
    return find_hython()


@mcp.tool()
async def render_sheet(
    ctx: Context,
    start: int | None = None,
    end: int | None = None,
    step: int = 1,
    camera: str | dict[str, Any] | None = None,
    resolution: list[int] | None = None,
    columns: int | None = None,
    output_path: str | None = None,
    views: list[str | list[float]] | None = None,
    orbit: int | None = None,
    elevation: float | None = None,
    by_attribute: str | None = None,
    node: str | None = None,
    frame: float | None = None,
    hython: bool = False,
) -> dict:
    """Tile captures into one labelled contact sheet: frames, views or pieces.

    One image shows what a single frame cannot: a sim's spread (start..end),
    a model from several sides (views / orbit, at one frame), or every
    variant of a procedural asset (by_attribute: one tile per value, each
    piece cut out with a temporary blast and framed on its own).

    Rendered through the live viewport inside Houdini: no hython, no extra
    license seat. Everything moved (frame, viewer, view) is put back and the
    scratch nodes removed (`restored`). hython=True renders a frame range with
    the OpenGL ROP in a separate hython on a snapshot instead: it needs no
    viewport (headless sessions) but takes a license seat while it runs.

    Args:
        start: First frame (frame sheet).
        end: Last frame (frame sheet).
        step: Frame step; at most 64 tiles in all.
        camera: Camera object path (frame sheet); or a view dict like
            capture_screenshot's. With views/orbit/by_attribute only look_at,
            distance and clearance apply on top of each tile's angle.
        resolution: [width, height] of each tile. Default [320, 240].
        columns: Tiles per row. Default: about square.
        output_path: Sheet image path. Default: a new PNG in the temp dir.
        views: Direction names ('front', 'right', 'top', 'persp', ...) or
            [rx, ry] degrees, one perspective tile each, framing the subject.
        orbit: N views around the subject, looking down `elevation` degrees.
        elevation: Orbit tilt in degrees below the horizon. Default 25.
        by_attribute: Integer or string prim/point attribute on node: one
            tile per unique value.
        node: Draw only this SOP or object (required with by_attribute); no
            display flag moves.
        frame: Frame for views/orbit/by_attribute. Default: the current one.
        hython: Use the separate-hython OpenGL ROP path (frames only).
    """
    if start is not None or end is not None or hython:
        if start is None or end is None:
            raise ValueError("A frame sheet needs both start and end.")
        count = len(range(start, end + 1, max(1, step)))
        if step < 1 or count < 1 or count > 64:
            raise ValueError(
                f"start..end by step gives {count} frames; give 1 to 64 with step >= 1."
            )
    bridge = _get_bridge(ctx)
    if not hython:
        params: dict[str, Any] = {"output_path": capture_path(output_path, "sheet")}
        for key, value in (
            ("start", start),
            ("end", end),
            ("views", views),
            ("orbit", orbit),
            ("elevation", elevation),
            ("by_attribute", by_attribute),
            ("node", node),
            ("camera", camera),
            ("resolution", resolution),
            ("columns", columns),
            ("frame", frame),
        ):
            if value is not None:
                params[key] = value
        if start is not None:
            params["step"] = step
        return await bridge.execute("rendering.render_sheet", params, timeout=_SHEET_TIMEOUT)
    if views is not None or orbit is not None or by_attribute is not None or node is not None:
        raise ValueError(
            "hython=True renders a frame range only; views, orbit, by_attribute "
            "and node need the viewport path (hython=False)."
        )
    if camera is not None and not isinstance(camera, str):
        raise ValueError("hython=True takes a camera object path, not a view dict.")
    snapshot = await bridge.execute("scene.write_snapshot")
    hython = _hython_for(snapshot.get("houdini_version"))
    if hython is None:
        raise RuntimeError("No hython found to render the sheet. Set HYTHON.")
    args = {
        "hip": snapshot["snapshot"],
        "hip_file": snapshot.get("hip_file"),
        "camera": camera,
        "start": start,
        "end": end,
        "step": step,
        "resolution": resolution or [320, 240],
        "columns": columns,
        "output": capture_path(output_path, "sheet"),
    }
    child = Path(__file__).resolve().parent.parent / "sheet_child.py"
    try:
        process = await asyncio.create_subprocess_exec(
            str(hython),
            str(child),
            json.dumps(args),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(process.communicate(), timeout=600)
        except TimeoutError:
            process.kill()
            raise TimeoutError("The sheet's hython ran past 600 s and was stopped.") from None
    finally:
        with contextlib.suppress(OSError):
            os.remove(snapshot["snapshot"])
    text = out.decode("utf-8", errors="replace")
    marker = next(
        (line for line in reversed(text.splitlines()) if line.startswith("__MCP_RESULT__ ")), None
    )
    if marker is None:
        tail = "\n".join(text.splitlines()[-15:])
        raise RuntimeError(f"The sheet's hython exited with code {process.returncode}:\n{tail}")
    result = json.loads(marker[len("__MCP_RESULT__ ") :])
    if "error" in result:
        raise ValueError(result["error"])
    return {"success": True, "hip_file": snapshot.get("hip_file"), **result}
