"""VEX handlers for FXHoudini-MCP.

Provides tools for creating, reading, and validating VEX code
in Attribute Wrangle nodes and VEX expressions.
"""

from __future__ import annotations

import contextlib

# Built-in
import re
from typing import Any

# Third-party
import hou

# Internal
from fxhoudinimcp_server.config import place_new_node
from fxhoudinimcp_server.dispatcher import register_handler
from fxhoudinimcp_server.errors import readable_message
from fxhoudinimcp_server.ui import focus_network_editor as _focus_network_editor

###### Helpers


def _get_node(node_path: str) -> hou.Node:
    """Return a node or raise if not found."""
    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")
    return node


def _validate_vex_quick(node: hou.Node) -> dict:
    """Cook a wrangle node and return any VEX errors/warnings."""
    with contextlib.suppress(hou.OperationFailed):
        node.cook(force=False)  # the new code already dirtied it; force only redid the work

    errors = []
    warnings = []
    with contextlib.suppress(Exception):
        errors = list(node.errors() or [])
    with contextlib.suppress(Exception):
        warnings = list(node.warnings() or [])

    return {
        "vex_valid": len(errors) == 0,
        "vex_errors": errors,
        "vex_warnings": warnings,
    }


def _resolve_class_value(node: hou.Node, run_over: str) -> int:
    """Resolve a run_over string to the correct menu index on this node.

    Reads the ``class`` parameter's menu labels dynamically so the mapping
    is always correct regardless of Houdini version.
    """
    class_parm = node.parm("class")
    if class_parm is None:
        raise ValueError(
            f"Node {node.path()} has no 'class' parameter — is it an Attribute Wrangle?"
        )

    template = class_parm.parmTemplate()
    labels = list(template.menuLabels())
    items = list(template.menuItems())

    # Try exact match first (case-insensitive), then substring match
    target = run_over.strip().lower()
    for idx, label in enumerate(labels):
        if label.lower() == target:
            return int(items[idx]) if items[idx].isdigit() else idx
    for idx, label in enumerate(labels):
        if target in label.lower():
            return int(items[idx]) if items[idx].isdigit() else idx

    raise ValueError(f"Invalid run_over value '{run_over}'. Available options: {labels}")


def _reverse_class_label(node: hou.Node) -> str | None:
    """Return the human-readable label for the current class value."""
    class_parm = node.parm("class")
    if class_parm is None:
        return None
    value = class_parm.eval()
    template = class_parm.parmTemplate()
    labels = list(template.menuLabels())
    items = list(template.menuItems())
    for idx, item in enumerate(items):
        if (item.isdigit() and int(item) == value) or idx == value:
            return labels[idx] if idx < len(labels) else str(value)
    return str(value)


# Regex pattern for detecting absolute channel paths in VEX code
_RE_ABS_CH = re.compile(r'ch[sfiv]?\s*\(\s*["\']/')


def _check_channel_paths(vex_code: str) -> list[str]:
    """Return warnings if the VEX code contains absolute channel refs."""
    warnings = []
    if _RE_ABS_CH.search(vex_code):
        warnings.append(
            'VEX contains absolute channel path (ch("/...")). '
            "Prefer relative paths — use ../parm_name to reach the "
            "immediate parent, ../../parm_name for two levels up, etc. "
            "Absolute paths break when nodes are renamed or moved."
        )
    return warnings


###### VEX lint: traps that cost a verify round each (agent-ux-notes 4d#4, 4f#12, 4k#4)

