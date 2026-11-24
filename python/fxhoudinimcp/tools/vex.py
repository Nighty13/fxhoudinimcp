"""MCP tool wrappers for Houdini VEX operations.

Each tool delegates to the corresponding handler running inside Houdini
via the HTTP bridge.
"""

from __future__ import annotations

# Built-in
from typing import Any

# Third-party
from fxhoudinimcp._sdk import Context

# Internal
from fxhoudinimcp.server import _get_bridge, mcp


@mcp.tool()
async def create_wrangle(
    ctx: Context,
    parent_path: str,
    vex_code: str,
    justification: str,
    run_over: str = "Points",
    name: str | None = None,
    inputs: list[str] | None = None,
) -> dict:
    """Create an Attribute Wrangle node with VEX code.

    LAST RESORT. VEX is for attribute math that no node expresses —
    NEVER for modeling, scattering, copying, deforming, grouping, or
    randomizing, which all have dedicated nodes. Building geometry in a
    wrangle when a native node exists is a failure, not a shortcut.

    The justification parameter is mandatory: state which
    list_node_types searches you ran and why none of the results can do
    this. If you cannot write that sentence honestly, you have not
    checked — check first.

    Args:
        parent_path: Parent SOP network path.
        vex_code: VEX snippet to set.
        justification: Which native nodes you checked (the actual
            list_node_types filters used) and why none can express this
            logic.
        run_over: Element to run over ("Points", "Vertices", "Primitives", "Detail", "Numbers").
        name: Node name.
        inputs: Nodes to wire into inputs 0, 1, ... (paths or sibling
            names), wired before the code is checked.
    """
    bridge = _get_bridge(ctx)
    params: dict = {
        "parent_path": parent_path,
        "vex_code": vex_code,
        "run_over": run_over,
    }
    if name is not None:
        params["name"] = name
    if inputs:
        params["inputs"] = inputs
    result = await bridge.execute("vex.create_wrangle", params)
    if isinstance(result, dict):
        result["justification"] = justification
    return result


@mcp.tool()
async def set_wrangle_code(
    ctx: Context,
    node_path: str,
    vex_code: str | None = None,
    edits: list[dict[str, str]] | None = None,
) -> dict:
    """Set VEX code on an existing Attribute Wrangle node, whole or in part.

    For one block of a long snippet, pass `edits` instead of `vex_code`:
    [{"old": "<exact text now in the snippet>", "new": "<its replacement>"}],
    applied in order; each `old` must occur exactly once (add a neighbouring
    line to make it unique), and one bad edit writes nothing. The reply then
    gives `edits_applied` (line range and new text of each).

    Neither form echoes the code back: the reply carries `line_count`,
    `char_count`, `vex_valid` / `vex_errors` and, for known VEX traps, `hints`.
    validate_vex(code=...) checks a snippet before writing it.

    Args:
        node_path: Path to the wrangle node.
        vex_code: VEX snippet to set.
        edits: Exact replacements in the current snippet, instead of vex_code.
    """
    bridge = _get_bridge(ctx)
    payload: dict[str, Any] = {"node_path": node_path}
    if vex_code is not None:
        payload["vex_code"] = vex_code
    if edits is not None:
        payload["edits"] = edits
    return await bridge.execute("vex.set_wrangle_code", payload)


@mcp.tool()
async def get_wrangle_code(ctx: Context, node_path: str) -> dict:
    """Read the VEX code from an Attribute Wrangle node.

    Args:
        node_path: Path to the wrangle node.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute("vex.get_wrangle_code", {"node_path": node_path})


@mcp.tool()
async def create_vex_expression(
    ctx: Context,
    node_path: str,
    parm_name: str,
    vex_code: str,
) -> dict:
    """Set an HScript expression on a parameter, evaluated before it is kept.

    Parameters cannot run VEX: attribute @ syntax is refused with a pointer to
    create_wrangle. An expression that does not evaluate is not kept.

    Args:
        node_path: Path to the node.
        parm_name: Parameter name.
        vex_code: Expression code, in HScript syntax.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "vex.create_vex_expression",
        {
            "node_path": node_path,
            "parm_name": parm_name,
            "vex_code": vex_code,
        },
    )


@mcp.tool()
async def validate_vex(
    ctx: Context,
    node_path: str | None = None,
    code: str | None = None,
    run_over: str | None = None,
    context: str | None = None,
) -> dict:
    """Validate VEX: cook a wrangle, or compile a snippet before it lands.

    With `code`, the snippet compiles in a scratch wrangle that is destroyed
    afterwards: next to `node_path` (same inputs and run-over) when given,
    else in a scratch network. The reply never echoes the code. `hints` names
    known traps in plain English: point()/prim()/detail() with no type to
    return (in a comparison, append(), set*attrib()), foreach over an array
    literal (the cryptic "__impl_foreach_end"), point(0, ...) reading input
    points that addpoint() in the same snippet cannot add to, range().

    Args:
        node_path: The wrangle to validate; with code, the wrangle it is for.
        code: VEX snippet to compile instead of the node's own.
        run_over: With code: "Points", "Primitives", "Vertices", "Detail", "Numbers".
        context: With code: "sop" (attribwrangle, default) or "volume" (volumewrangle).
    """
    bridge = _get_bridge(ctx)
    payload: dict[str, Any] = {}
    if node_path is not None:
        payload["node_path"] = node_path
    if code is not None:
        payload["code"] = code
    if run_over is not None:
        payload["run_over"] = run_over
    if context is not None:
        payload["context"] = context
    return await bridge.execute("vex.validate_vex", payload)
