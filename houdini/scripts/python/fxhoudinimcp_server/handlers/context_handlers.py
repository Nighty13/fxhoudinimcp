"""Scene-understanding / context handlers for FXHoudini-MCP.

Provides tools for querying network structure, cook chains, node
explanations, selection management, scene summaries, state snapshots,
and detailed error analysis.
"""

from __future__ import annotations

import contextlib

# Built-in
from typing import Any

# Third-party
import hou

# Internal
from fxhoudinimcp_server.dispatcher import register_handler

###### Module-level state

# Snapshot storage for compare_snapshots
_snapshots: dict[str, dict] = {}


###### Helpers


def _get_node(node_path: str) -> hou.Node:
    """Resolve a node path and raise a clear error if it does not exist."""
    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")
    return node


def _node_flags(node: hou.Node) -> dict[str, bool]:
    """Safely read common flags from a node."""
    flags: dict[str, bool] = {}
    with contextlib.suppress(Exception):
        flags["display"] = node.isDisplayFlagSet()
    with contextlib.suppress(Exception):
        flags["render"] = node.isRenderFlagSet()
    with contextlib.suppress(Exception):
        flags["bypass"] = node.isBypassed()
    return flags


def _has_errors(node: hou.Node) -> bool:
    """Return True if the node has any errors."""
    try:
        return bool(node.errors())
    except Exception:
        return False


def _non_default_parms(node: hou.Node) -> dict[str, Any]:
    """Return a dict of parameter names to values for non-default parameters.

    Each component is compared with its own default: comparing ty with the
    tuple default (0, 0, 0) reported 30 of 49 parms as changed on a fresh
    xform, in explain_node and compare_snapshots alike.
    """
    from fxhoudinimcp_server.handlers.node_handlers import _component_default, _is_at_default

    result: dict[str, Any] = {}
    for parm in node.parms():
        try:
            val = parm.eval()
        except Exception:
            continue
        try:
            default = _component_default(parm)
        except Exception:
            default = None
        if not _is_at_default(parm, val, default):
            if isinstance(val, (hou.Vector2, hou.Vector3, hou.Vector4)):
                val = list(val)
            result[parm.name()] = val
    return result


###### context.get_network_overview


_NAMED_INPUT_CATEGORIES = ("Vop", "Cop")


def _name_connectors(wire: dict, source, target, named_inputs: bool) -> None:
    """Add the connector names that tell wires apart.

    The output's name when the source has more than one output (an index alone
    does not say v from P); the input's name where inputs are named by design
    (VOP, Copernicus), not "input1".
    """
    with contextlib.suppress(Exception):
        names = source.outputNames()
        if len(names) > 1 and wire["from_output"] < len(names):
            wire["from_output_name"] = names[wire["from_output"]]
    if named_inputs:
        with contextlib.suppress(Exception):
            names = target.inputNames()
            if wire["to_input"] < len(names):
                wire["to_input_name"] = names[wire["to_input"]]