# Functions whose return type VEX picks from the receiving side: used where
# nothing fixes that type, the call is "ambiguous".
_GENERIC_READS = ("point", "prim", "vertex", "detail", "primuv", "attrib")
_RE_GENERIC_CALL = re.compile(r"\b(" + "|".join(_GENERIC_READS) + r")\s*\(")
_RE_COMPARISON = re.compile(r"(?:[<>]=?|[!=]=)$")
_RE_COMPARISON_AFTER = re.compile(r"^(?:[<>]=?|[!=]=)(?![<>=])")
_TYPED_PREFIX = re.compile(r"\b(?:int|float|vector2?|vector4|matrix[23]?|string|dict)\s*$")
_RE_ARRAY_CALL = re.compile(
    r"\b(append|push|insert|setpointattrib|setprimattrib|setdetailattrib|"
    r"setvertexattrib|setattrib)\s*\("
)
_RE_FOREACH_LITERAL = re.compile(r"\bforeach\s*\([^;]*;\s*(?:array\s*\(|\{)")
_RE_ADDPOINT = re.compile(r"\baddpoint\s*\(")
_RE_READ_INPUT0 = re.compile(r"\b(?:point|npoints|pointattrib)\s*\(\s*0\s*[,)]")
_RE_RANGE = re.compile(r"(?<![\w.@])range\s*\(")

_HINT_AMBIGUOUS = (
    "{name}() at line {line} has no type to return here ({where}): VEX cannot pick the "
    "overload and reports an ambiguous call. Assign it to a typed variable first "
    "(float v = {name}(...);) or prefix the type (f@, v[]@...)."
)
_HINT_FOREACH = (
    "foreach over an array literal at line {line}: VEX cannot type the literal and fails "
    "with the cryptic 'uninitialized __impl_foreach_end'. Store it in a typed array first: "
    "int items[] = array(a, b); foreach (int x; items) ..."
)
_HINT_ADDPOINT = (
    "addpoint() creates points that point(0, ...) / npoints(0) cannot see in the same cook: "
    "input 0 is the geometry as it came in. Keep the new points' values in local "
    "variables or arrays."
)
_HINT_RANGE = "VEX has no range(); use for (int i = 0; i < n; i++)."


def _strip_comments(code: str) -> str:
    """*code* with // and /* */ comments blanked, line numbers kept."""
    code = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), code, flags=re.S)
    return re.sub(r"//[^\n]*", "", code)


