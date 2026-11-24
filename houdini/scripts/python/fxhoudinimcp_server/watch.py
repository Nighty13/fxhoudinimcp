"""Notice what the user changed between two MCP calls.

The user drives the same Houdini the agent does. In one session they changed
seven HDA parameters and moved a display flag while the agent worked; nothing
said so, and the agent found out only when its numbers moved (agent-ux-notes
4h #2). An agent that does not know can overwrite someone's experiment.

Every node a command names (node_path, parent_path, ...) is watched, with its
direct children when it is a network: a Houdini event callback records each
parameter, flag and child change that happens OUTSIDE an MCP call. The next
reply carries them as `scene_changed_outside_mcp` and the record is cleared.
Changes made by the MCP's own calls are not recorded.
"""

from __future__ import annotations

# Built-in
import contextlib
from typing import Any

# Third-party
import hou

_WATCH_LIMIT = 2000
_CHILDREN_PER_NETWORK = 300
_REPORT_LIMIT = 40

_depth = 0
_watched: set[str] = set()
_changes: dict[str, list[str]] = {}
_event_count = 0


def _event_types() -> tuple:
    kinds = []
    for name in ("ParmTupleChanged", "FlagChanged", "ChildCreated", "ChildDeleted"):
        with contextlib.suppress(Exception):
            kinds.append(getattr(hou.nodeEventType, name))
    return tuple(kinds)


def _record(path: str, what: str) -> None:
    global _event_count
    _event_count += 1
    seen = _changes.setdefault(path, [])
    if what not in seen and len(seen) < 20:
        seen.append(what)


_FLAG_NAMES = (
    "isDisplayFlagSet",
    "isRenderFlagSet",
    "isBypassed",
    "isTemplateFlagSet",
    "isHardLocked",
)
# Flags last seen per watched node (session id), refreshed when each call ends.
_flags: dict[str, tuple] = {}


def _flags_of(node: Any) -> tuple:
    values = []
    for name in _FLAG_NAMES:
        try:
            values.append(bool(getattr(node, name)()))
        except Exception:
            values.append(None)
    return tuple(values)


def _driven(parm_tuple: Any) -> bool:
    """A parm whose value an expression or channel sets: cooks change it, not people.

    A for-each loop re-evaluates prim('../face_n', 0, 'N', 0) on every pass,
    and each pass fired ParmTupleChanged: 72 "edits" were reported after one
    cook nobody touched.
    """
    with contextlib.suppress(Exception):
        return any(len(parm.keyframes()) > 0 for parm in parm_tuple)
    return False


def _on_event(event_type: Any = None, **kwargs: Any) -> None:
    """Houdini node event callback. Ignores everything inside an MCP call."""
    if _depth > 0:
        return
    with contextlib.suppress(Exception):
        node = kwargs.get("node")
        # A locked asset's internals change when the asset cooks
        # (attribrandomize's own switch1), never by hand.
        if node.isInsideLockedHDA() is True:
            return
        path = node.path()
        name = getattr(event_type, "name", lambda: str(event_type))()
        if name == "ParmTupleChanged":
            parm_tuple = kwargs.get("parm_tuple")
            if parm_tuple is not None and _driven(parm_tuple):
                return
            _record(path, f"parm {parm_tuple.name()}" if parm_tuple is not None else "parms")
        elif name == "FlagChanged":
            # The event also fires for selection and current-node changes and
            # for display flags an MCP call set (delivered after it ended):
            # only a real flag that differs from what the last call left is an edit.
            key = str(node.sessionId())
            now = _flags_of(node)
            if _flags.get(key) == now:
                return
            _flags[key] = now
            _record(path, "flags")
        elif name == "ChildCreated":
            _record(path, f"+{kwargs['child_node'].name()}")
        elif name == "ChildDeleted":
            _record(path, f"-{kwargs['child_node'].name()}")


def _watch(node: Any) -> None:
    if node is None or len(_watched) >= _WATCH_LIMIT:
        return
    with contextlib.suppress(Exception):
        # By session id, not path: after new_scene or a delete + rebuild, a
        # node at the same path is a different node with no callback.
        key = str(node.sessionId())
        if key in _watched:
            return
        node.addEventCallback(_event_types(), _on_event)
        _watched.add(key)
        _flags[key] = _flags_of(node)


def _paths_in(params: dict[str, Any]) -> list[str]:
    found = []
    for key, value in (params or {}).items():
        if not (key.endswith("path") or key.endswith("paths") or key in ("path", "parent")):
            continue
        values = value if isinstance(value, list | tuple) else [value]
        found += [v for v in values if isinstance(v, str) and v.startswith("/")]
    return found


def begin_call() -> None:
    global _depth
    _depth += 1


def end_call(params: dict[str, Any]) -> None:
    """Leave the call and watch what it named, so later user edits are seen."""
    global _depth
    _depth = max(0, _depth - 1)
    if _depth:
        return
    # What this call did to flags (a display flag moves off a sibling too) is
    # the new baseline, so the deferred FlagChanged it causes is not an edit.
    for key in list(_flags):
        with contextlib.suppress(Exception):
            node = hou.nodeBySessionId(int(key))
            if node is None:
                _flags.pop(key, None)
            else:
                _flags[key] = _flags_of(node)
    for path in _paths_in(params):
        with contextlib.suppress(Exception):
            node = hou.node(path)
            if node is None:
                continue
            if node.isInsideLockedHDA() is True:
                continue
            _watch(node)
            _watch(node.parent())
            # A locked asset's insides are its own business: the Labs Building
            # Generator's line3 "changed" on every cook it did.
            if node.isLockedHDA() is True:
                continue
            for child in list(node.children())[:_CHILDREN_PER_NETWORK]:
                _watch(child)


def take_changes() -> dict[str, Any] | None:
    """What changed outside MCP calls since the last reply; clears the record."""
    global _event_count
    if not _changes:
        return None
    paths = sorted(_changes)
    report: dict[str, Any] = {
        "events": _event_count,
        "nodes": {path: _changes[path] for path in paths[:_REPORT_LIMIT]},
        "note": (
            "Someone changed these in Houdini since your last call. Read the "
            "current values before writing over them."
        ),
    }
    if len(paths) > _REPORT_LIMIT:
        report["nodes_not_listed"] = len(paths) - _REPORT_LIMIT
    _changes.clear()
    _event_count = 0
    return report