def _get_network_overview(
    path: str = "/obj",
    depth: int = 2,
    detail: str = "full",
    **_: Any,
) -> dict:
    """Return a compact network overview of nodes and connections.

    Args:
        path: Root network path to inspect.
        depth: How many levels deep to recurse into children.
        detail: "compact" answers the graph as text only (`graph`: one line
            per node, inputs in connector order, flags when set) plus the
            erroring nodes. "full" adds per-node records with positions and
            every wire as an object (~1k tokens per 16 nodes).
    """
    compact = str(detail).lower() == "compact"

    def _build_overview(network: hou.Node, remaining_depth: int) -> dict:
        children = network.children()
        if compact:
            result: dict[str, Any] = {
                "path": network.path(),
                "node_count": len(children),
                "graph": _build_ascii_flow(children),
            }
            for child in children:
                flags = _node_flags(child)
                if flags.get("display"):
                    result["display_node"] = child.name()
                if flags.get("render"):
                    result["render_node"] = child.name()
            erroring = [c.name() for c in children if _has_errors(c)]
            if erroring:
                result["error_nodes"] = erroring
            if remaining_depth > 1:
                nested = {
                    c.name(): _build_overview(c, remaining_depth - 1)
                    for c in children
                    if c.children()
                }
                if nested:
                    result["child_networks"] = nested
            return result

        nodes_info = []
        connections = []
        display_node = None
        render_node = None

        for child in children:
            flags = _node_flags(child)
            info = {
                "name": child.name(),
                "type": child.type().name(),
                "position": list(child.position()),
                "flags": flags,
                "has_errors": _has_errors(child),
            }
            if flags.get("display"):
                display_node = child.name()
            if flags.get("render"):
                render_node = child.name()
            nodes_info.append(info)

        # Build connections as adjacency list. Read off the target's
        # inputConnections(): both indices are the wire's own. Looking the
        # output up in source.outputConnections() matched inputNode() -- the
        # source itself there -- against the target, so it never matched and
        # every wire read from_output 0: a VOP wire from geometryvopglobal's
        # v and one from its P looked the same.
        for child in children:
            named_inputs = child.type().category().name() in _NAMED_INPUT_CATEGORIES
            for conn in sorted(child.inputConnections(), key=lambda c: c.inputIndex()):
                source = conn.inputNode()
                if source is None:
                    continue
                wire: dict[str, Any] = {
                    "from": source.name(),
                    "from_output": conn.outputIndex(),
                    "to": child.name(),
                    "to_input": conn.inputIndex(),
                }
                _name_connectors(wire, source, child, named_inputs)
                connections.append(wire)

        # Build ASCII art representation
        ascii_lines = _build_ascii_flow(children)

        result: dict[str, Any] = {
            "path": network.path(),
            "node_count": len(children),
            "nodes": nodes_info,
            "connections": connections,
            "display_node": display_node,
            "render_node": render_node,
            "ascii_flow": ascii_lines,
        }

        # Recurse into children that are networks
        if remaining_depth > 1:
            child_networks = {}
            for child in children:
                if child.children():
                    child_networks[child.name()] = _build_overview(
                        child,
                        remaining_depth - 1,
                    )
            if child_networks:
                result["child_networks"] = child_networks

        return result

    def _build_ascii_flow(children: tuple) -> str:
        """One line per node, inputs in connector order: the wiring, faithfully.

        `name  type  <- in0, in1  [D][R][B][!]`. The old chain view followed
        one output per node and squashed multi-input nodes into chains: on a
        terrain network it showed a heightfield as the main input of a curve
        wrangle and left the curve branch dangling. Nodes are listed sources
        first (inputs before the nodes that read them); an unconnected input
        slot below the last connected one is omitted, a gap shows as `-`.
        """
        if not children:
            return "(empty network)"
        by_path = {c.path(): c for c in children}
        order, placed = [], set()

        def place(node, stack=()):
            if node.path() in placed or node.path() in stack:
                return
            for inp in node.inputs():
                if inp is not None and inp.path() in by_path:
                    place(inp, stack + (node.path(),))
            placed.add(node.path())
            order.append(node)

        for child in children:
            place(child)
        lines = []
        for node in order:
            ins = [i.name() if i is not None else "-" for i in node.inputs()]
            while ins and ins[-1] == "-":
                ins.pop()
            flags = _node_flags(node)
            tags = "".join(
                t
                for k, t in (("display", "[D]"), ("render", "[R]"), ("bypass", "[B]"))
                if flags.get(k)
            )
            with contextlib.suppress(Exception):
                if node.errors():
                    tags += "[!]"
            line = f"{node.name()}  {node.type().name()}"
            if ins:
                line += "  <- " + ", ".join(ins)
            if tags:
                line += "  " + tags
            lines.append(line)
        return "\n".join(lines)

    root = _get_node(path)
    return _build_overview(root, depth)


###### context.get_cook_chain


def _get_cook_chain(node_path: str, **_: Any) -> dict:
    """Trace the cook dependency chain from sources to the target node.

    Walks node.inputs() recursively back to source nodes and returns
    an ordered list from source to target.

    Args:
        node_path: Absolute path to the target node.
    """
    target = _get_node(node_path)

    visited: set[str] = set()
    chain: list[dict] = []

    def _walk(node: hou.Node) -> None:
        if node.path() in visited:
            return
        visited.add(node.path())
        # Walk inputs first (sources come before dependents)
        for inp in node.inputs():
            if inp is not None:
                _walk(inp)
        # Cook time
        try:
            cook_time = node.cookTime()
        except Exception:
            cook_time = None
        chain.append(
            {
                "path": node.path(),
                "type": node.type().name(),
                "cook_time": cook_time,
                "has_errors": _has_errors(node),
            }
        )

    _walk(target)

    return {
        "target": node_path,
        "chain_length": len(chain),
        "chain": chain,
    }