def _close_paren(code: str, open_index: int) -> int:
    """Index of the ')' matching the '(' at *open_index*; len(code) if unbalanced."""
    depth = 0
    for index in range(open_index, len(code)):
        char = code[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return len(code)


def _top_level_args(text: str) -> list[str]:
    """Comma-split *text* (a call's arguments) at depth 0."""
    args, depth, start = [], 0, 0
    for index, char in enumerate(text):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            args.append(text[start:index])
            start = index + 1
    args.append(text[start:])
    return [a.strip() for a in args]


def vex_lint(code: str | None) -> list[str]:
    """Plain-English hints for the VEX traps an agent hit again and again.

    Heuristics, not a parser: a hint is a reason to look, not an error.
    """
    if not code:
        return []
    text = _strip_comments(code)
    hints: list[str] = []

    def line_of(index: int) -> int:
        return text.count("\n", 0, index) + 1

    for match in _RE_GENERIC_CALL.finditer(text):
        name = match.group(1)
        before = text[: match.start()].rstrip()
        if _TYPED_PREFIX.search(before) or before.endswith(("@", ".")):
            continue
        end = _close_paren(text, match.end() - 1)
        after = text[end + 1 :].lstrip()
        if _RE_COMPARISON.search(before) or _RE_COMPARISON_AFTER.match(after):
            hint = _HINT_AMBIGUOUS.format(
                name=name, line=line_of(match.start()), where="a comparison"
            )
            if hint not in hints:
                hints.append(hint)
    for match in _RE_ARRAY_CALL.finditer(text):
        end = _close_paren(text, match.end() - 1)
        args = _top_level_args(text[match.end() : end])
        # append(arr, point(...)), setpointattrib(0, "n", pt, point(...)).
        value = next((a for a in args[1:] if _RE_GENERIC_CALL.match(a)), None)
        if value is not None:
            name = _RE_GENERIC_CALL.match(value).group(1)
            hint = _HINT_AMBIGUOUS.format(
                name=name,
                line=line_of(match.start()),
                where=f"an argument of {match.group(1)}()",
            )
            if hint not in hints:
                hints.append(hint)
    for match in _RE_FOREACH_LITERAL.finditer(text):
        hints.append(_HINT_FOREACH.format(line=line_of(match.start())))
    if _RE_ADDPOINT.search(text) and _RE_READ_INPUT0.search(text):
        hints.append(_HINT_ADDPOINT)
    if _RE_RANGE.search(text):
        hints.append(_HINT_RANGE)
    return hints


def error_hints(errors: list) -> list[str]:
    """Plain-English readings of VEX compiler messages that say little on their own."""
    hints: list[str] = []
    joined = "\n".join(str(e) for e in errors or [])
    if "__impl_foreach" in joined:
        hints.append(
            "'__impl_foreach_end' means a foreach whose array VEX could not type, "
            "usually an array literal: store it in a typed array variable first."
        )
    if re.search(r"[Aa]mbiguous call", joined):
        hints.append(
            "An ambiguous call is a function whose return type VEX picks from its use "
            "(point(), prim(), detail()...): assign it to a typed variable first."
        )
    return hints


def _hints_for(code: str | None, errors: list) -> list[str]:
    found = vex_lint(code)
    found += [h for h in error_hints(errors) if h not in found]
    return found


###### vex.create_wrangle


def create_wrangle(
    parent_path: str,
    vex_code: str,
    run_over: str = "Points",
    name: str = None,
) -> dict:
    """Create an Attribute Wrangle node with VEX code.

    Args:
        parent_path: Path to the parent SOP network.
        vex_code: The VEX snippet code to set.
        run_over: What to run the wrangle over:
                  "Points", "Vertices", "Primitives", "Detail", or "Numbers".
        name: Optional explicit name for the node.
    """
    parent = hou.node(parent_path)
    if parent is None:
        raise ValueError(f"Parent node not found: {parent_path}")

    # Create the attribwrangle node
    try:
        if name:
            node = parent.createNode("attribwrangle", name)
        else:
            node = parent.createNode("attribwrangle")
    except hou.OperationFailed as e:
        raise ValueError(f"Failed to create attribwrangle node: {readable_message(e)}") from e

    # Set the VEX snippet
    snippet_parm = node.parm("snippet")
    if snippet_parm is None:
        raise ValueError(f"Created node {node.path()} does not have a 'snippet' parameter.")
    snippet_parm.set(vex_code)

    # Set the run_over class (resolved dynamically from menu labels)
    class_value = _resolve_class_value(node, run_over)
    node.parm("class").set(class_value)

    _focus_network_editor(node)

    # Build relative prefix map so the AI knows what each ../ level reaches.
    ancestors = {}
    current = node.parent()
    level = 1
    while current is not None:
        dots = "/".join([".."] * level)
        ancestors[dots] = current.path()
        current = current.parent()
        level += 1

    result = {
        "success": True,
        "node_path": node.path(),
        "node_name": node.name(),
        "run_over": run_over,
        "vex_code": vex_code,
        "channel_prefix": "../",
        "channel_ancestors": ancestors,
        "channel_hint": (
            "Use relative paths for channel references. "
            "See channel_ancestors to find the correct ../ depth "
            "for the node whose parameters you want to reference."
        ),
    }
    result.update(_validate_vex_quick(node))

    path_warnings = _check_channel_paths(vex_code)
    if path_warnings:
        result.setdefault("vex_warnings", []).extend(path_warnings)

    return result


###### vex.set_wrangle_code


def _apply_snippet_edits(code: str, edits: Any) -> tuple[str, list[dict]]:
    """Apply exact {old, new} replacements in order; raise before any write.

    Each `old` must occur exactly once in the text as the earlier edits left
    it, so a replacement cannot land on the wrong copy of a repeated line.
    """
    if not isinstance(edits, (list, tuple)) or not edits:
        raise ValueError('edits must be a non-empty list of {"old": ..., "new": ...}.')
    # A snippet saved with CRLF would never match an `old` typed with LF.
    code = code.replace("\r\n", "\n")
    applied = []
    for n, edit in enumerate(edits):
        if not isinstance(edit, dict) or set(edit) != {"old", "new"}:
            raise ValueError(f'edits[{n}] must be {{"old": ..., "new": ...}}. Nothing was written.')
        old = str(edit["old"]).replace("\r\n", "\n")
        new = str(edit["new"]).replace("\r\n", "\n")
        count = code.count(old) if old else 0
        if count != 1:
            where = "is empty" if not old else f"occurs {count} times"
            raise ValueError(
                f"edits[{n}]: old {where} in the snippet; it must occur exactly once "
                "(add surrounding lines to make it unique). Nothing was written."
            )
        start = code.index(old)
        code = code[:start] + new + code[start + len(old) :]
        # An earlier block after this one moved with it; lines are read off the final text.
        for spans in applied:
            if spans["start"] >= start + len(old):
                spans["start"] += len(new) - len(old)
        applied.append({"index": n, "start": start, "text": new})
    for entry in applied:
        start, new = entry.pop("start"), entry["text"]
        first = code.count("\n", 0, start) + 1
        # A trailing newline ends the last line, it does not start one.
        entry["lines"] = [first, first + new.count("\n", 0, max(len(new) - 1, 0))]
    return code, applied


def set_wrangle_code(node_path: str, vex_code: str | None = None, edits: Any = None) -> dict:
    """Set VEX code on an existing Attribute Wrangle node.

    Args:
        node_path: Path to the wrangle node.
        vex_code: The VEX snippet code to set.
        edits: Instead of vex_code, exact replacements in the snippet as it
            stands: [{"old": ..., "new": ...}], applied in order, each `old`
            found exactly once; one bad edit writes nothing. A block of a
            130-line snippet needed the whole text, or execute_python.
            The reply then names the changed lines, not the code.

    Neither form echoes the code: the reply carries `line_count`,
    `char_count`, `vex_valid` / `vex_errors` and lint `hints`.
    """
    node = _get_node(node_path)

    snippet_parm = node.parm("snippet")
    if snippet_parm is None:
        raise ValueError(
            f"Node {node_path} does not have a 'snippet' parameter. Is it an Attribute Wrangle?"
        )
    if (vex_code is None) == (edits is None):
        raise ValueError("Give vex_code (the whole snippet) or edits (replacements in it).")
    applied = None
    if edits is not None:
        vex_code, applied = _apply_snippet_edits(
            # A promoted snippet is a channel reference: unexpandedString()
            # raises on it, while set() below follows it anyway.
            snippet_parm.getReferencedParm().unexpandedString(),
            edits,
        )

    snippet_parm.set(vex_code)

    # The snippet is the caller's own text: a 200-line wrangle echoed back
    # was ~10k tokens (agent-ux-notes 4f#5). Counts say it landed.
    result = {
        "success": True,
        "node_path": node.path(),
        "line_count": len(vex_code.splitlines()),
        "char_count": len(vex_code),
        "channel_prefix": "../",
    }
    if applied is not None:
        result["edits_applied"] = applied
    result.update(_validate_vex_quick(node))

    path_warnings = _check_channel_paths(vex_code)
    if path_warnings:
        result.setdefault("vex_warnings", []).extend(path_warnings)
    hints = _hints_for(vex_code, result.get("vex_errors") or [])
    if hints:
        result["hints"] = hints

    return result


###### vex.get_wrangle_code


def get_wrangle_code(node_path: str) -> dict:
    """Read the VEX code from an Attribute Wrangle node.

    Args:
        node_path: Path to the wrangle node.
    """
    node = _get_node(node_path)

    snippet_parm = node.parm("snippet")
    if snippet_parm is None:
        raise ValueError(
            f"Node {node_path} does not have a 'snippet' parameter. Is it an Attribute Wrangle?"
        )

    vex_code = snippet_parm.eval()

    # Also get the run_over class (resolved dynamically from menu labels)
    run_over = _reverse_class_label(node)

    return {
        "node_path": node.path(),
        "vex_code": vex_code,
        "run_over": run_over,
    }


###### vex.create_vex_expression


def create_vex_expression(
    node_path: str,
    parm_name: str,
    vex_code: str,
) -> dict:
    """Set a parameter expression, refusing VEX that cannot run there.

    HOM parameter expressions are HScript or Python; there is no VEX language
    for them. This used to store the code as HScript regardless, falling back
    to Python, and said success for `@P.y*2` that then failed at every
    evaluation. VEX belongs in a wrangle.

    Args:
        node_path: Path to the node.
        parm_name: Name of the parameter.
        vex_code: The expression code (HScript syntax).
    """
    import re

    node = _get_node(node_path)

    parm = node.parm(parm_name)
    if parm is None:
        raise ValueError(f"Parameter '{parm_name}' not found on node {node_path}.")
    if re.search(r"(^|[^\w$])[fvisp]?\[?\]?@\w|;\s*$", vex_code):
        raise ValueError(
            "That is VEX (attribute @ syntax or a trailing ';'), which a parameter "
            "expression cannot run: parameters take HScript or Python. Put VEX in a "
            "wrangle with create_wrangle, or write the HScript equivalent (point(), ch())."
        )
    # Validated before it is set. setExpression does not parse, eval() of an
    # unknown function answers 0 without raising, and the node error that a
    # cook leaves behind outlives a later valid expression (all measured on
    # 22.0.368). hscriptExpression with the node as pwd raises on an unknown
    # function or a bracing error and resolves relative references.
    previous = hou.pwd()
    try:
        hou.setPwd(node)
        hou.hscriptExpression(vex_code)
    except hou.OperationFailed as e:
        detail = str(e).replace("The attempted operation failed.", "").strip()
        raise ValueError(
            f"The expression does not evaluate on {node_path}/{parm_name}, so it was "
            f"not set: {detail or readable_message(e)}"
        ) from e
    finally:
        hou.setPwd(previous)
    from fxhoudinimcp_server.handlers.parameter_handlers import set_whole_expression

    replaced = set_whole_expression(parm, vex_code, hou.exprLanguage.Hscript)
    value = parm.eval()

    return {
        "success": True,
        "node_path": node.path(),
        "parm_name": parm_name,
        "expression": vex_code,
        "language": "hscript",
        "value": value if isinstance(value, (int, float, str)) else str(value),
        **({"replaced_keyframes": replaced} if replaced else {}),
    }


###### vex.validate_vex


# context -> wrangle node type a scratch check is made of.
_SCRATCH_TYPES = {
    "sop": "attribwrangle",
    "attrib": "attribwrangle",
    "attribwrangle": "attribwrangle",
    "volume": "volumewrangle",
    "volumewrangle": "volumewrangle",
}


def _no_undo():
    """hou.undos.disabler(), so a scratch node leaves no undo entries behind."""
    with contextlib.suppress(Exception):
        return hou.undos.disabler()
    return contextlib.nullcontext()


def _scratch_wrangle(near: hou.Node | None, type_name: str | None) -> tuple[hou.Node, hou.Node]:
    """(wrangle, node to destroy): next to *near* with its inputs, or in a scratch /obj geo.

    Next to the wrangle it would replace, relative ch() paths and the inputs'
    attributes are what the real node sees; a locked parent falls back to
    the scratch container.
    """
    if near is not None:
        with contextlib.suppress(Exception):
            wrangle = near.parent().createNode(type_name or near.type().name(), "__fxh_vex_check")
            for index, source in enumerate(near.inputs()):
                if source is not None:
                    with contextlib.suppress(Exception):
                        wrangle.setInput(index, source)
            place_new_node(wrangle)  # beside the user's nodes, never on top of one
            return wrangle, wrangle
    container = hou.node("/obj").createNode("geo", "__fxh_vex_scratch")
    place_new_node(container)
    wrangle = container.createNode(type_name or "attribwrangle", "__fxh_vex_check")
    place_new_node(wrangle)
    return wrangle, container


def _validate_code(
    code: str, node_path: str | None, run_over: str | None, context: str | None
) -> dict:
    """Compile *code* in a scratch wrangle that is destroyed afterwards."""
    type_name = None
    if context is not None:
        type_name = _SCRATCH_TYPES.get(str(context).strip().lower())
        if type_name is None:
            raise ValueError(f"Unknown context {context!r}; known: {sorted(_SCRATCH_TYPES)}")
    near = _get_node(node_path) if node_path else None
    with _no_undo():
        wrangle, scratch = _scratch_wrangle(near, type_name)
        try:
            wrangle.parm("snippet").set(code)
            class_parm = wrangle.parm("class")
            if class_parm is not None:
                if run_over:
                    class_parm.set(_resolve_class_value(wrangle, run_over))
                elif near is not None and near.parm("class") is not None:
                    class_parm.set(near.parm("class").eval())
            checked = _validate_vex_quick(wrangle)
            label = _reverse_class_label(wrangle) if class_parm is not None else None
            scratch_path = wrangle.path()
        finally:
            with contextlib.suppress(Exception):
                scratch.destroy()
    errors = [str(e).replace(scratch_path, "<snippet>") for e in checked["vex_errors"]]
    warnings = [str(w).replace(scratch_path, "<snippet>") for w in checked["vex_warnings"]]
    warnings += _check_channel_paths(code)
    result = {
        "is_valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "hints": _hints_for(code, errors),
        "line_count": len(code.splitlines()),
        "checked_in": "next to " + near.path() if near is not None else "scratch network",
    }
    if label is not None:
        result["run_over"] = label
    result["message"] = (
        "VEX code is valid." if not errors else f"VEX code has {len(errors)} error(s)."
    )
    return result


def validate_vex(
    node_path: str | None = None,
    code: str | None = None,
    run_over: str | None = None,
    context: str | None = None,
) -> dict:
    """Validate VEX code by cooking the node and checking for errors.

    With *code*, the snippet is compiled in a scratch wrangle that is
    destroyed afterwards (no undo entries), before it lands anywhere: next to
    *node_path* with its inputs and run-over class when one is given, else in
    a scratch network. The reply never echoes the code.

    Args:
        node_path: Path to the wrangle node to validate (or, with code, the
            wrangle the code is meant for).
        code: A snippet to compile instead of the node's own.
        run_over: With code: "Points", "Primitives", "Detail", ...
        context: With code: "sop" (attribwrangle, default) or "volume".
    """
    if code is not None:
        return _validate_code(str(code), node_path, run_over, context)
    if not node_path:
        raise ValueError("Give node_path (a wrangle to check) or code (a snippet to compile).")
    node = _get_node(node_path)

    # Read the current VEX code for reference
    vex_code = None
    snippet_parm = node.parm("snippet")
    if snippet_parm is not None:
        vex_code = snippet_parm.eval()

    # Force cook the node to trigger VEX compilation. A failure here is the
    # expected path for broken VEX; the errors are read off the node below.
    with contextlib.suppress(hou.OperationFailed):
        node.cook(force=False)  # the new code already dirtied it; force only redid the work

    # Gather errors and warnings
    errors = []
    warnings = []

    try:
        node_errors = node.errors()
        if node_errors:
            errors = list(node_errors)
    except Exception:
        pass

    try:
        node_warnings = node.warnings()
        if node_warnings:
            warnings = list(node_warnings)
    except Exception:
        pass

    is_valid = len(errors) == 0

    result = {
        "node_path": node.path(),
        "is_valid": is_valid,
        "errors": errors,
        "warnings": warnings,
    }

    if vex_code is not None:
        result["vex_code"] = vex_code
    hints = _hints_for(vex_code, errors)
    if hints:
        result["hints"] = hints

    if is_valid:
        result["message"] = "VEX code is valid."
    else:
        result["message"] = f"VEX code has {len(errors)} error(s)."

    return result


###### Registration

register_handler("vex.create_wrangle", create_wrangle)
register_handler("vex.set_wrangle_code", set_wrangle_code)
register_handler("vex.get_wrangle_code", get_wrangle_code)
register_handler("vex.create_vex_expression", create_vex_expression)
register_handler("vex.validate_vex", validate_vex)
