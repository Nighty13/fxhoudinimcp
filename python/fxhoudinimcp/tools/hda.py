"""MCP tool wrappers for Houdini Digital Asset (HDA) operations.

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
async def list_installed_hdas(
    ctx: Context,
    filter: str | None = None,
    limit: int = 100,
) -> dict:
    """List installed HDA definitions, grouped by library file.

    A stock install loads thousands; pass a filter (namespace, name or path
    fragment). `truncated` says when `limit` cut the list.

    Args:
        ctx: MCP context.
        filter: Substring filter for type names or file paths.
        limit: Maximum definitions returned.
    """
    bridge = _get_bridge(ctx)
    params: dict = {"limit": limit}
    if filter is not None:
        params["filter"] = filter
    return await bridge.execute("hda.list_installed_hdas", params)


@mcp.tool()
async def get_hda_info(
    ctx: Context,
    node_path: str | None = None,
    hda_file: str | None = None,
    type_name: str | None = None,
) -> dict:
    """Get detailed information about an HDA definition.

    Args:
        ctx: MCP context.
        node_path: Node path.
        hda_file: HDA file path; variables ($JOB) are expanded.
        type_name: HDA type name.
    """
    bridge = _get_bridge(ctx)
    params: dict = {}
    if node_path is not None:
        params["node_path"] = node_path
    if hda_file is not None:
        params["hda_file"] = hda_file
    if type_name is not None:
        params["type_name"] = type_name
    return await bridge.execute("hda.get_hda_info", params)


@mcp.tool()
async def install_hda(
    ctx: Context,
    file_path: str,
    force: bool = False,
) -> dict:
    """Install an HDA file into the current session.

    Args:
        ctx: MCP context.
        file_path: HDA file path; variables ($JOB) are expanded.
        force: Force reinstall even if already loaded.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "hda.install_hda",
        {
            "file_path": file_path,
            "force": force,
        },
    )


@mcp.tool()
async def uninstall_hda(ctx: Context, file_path: str) -> dict:
    """Uninstall an HDA file from the current session.

    Args:
        ctx: MCP context.
        file_path: HDA file path; variables ($JOB) are expanded.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute("hda.uninstall_hda", {"file_path": file_path})


@mcp.tool()
async def reload_hda(ctx: Context, file_path: str) -> dict:
    """Reload an HDA file from disk.

    Args:
        ctx: MCP context.
        file_path: HDA file path; variables ($JOB) are expanded.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute("hda.reload_hda", {"file_path": file_path})


@mcp.tool()
async def create_hda(
    ctx: Context,
    node_path: str,
    hda_file: str,
    type_name: str,
    label: str,
    version: str = "1.0",
    promote_from: str | None = None,
    inputs: list[dict] | None = None,
) -> dict:
    """Create a new HDA from an existing subnet node.

    A Copernicus subnet's connectors are signed from its own Input/Output
    parms (inputtype#/outputtype#, labels): a cable output stays Cable.
    `connectors` reads them back.

    promote_from="CONTROLS" (a node inside the subnet, usually a null) copies
    its spare parms onto the asset interface with their CURRENT values as
    defaults, then sets each of its parms to ch("../<name>") (chs for
    strings; one ".." per level), unlocking the asset if needed, and saves
    the contents. `promoted_from` lists the parameters, linked parms, skipped
    ones and locked_before/after.

    Args:
        ctx: MCP context.
        node_path: Subnet node path.
        hda_file: Destination HDA file path; variables ($JOB) are expanded.
        type_name: Operator type name.
        label: Human-readable label.
        version: Version string.
        promote_from: Node inside the subnet whose spare parms become the interface.
        inputs: [{"label": ..., "optional": bool}] per input, in order: labels,
            max inputs = len, min inputs = the leading required ones.
    """
    bridge = _get_bridge(ctx)
    payload: dict[str, Any] = {
        "node_path": node_path,
        "hda_file": hda_file,
        "type_name": type_name,
        "label": label,
        "version": version,
    }
    if promote_from is not None:
        payload["promote_from"] = promote_from
    if inputs is not None:
        payload["inputs"] = inputs
    return await bridge.execute("hda.create_hda", payload)


