"""MCP tool wrappers for Houdini scene-understanding / context operations.

Each tool delegates to the corresponding handler running inside Houdini
via the HTTP bridge.
"""

from __future__ import annotations

# Built-in
# Third-party
from fxhoudinimcp._sdk import Context

# Internal
from fxhoudinimcp.server import _get_bridge, mcp


@mcp.tool()
async def get_network_overview(
    ctx: Context,
    path: str = "/obj",
    depth: int = 2,
    detail: str = "compact",
) -> dict:
    """Get a compact overview of a network.

    The default answer is the graph as text, one line per node with its
    inputs in connector order and its flags when set:
    `river_sample_height  attribwrangle  <- river_resample, terrain_noise`
    `OUT  null  <- merge1  [D][R]` ([B] bypassed, [!] errors), plus
    `display_node` and `error_nodes`. Read it before any non-trivial edit.

    Keep `depth` low (1–2). Larger values on complex scenes return thousands
    of nodes and can overflow the context window.

    Args:
        path: Network path.
        depth: Recursion depth (default 2, keep ≤ 3).
        detail: "compact" (default, text graph) or "full" (adds node records
            with positions and flags, and every wire with its output/input
            indices and connector names).
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "context.get_network_overview",
        {
            "path": path,
            "depth": depth,
            "detail": detail,
        },
    )


@mcp.tool()
async def get_cook_chain(ctx: Context, node_path: str) -> dict:
    """Trace the cook dependency chain for a node.

    Args:
        node_path: Node path.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "context.get_cook_chain",
        {
            "node_path": node_path,
        },
    )


@mcp.tool()
async def explain_node(ctx: Context, node_path: str) -> dict:
    """Explain a node in human-readable form.

    Args:
        node_path: Node path.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "context.explain_node",
        {
            "node_path": node_path,
        },
    )


@mcp.tool()
async def get_selection(ctx: Context) -> dict:
    """Get the current node selection."""
    bridge = _get_bridge(ctx)
    return await bridge.execute("context.get_selection")


@mcp.tool()
async def set_selection(
    ctx: Context,
    node_paths: list[str] | None = None,
) -> dict:
    """Set the node selection.

    Args:
        node_paths: Node paths to select.
    """
    bridge = _get_bridge(ctx)
    params: dict = {}
    if node_paths is not None:
        params["node_paths"] = node_paths
    return await bridge.execute("context.set_selection", params)


@mcp.tool()
async def get_scene_summary(ctx: Context) -> dict:
    """Get a high-level summary of the scene."""
    bridge = _get_bridge(ctx)
    return await bridge.execute("context.get_scene_summary")


@mcp.tool()
async def compare_snapshots(
    ctx: Context,
    action: str = "take",
    snapshot_name: str = "default",
    node_path: str | None = None,
) -> dict:
    """Take, compare or restore scene state snapshots.

    To measure at other settings on a scene the user is also editing:
    take(snapshot_name, node_path=<node>), change parms, measure, then
    restore(snapshot_name) puts every parm of that node and its children
    back, expressions included. Animated parms are listed in `not_restored`.

    Args:
        action: "take", "compare" or "restore".
        snapshot_name: Snapshot name.
        node_path: Scope to this node and its children; needed for "restore".
    """
    bridge = _get_bridge(ctx)
    params: dict = {"action": action, "snapshot_name": snapshot_name}
    if node_path is not None:
        params["node_path"] = node_path
    return await bridge.execute("context.compare_snapshots", params)


@mcp.tool()
async def get_node_errors_detailed(
    ctx: Context,
    node_path: str | None = None,
    root_path: str = "/",
    include_locked_assets: bool = False,
    include_warnings: bool = True,
) -> dict:
    """Get detailed error analysis for nodes.

    A scan skips nodes inside locked HDAs (stock assets' internal warnings
    drown the real errors) and reports `skipped_inside_locked_assets`. A
    message repeated on one node is listed once with `(xN)`; a node failing
    only because its input fails is marked `blocked_by` (see `root_errors`).

    Args:
        node_path: Node to analyze, or scan from root_path if omitted.
        root_path: Root path to scan.
        include_locked_assets: Also scan inside locked HDAs.
        include_warnings: Report warnings as well as errors.
    """
    bridge = _get_bridge(ctx)
    params: dict = {"root_path": root_path}
    if node_path is not None:
        params["node_path"] = node_path
    if include_locked_assets:
        params["include_locked_assets"] = True
    if not include_warnings:
        params["include_warnings"] = False
    return await bridge.execute("context.get_node_errors_detailed", params)