###### context.explain_node


def _explain_node(node_path: str, **_: Any) -> dict:
    """Return a human-readable explanation of a node.

    Includes type description, non-default parameters, connections,
    current state, and a summary text field.

    Args:
        node_path: Absolute path to the node.
    """
    node = _get_node(node_path)
    node_type = node.type()

    # Errors first: evaluating parms (below) then reading errors() in the
    # same tick pins a transient expression error, see get_node_info.
    errors = []
    warnings = []
    with contextlib.suppress(Exception):
        errors = list(node.errors())
    with contextlib.suppress(Exception):
        warnings = list(node.warnings())

    # Type description
    type_desc = node_type.description()
    type_name = node_type.name()
    category = node_type.category().name()

    # Non-default parameters
    changed_parms = _non_default_parms(node)

    # Input connections
    inputs = []
    for i, inp in enumerate(node.inputs()):
        if inp is not None:
            inputs.append(
                {
                    "index": i,
                    "path": inp.path(),
                    "name": inp.name(),
                    "type": inp.type().name(),
                }
            )

    # Output connections
    outputs = []
    for out in node.outputs():
        outputs.append(
            {
                "path": out.path(),
                "name": out.name(),
                "type": out.type().name(),
            }
        )

    # Current state
    try:
        cook_time = node.cookTime()
    except Exception:
        cook_time = None

    flags = _node_flags(node)

    state = {
        "errors": errors,
        "warnings": warnings,
        "cook_time": cook_time,
        "flags": flags,
    }

    # Build summary text
    summary_parts = [f"{node.name()} is a {type_desc} ({category}/{type_name})."]
    if changed_parms:
        parm_strs = [f"{k}={v!r}" for k, v in list(changed_parms.items())[:10]]
        summary_parts.append(f"Modified parameters: {', '.join(parm_strs)}.")
    if inputs:
        input_strs = [f"{inp['name']} ({inp['type']})" for inp in inputs]
        summary_parts.append(f"Inputs: {', '.join(input_strs)}.")
    else:
        summary_parts.append("No inputs connected.")
    if outputs:
        output_strs = [f"{out['name']} ({out['type']})" for out in outputs]
        summary_parts.append(f"Outputs: {', '.join(output_strs)}.")
    else:
        summary_parts.append("No outputs connected.")
    if errors:
        summary_parts.append(f"ERRORS: {'; '.join(errors)}")
    if warnings:
        summary_parts.append(f"Warnings: {'; '.join(warnings)}")
    if flags.get("display"):
        summary_parts.append("Display flag is ON.")
    if flags.get("render"):
        summary_parts.append("Render flag is ON.")
    if flags.get("bypass"):
        summary_parts.append("Node is BYPASSED.")

    return {
        "node_path": node.path(),
        "type_description": type_desc,
        "type_name": type_name,
        "category": category,
        "changed_parameters": changed_parms,
        "inputs": inputs,
        "outputs": outputs,
        "state": state,
        "summary": " ".join(summary_parts),
    }


###### context.get_selection