@mcp.tool()
async def update_hda(
    ctx: Context,
    node_path: str,
    unlock: bool = False,
    lock: bool = False,
    save: bool = True,
    promote_current_values: bool = False,
) -> dict:
    """Save the current node contents back to its HDA definition.

    The definition's library file is written by this call: `saved_to_disk`
    and `library_file_mtime_iso` / `library_file_mtime_epoch_ms` (stat after
    the save) say so, so no separate save is needed. An embedded definition
    lives in the hip file and is kept when the scene is saved.

    `locked_before` / `locked_after` report the instance's lock. unlock=True
    allows editing of contents and leaves it unlocked (with save=False it is
    just the unlock door); lock=True matches the definition after saving.

    `instance_values_differ` lists parms whose value on this instance is not
    the definition default: Unreal / Houdini Engine cooks the DEFAULTS.
    promote_current_values=True writes those values as the new defaults.

    Args:
        ctx: MCP context.
        node_path: Node path.
        unlock: Allow editing of contents first.
        lock: Lock the instance to its definition after saving.
        save: Write the contents to the definition.
        promote_current_values: Make the differing instance values the defaults.
    """
    bridge = _get_bridge(ctx)
    payload: dict[str, Any] = {"node_path": node_path}
    if unlock:
        payload["unlock"] = True
    if lock:
        payload["lock"] = True
    if not save:
        payload["save"] = False
    if promote_current_values:
        payload["promote_current_values"] = True
    return await bridge.execute("hda.update_hda", payload)


@mcp.tool()
async def get_hda_sections(ctx: Context, node_path: str) -> dict:
    """List all sections in an HDA definition.

    Args:
        ctx: MCP context.
        node_path: Node path.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute("hda.get_hda_sections", {"node_path": node_path})


@mcp.tool()
async def get_hda_section_content(
    ctx: Context,
    node_path: str,
    section_name: str,
) -> dict:
    """Read the content of a specific section in an HDA definition.

    Args:
        ctx: MCP context.
        node_path: Node path.
        section_name: Section name.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "hda.get_hda_section_content",
        {
            "node_path": node_path,
            "section_name": section_name,
        },
    )


@mcp.tool()
async def set_hda_section_content(
    ctx: Context,
    node_path: str,
    section_name: str,
    content: str,
    language: str | None = None,
) -> dict:
    """Write content to a specific section in an HDA definition.

    Event scripts (OnCreated, OnLoaded, OnUpdated, OnDeleted, OnInputChanged,
    PreFirstCreate, PostLastDelete, ...) are HScript unless marked Python:
    pass language="python" (or "hscript"); left out, code using kwargs,
    hou. or import is marked Python. `script_language` reads it back.

    Args:
        ctx: MCP context.
        node_path: Node path.
        section_name: Section name.
        content: Section content.
        language: For an event script, "python" or "hscript".
    """
    bridge = _get_bridge(ctx)
    payload: dict[str, Any] = {
        "node_path": node_path,
        "section_name": section_name,
        "content": content,
    }
    if language is not None:
        payload["language"] = language
    return await bridge.execute("hda.set_hda_section_content", payload)