def _get_selection(**_: Any) -> dict:
    """Get the current selection in Houdini.

    Returns selected nodes and, if available, geometry component
    selection (points, prims, edges).
    """
    # Node selection
    selected_nodes = hou.selectedNodes()
    nodes_info = []
    for node in selected_nodes:
        nodes_info.append(
            {
                "path": node.path(),
                "type": node.type().name(),
                "name": node.name(),
            }
        )

    # Geometry selection
    geo_info: dict[str, Any] | None = None
    try:
        # Attempt to get geometry-level selection from the current viewer
        viewer = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.SceneViewer)
        if viewer is not None:
            sel = viewer.currentGeometrySelection()
            if sel is not None:
                sel_type = sel.geometryType()
                selections = sel.selections()
                total_count = 0
                sel_node_path = None
                if selections:
                    # Get the node from the first selected node context
                    sel_nodes = sel.nodes()
                    if sel_nodes:
                        sel_node_path = sel_nodes[0].path()
                        geo = sel_nodes[0].geometry()
                        if geo is not None:
                            for s in selections:
                                total_count += (
                                    len(
                                        s.selectionString(
                                            geo,
                                        ).split()
                                    )
                                    if s.selectionString(geo)
                                    else 0
                                )
                type_name = "unknown"
                if sel_type == hou.geometryType.Points:
                    type_name = "points"
                elif sel_type == hou.geometryType.Primitives:
                    type_name = "primitives"
                elif sel_type == hou.geometryType.Edges:
                    type_name = "edges"
                elif sel_type == hou.geometryType.Vertices:
                    type_name = "vertices"
                geo_info = {
                    "type": type_name,
                    "count": total_count,
                    "node_path": sel_node_path,
                }
    except Exception:
        # Geometry selection may not be available in all contexts
        pass

    return {
        "nodes": nodes_info,
        "geometry": geo_info,
    }


###### context.set_selection


def _set_selection(node_paths: list | None = None, **_: Any) -> dict:
    """Select nodes by their paths.

    Clears the existing selection first, then selects the specified nodes.

    Args:
        node_paths: List of absolute node paths to select.
    """
    if node_paths is None:
        node_paths = []

    hou.clearAllSelected()

    selected_count = 0
    missing: list[str] = []
    for path in node_paths:
        node = hou.node(path)
        if node is not None:
            node.setSelected(True)
            selected_count += 1
        else:
            missing.append(path)

    # Asking for three nodes and selecting two is not success. This reported
    # {"success": True, "selected_count": 0, "requested_count": 1} for a path that
    # does not exist -- the counts disagreed and the verdict ignored them, so a
    # caller reading "success" went on to operate on an empty selection.
    return {
        "success": not missing,
        "missing": missing,
        "message": (
            f"Selected {selected_count} of {len(node_paths)} node(s)."
            if not missing
            else f"Could not select {len(missing)} of {len(node_paths)} node(s), "
            f"because these paths do not exist: {missing[:6]}"
        ),
        "selected_count": selected_count,
        "requested_count": len(node_paths),
    }


###### context.get_scene_summary


def _get_scene_summary(**_: Any) -> dict:
    """Return a high-level overview of the entire Houdini scene.

    Includes /obj, /out, /stage children, total node count,
    error summary, and timeline info.
    """
    # /obj children
    obj_children = []
    obj_node = hou.node("/obj")
    if obj_node is not None:
        for child in obj_node.children():
            info: dict[str, Any] = {
                "name": child.name(),
                "type": child.type().name(),
            }
            # Count SOPs inside and find displayed SOP
            sub_children = child.children()
            sop_count = 0
            displayed_sop = None
            for sub in sub_children:
                if sub.type().category().name() == "Sop":
                    sop_count += 1
                    try:
                        if sub.isDisplayFlagSet():
                            displayed_sop = sub.name()
                    except Exception:
                        pass
            info["sop_count"] = sop_count
            info["displayed_sop"] = displayed_sop
            obj_children.append(info)

    # /out children (render nodes)
    out_children = []
    out_node = hou.node("/out")
    if out_node is not None:
        for child in out_node.children():
            info = {
                "name": child.name(),
                "type": child.type().name(),
            }
            # Try to get output path from common parameter names
            output_path = None
            for parm_name in ("sopoutput", "vm_picture", "picture", "lopoutput", "outputimage"):
                parm = child.parm(parm_name)
                if parm is not None:
                    try:
                        output_path = parm.eval()
                        break
                    except Exception:
                        pass
            info["output_path"] = output_path
            out_children.append(info)

    # /stage children (LOP networks)
    stage_children = []
    stage_node = hou.node("/stage")
    if stage_node is not None:
        for child in stage_node.children():
            stage_children.append(
                {
                    "name": child.name(),
                    "type": child.type().name(),
                }
            )

    # Total node count and error scan
    total_nodes = 0
    error_count = 0
    root = hou.node("/")
    if root is not None:
        all_nodes = root.allSubChildren()
        total_nodes = len(all_nodes)
        for node in all_nodes:
            if _has_errors(node):
                error_count += 1

    # Timeline info
    current_frame = hou.frame()
    try:
        frame_range = list(hou.playbar.playbackRange())
    except Exception:
        frame_range = [1, 240]
    fps = hou.fps()

    return {
        "obj_children": obj_children,
        "out_children": out_children,
        "stage_children": stage_children,
        "total_nodes": total_nodes,
        "error_count": error_count,
        "current_frame": current_frame,
        "frame_range": frame_range,
        "fps": fps,
    }


###### context.compare_snapshots


# Raw parameter state per snapshot, for action="restore": {name: {node path:
# {parm name: state}}}. Only taken for a scoped snapshot (node_path).
_raw_snapshots: dict[str, dict[str, dict[str, Any]]] = {}


def _raw_parm_state(parm: Any) -> dict[str, Any]:
    """What restoring *parm* needs: its expression, or its literal value."""
    keyframes = []
    with contextlib.suppress(Exception):
        keyframes = list(parm.keyframes())
    if len(keyframes) > 1:
        return {"animated": len(keyframes)}
    with contextlib.suppress(Exception):
        expression = parm.expression()
        language = parm.expressionLanguage()
        return {"expr": expression, "language": language}
    with contextlib.suppress(Exception):
        if parm.parmTemplate().type() == hou.parmTemplateType.String:
            return {"value": parm.unexpandedString()}
    return {"value": parm.eval()}


def _scope_nodes(node_path: str) -> list:
    node = _get_node(node_path)
    found = [node]
    with contextlib.suppress(Exception):
        found += [n for n in node.allSubChildren() if not n.isInsideLockedHDA()]
    return found


def _is_count_parm(parm: Any) -> bool:
    with contextlib.suppress(Exception):
        return "Multiparm" in parm.parmTemplate().folderType().name()
    return False


def _capture_raw(node_path: str) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    for node in _scope_nodes(node_path):
        parms: dict[str, Any] = {}
        for parm in node.parms():
            with contextlib.suppress(Exception):
                parms[parm.name()] = _raw_parm_state(parm)
        state[node.path()] = parms
    return state