@mcp.tool()
async def list_hda_versions(ctx: Context, node_path: str) -> dict:
    """Every installed definition of an HDA node's type: version, file, which is current.

    Args:
        ctx: MCP context.
        node_path: An HDA instance.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute("hda.list_hda_versions", {"node_path": node_path})


@mcp.tool()
async def set_hda_interface(
    ctx: Context,
    node_path: str,
    parameters: list[dict] | None = None,
    replace: bool = False,
    dry_run: bool = False,
    default_from_value: bool = False,
    inputs: list[dict] | None = None,
    verbose: bool = False,
) -> dict:
    """Author an HDA's Type Properties interface in one call.

    Use this for the asset's TYPE interface — tab folders, strict ranges,
    ordered menus, Hide/Disable When. `create_spare_parameter` is a different
    thing: it adds parameters to one node instance and never reaches the type.

    Each entry of `parameters` is a dict:
        name, label, type (int|float|vector|color|string|file|oppath|toggle|
        menu|button|separator|label|folder|ramp_float|ramp_color),
        default, min, max, min_strict, max_strict, components,
        menu_items ([value, label] pairs or plain strings),
        folder_type (tabs|simple|collapsible|radio) + children for folders,
        hide_when / disable_when (Houdini conditionals), help,
        callback (+ callback_language, python by default) for a button,
        column_labels for a label.
        ramp_float / ramp_color types: default is the keys,
        [{"pos": 0, "value": 0 (or [r, g, b]), "interp": "linear"}, ...].
        look (logarithmic|angle|vector|color|huecircle) for numbers,
        label_hidden (or is_label_hidden), hidden, join_with_next, tags; a ramp
        refuses label_hidden and join_with_next, which Houdini does not store.
        from_parm ("/path/node/parm"): copy that parameter's template under
        `name` (label, help, conditionals still apply; the source's own
        conditionals and callback are dropped, and type/default/min/max/
        components/menu_items are refused). A stock node's ramp comes with
        its default keys. default_from_value: True takes the source's
        CURRENT value as the default instead of its template default.
        Any other key is refused with a did-you-mean (it used to be dropped).

    Example — a Controls tab whose Bevel disappears for a single stud:
        [{"name": "controls", "label": "Controls", "type": "folder",
          "children": [
            {"name": "stud_count", "type": "int", "default": 4,
             "min": 1, "max": 8, "min_strict": True, "max_strict": True},
            {"name": "bevel", "type": "float", "default": 0.02,
             "min": 0.0, "max": 0.1, "hide_when": "{ stud_count == 1 }"},
            {"name": "material", "type": "menu",
             "menu_items": [["plastic", "Plastic"], ["metal", "Metal"]]}]}]

    It is edit_hda_interface with one insert per entry: names already in the
    interface are refused before anything is written, and the reply is read
    back off the definition — `ops[].stored` (a compact tree, each parm once:
    name, type, label, default; verbose=True for full templates),
    `renamed_by_houdini` (a tab
    folder joins the existing tab set's naming series), `not_found_after_write`,
    `help_not_stored`, `instance_parms_missing` and `instance_expression_errors`.

    Args:
        ctx: MCP context.
        node_path: An instance of the HDA whose definition is edited.
        parameters: Interface spec (see above). create_spare_parameters'
            spelling (parm_name, parm_type, default_value) is accepted too.
            May be empty when only `inputs` is given.
        replace: Start from an empty interface. Built-in parameters of the node
            type cannot be removed: Houdini puts them back
            (`reinstated_by_houdini`).
        dry_run: Validate and report the plan without writing.
        default_from_value: Every from_parm spec takes its source's current value.
        inputs: [{"label": ..., "optional": bool}] per input, in order: input
            labels, max inputs = len, min inputs = the leading required ones.
        verbose: Full template readback in `stored`.
    """
    bridge = _get_bridge(ctx)
    payload: dict = {
        "node_path": node_path,
        "parameters": parameters or [],
        "replace": replace,
    }
    if dry_run:
        payload["dry_run"] = True
    if default_from_value:
        payload["default_from_value"] = True
    if inputs is not None:
        payload["inputs"] = inputs
    if verbose:
        payload["verbose"] = True
    return await bridge.execute("hda.set_hda_interface", payload)


@mcp.tool()
async def edit_hda_interface(
    ctx: Context,
    node_path: str,
    ops: list[dict],
    dry_run: bool = False,
    default_from_value: bool = False,
    verbose: bool = False,
) -> dict:
    """Edit an HDA's EXISTING Type Properties interface in one atomic call:
    insert at a position, remove, hide/show, replace, modify, move.

    set_hda_interface only appends. Every op here works on the definition's
    parameter group; all ops are applied to a copy, the result is checked
    for component-name collisions, and it is written once — a failing op
    changes nothing. Read the interface first with get_parm_template_tree.

    Ops (dicts, applied in order):
        {"op": "insert", "spec": {...}, "after": name | "before": name |
            "in_folder": label or [labels]}  — omit the position to append.
            spec is a set_hda_interface spec, plus types button (with
            "callback", Python by default), separator, label, vector, color,
            file, oppath; and fields naming_scheme (base1|xyzw|rgba|minmax|
            startend|uvw), default_expression (+ default_expression_language),
            hidden, join_with_next, callback, tags; folder_type "multiparm"
            for a multiparm block (children named "item#").
        {"op": "remove", "name": name_or_folder_label}
        {"op": "hide" | "show", "name": ...}
        {"op": "replace", "name": ..., "spec": {...}}
        {"op": "modify", "name": ..., <label | help | default |
            default_expression | default_expression_language | min | max |
            min_strict | max_strict | hide_when | disable_when ("" clears) |
            hidden | join_with_next | menu_items | callback | naming_scheme |
            new_name | tags>}
            ("rename", "set_conditional", "set_default" are aliases)
        {"op": "move", "name": ..., "after" | "before" | "in_folder": ...}

    Names are template names (`t`, not `tx`; `stud_count`); folders are
    addressed by label ("Controls"). Built-in parameters of the node type
    (an Object's Transform) cannot be removed — Houdini re-adds them at the
    top level and the reply says so in `reinstated_by_houdini`; hide them.

    `default_expression_language` is "hscript" (Houdini's default) or
    "python"; given alone in a modify, it changes the language of the
    expression already there. `stored` reads both back, and
    `instance_expression_errors` names a default expression that does not
    evaluate on the instance. `stored` carries each template's help, a
    button's `callback` and a label's `column_labels`, and `help_not_stored`
    names one whose help the definition did not keep.

    Args:
        ctx: MCP context.
        node_path: An instance of the HDA whose definition is edited.
        ops: Operations, in order.
        dry_run: Validate and report the plan without writing.
        default_from_value: from_parm specs take the source's current value.
        verbose: Full template readback in `stored` (default: compact tree).
    """
    bridge = _get_bridge(ctx)
    payload: dict = {"node_path": node_path, "ops": ops, "dry_run": dry_run}
    if default_from_value:
        payload["default_from_value"] = True
    if verbose:
        payload["verbose"] = True
    return await bridge.execute("hda.edit_hda_interface", payload)