def _restore_raw(raw: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Write every captured parm back; multiparm counts first."""
    restored = 0
    skipped: list[str] = []
    missing_nodes: list[str] = []
    for path, parms in raw.items():
        node = hou.node(path)
        if node is None:
            missing_nodes.append(path)
            continue
        names = sorted(parms, key=lambda n: (not _is_count_parm(node.parm(n)), n))
        for name in names:
            state = parms[name]
            parm = node.parm(name)
            if parm is None:
                continue
            label = f"{path}/{name}"
            if "animated" in state:
                skipped.append(f"{label} (animated)")
                continue
            try:
                if parm.isLocked():
                    skipped.append(f"{label} (locked)")
                    continue
                current = _raw_parm_state(parm)
                if current == state:
                    continue
                if "expr" in state:
                    parm.setExpression(state["expr"], state["language"], replace_expression=True)
                else:
                    if "expr" in current:
                        parm.deleteAllKeyframes()
                    parm.set(state["value"])
                restored += 1
            except Exception as exc:  # noqa: BLE001 - report and keep restoring the rest
                skipped.append(f"{label} ({exc})")
    result: dict[str, Any] = {"parms_restored": restored}
    if skipped:
        result["not_restored"] = skipped[:100]
    if missing_nodes:
        result["missing_nodes"] = missing_nodes
    return result


def _compare_snapshots(
    action: str = "take",
    snapshot_name: str = "default",
    node_path: str | None = None,
    **_: Any,
) -> dict:
    """Take, compare or restore scene snapshots.

    Args:
        action: "take" to store current state, "compare" to diff against
            stored, "restore" to write a scoped snapshot's parameters back.
        snapshot_name: Name of the snapshot to take or compare against.
        node_path: Scope the snapshot to this node and its children. A scoped
            snapshot also records each parameter's raw state (expression or
            literal), which "restore" writes back. Animated parms are
            reported, not restored.
    """
    global _snapshots

    _SNAPSHOT_NODE_LIMIT = 1000

    if action == "restore":
        raw = _raw_snapshots.get(snapshot_name)
        if raw is None:
            raise ValueError(
                f"No restorable snapshot named '{snapshot_name}'. Take one with "
                f"action='take', node_path=<node> first (restorable: {sorted(_raw_snapshots)})."
            )
        return {
            "success": True,
            "action": "restore",
            "snapshot_name": snapshot_name,
            **_restore_raw(raw),
        }

    def _capture_state() -> dict[str, dict]:
        """Capture current scene state as a lightweight snapshot."""
        state: dict[str, dict] = {}
        if node_path:
            nodes = _scope_nodes(node_path)
        else:
            root = hou.node("/")
            if root is None:
                return state
            nodes = root.allSubChildren()
        for node in nodes:
            parms = _non_default_parms(node)
            state[node.path()] = {
                "type": node.type().name(),
                "params": parms,
            }
            if len(state) >= _SNAPSHOT_NODE_LIMIT:
                break
        return state

    if action == "take":
        snapshot = _capture_state()
        _snapshots[snapshot_name] = snapshot
        reply: dict[str, Any] = {
            "success": True,
            "action": "take",
            "snapshot_name": snapshot_name,
            "node_count": len(snapshot),
        }
        if node_path:
            _raw_snapshots[snapshot_name] = _capture_raw(node_path)
            reply["restorable"] = True
        else:
            _raw_snapshots.pop(snapshot_name, None)
        return reply

    elif action == "compare":
        stored = _snapshots.get(snapshot_name)
        if stored is None:
            raise ValueError(
                f"No snapshot named '{snapshot_name}' found. "
                f"Available snapshots: {list(_snapshots.keys())}"
            )

        current = _capture_state()

        stored_paths = set(stored.keys())
        current_paths = set(current.keys())

        nodes_added = sorted(current_paths - stored_paths)
        nodes_removed = sorted(stored_paths - current_paths)

        # Find parameter changes on nodes that exist in both
        params_changed: list[dict] = []
        for path in sorted(stored_paths & current_paths):
            old_params = stored[path]["params"]
            new_params = current[path]["params"]
            if old_params != new_params:
                # Determine what changed
                all_keys = set(old_params.keys()) | set(new_params.keys())
                changes: dict[str, dict] = {}
                for key in all_keys:
                    old_val = old_params.get(key)
                    new_val = new_params.get(key)
                    if old_val != new_val:
                        changes[key] = {"old": old_val, "new": new_val}
                if changes:
                    params_changed.append(
                        {
                            "path": path,
                            "changes": changes,
                        }
                    )

        _LIST_CAP = 200
        return {
            "action": "compare",
            "snapshot_name": snapshot_name,
            "nodes_added": nodes_added[:_LIST_CAP],
            "nodes_added_count": len(nodes_added),
            "nodes_removed": nodes_removed[:_LIST_CAP],
            "nodes_removed_count": len(nodes_removed),
            "params_changed": params_changed[:_LIST_CAP],
            "params_changed_count": len(params_changed),
            "truncated": (
                len(nodes_added) > _LIST_CAP
                or len(nodes_removed) > _LIST_CAP
                or len(params_changed) > _LIST_CAP
            ),
        }

    else:
        raise ValueError(f"Unknown action '{action}'. Must be 'take', 'compare' or 'restore'.")


###### context.get_node_errors_detailed


def _dedupe_messages(messages: list) -> list:
    """Each distinct message once, with `(xN)` when it repeated on the node."""
    counts: dict[str, int] = {}
    for message in messages:
        counts[str(message)] = counts.get(str(message), 0) + 1
    return [m if n == 1 else f"{m} (x{n})" for m, n in counts.items()]


def _inside_locked_asset(node: Any) -> bool:
    with contextlib.suppress(Exception):
        return bool(node.isInsideLockedHDA())
    return False


def _get_node_errors_detailed(
    node_path: str | None = None,
    root_path: str = "/",
    include_locked_assets: bool = False,
    include_warnings: bool = True,
    **_: Any,
) -> dict:
    """Deep error analysis for a specific node or all descendants of a root.

    For each error, returns the node path, error message, node type,
    and parameters that might be causing it (e.g. file path params
    pointing to missing files).

    A scan skips the nodes inside locked HDAs (stock heightfield nodes'
    unused for-each blocks drowned the real errors: 25 hits) unless
    include_locked_assets=True; the skipped count is reported. A message a
    node repeats is listed once with its count, and a node whose only
    errors come from an erroring input is marked `blocked_by` that input
    instead of repeating its message (`root_errors` lists the causes).

    Args:
        node_path: If given, analyze errors on this specific node.
        root_path: If node_path is None, scan all descendants of this root.
        include_locked_assets: Also scan inside locked HDAs.
        include_warnings: Report warnings too (default True).
    """
    import os as _os

    results: list[dict] = []
    scanned_nodes: list = []

    def _analyze_node(node: hou.Node) -> None:
        errors = []
        warnings = []
        with contextlib.suppress(Exception):
            errors = _dedupe_messages(list(node.errors()))
        if include_warnings:
            with contextlib.suppress(Exception):
                warnings = _dedupe_messages(list(node.warnings()))

        if not errors and not warnings:
            return

        # Identify parameters that might be causing issues
        suspect_parms: list[dict] = []
        for parm in node.parms():
            try:
                tmpl = parm.parmTemplate()
                # Check file-type parameters
                if tmpl.type() == hou.parmTemplateType.String:
                    tags = tmpl.tags()
                    is_file = (
                        tags.get("filechooser_mode") is not None
                        or "file" in parm.name().lower()
                        or tmpl.stringType() == hou.stringParmType.FileReference
                    )
                    if is_file:
                        val = parm.eval()
                        if val and not val.startswith("op:"):
                            # Expand Houdini variables
                            expanded = hou.text.expandString(val)
                            if expanded and not _os.path.exists(expanded):
                                suspect_parms.append(
                                    {
                                        "name": parm.name(),
                                        "value": val,
                                        "expanded": expanded,
                                        "issue": "file_not_found",
                                    }
                                )
            except Exception:
                pass

        entry: dict[str, Any] = {
            "node_path": node.path(),
            "node_type": node.type().name(),
        }
        if errors:
            entry["errors"] = errors
        if warnings:
            entry["warnings"] = warnings
        if suspect_parms:
            entry["suspect_parameters"] = suspect_parms
        results.append(entry)
        scanned_nodes.append(node)

    skipped_locked = 0
    if node_path is not None:
        node = _get_node(node_path)
        _analyze_node(node)
    else:
        root = _get_node(root_path)
        _analyze_node(root)
        for child in root.allSubChildren():
            if not include_locked_assets and _inside_locked_asset(child):
                skipped_locked += 1
                continue
            _analyze_node(child)

    result: dict[str, Any] = {
        "scanned_path": node_path or root_path,
        "error_node_count": sum(1 for r in results if r.get("errors")),
        "details": results,
    }
    # Inherited errors collapse to their root, as in verify_network.
    with contextlib.suppress(Exception):
        from fxhoudinimcp_server.handlers.graph_handlers import _collapse_inherited_errors

        reports = [
            {"path": r["node_path"], "errors": r["errors"]} for r in results if r.get("errors")
        ]
        if len(reports) > 1:
            roots = _collapse_inherited_errors(scanned_nodes, reports)
            by_path = {r["path"]: r for r in reports}
            for entry in results:
                report = by_path.get(entry["node_path"])
                if report and report.get("blocked_by"):
                    entry["blocked_by"] = report["blocked_by"]
                    if "errors" not in report:
                        entry.pop("errors", None)
            result["root_errors"] = roots
    if skipped_locked:
        result["skipped_inside_locked_assets"] = skipped_locked
    return result


###### Registration

register_handler("context.get_network_overview", _get_network_overview)
register_handler("context.get_cook_chain", _get_cook_chain)
register_handler("context.explain_node", _explain_node)
register_handler("context.get_selection", _get_selection)
register_handler("context.set_selection", _set_selection)
register_handler("context.get_scene_summary", _get_scene_summary)
register_handler("context.compare_snapshots", _compare_snapshots)
register_handler("context.get_node_errors_detailed", _get_node_errors_detailed)
