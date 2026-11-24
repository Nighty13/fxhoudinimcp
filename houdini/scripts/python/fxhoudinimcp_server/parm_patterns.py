"""One parameter-filter vocabulary for every tool that narrows parms by name.

get_parameters(patterns), get_parameter_schema(filter / patterns) and
get_node_card(parm_filter) each grew their own matching: substrings in one,
`a|b` alternatives in another, and the literal `qa_*` an agent wrote matched
nothing anywhere (agent-ux-notes 4l#4). The rule, in one place:

* a pattern holding ``*``, ``?`` or ``[`` is a glob (fnmatch), matched against
  the WHOLE name or label: ``qa_*``, ``*scale``, ``ramp?pos``;
* any other pattern is a substring: ``scale`` matches ``uniformscale``;
* ``a|b`` inside one pattern is a list of alternatives, the same as passing
  ``["a", "b"]``;
* case is ignored, and a parm matches when its name OR its label does.

Substring-only patterns without ``|`` behave exactly as before, so a caller
that never wrote a glob sees no change.
"""

from __future__ import annotations

# Built-in
import contextlib
import fnmatch
from typing import Any

_GLOB_CHARS = frozenset("*?[")


def compile_patterns(patterns: Any) -> list[str] | None:
    """Lowered terms of *patterns* (a str or a list of str), `|` split; None for none.

    None means "no filter": every parm matches. An empty list, or only empty
    alternatives, is no filter either.
    """
    if patterns is None:
        return None
    if isinstance(patterns, str):
        patterns = [patterns]
    terms: list[str] = []
    for pattern in patterns:
        if pattern is None:
            continue
        for term in str(pattern).split("|"):
            term = term.strip().lower()
            if term and term not in terms:
                terms.append(term)
    return terms or None


def is_glob(term: str) -> bool:
    """Whether *term* is matched as a glob rather than a substring."""
    return any(char in _GLOB_CHARS for char in term)


def term_matches(term: str, text: str) -> bool:
    """One lowered *term* against one text: whole-text glob, or substring."""
    text = (text or "").lower()
    if is_glob(term):
        return fnmatch.fnmatchcase(text, term)
    return term in text


def matches_any(terms: list[str] | None, *texts: str) -> bool:
    """Whether any term matches any of *texts*; True when *terms* is None."""
    if terms is None:
        return True
    return any(term_matches(term, text) for term in terms for text in texts if text)


def matches(patterns: Any, *texts: str) -> bool:
    """compile_patterns + matches_any in one call, for a caller holding raw patterns.

    The drop-in for graph_handlers._filter_matches(parm_filter, name, label).
    """
    return matches_any(compile_patterns(patterns), *texts)


def parm_label(parm: Any) -> str:
    """A live parm's label, "" when it has none to read."""
    with contextlib.suppress(Exception):
        return str(parm.parmTemplate().label())
    with contextlib.suppress(Exception):
        return str(parm.description())
    return ""


def parm_matches(parm: Any, terms: list[str] | None) -> bool:
    """Whether a live hou.Parm's name or label matches any compiled term."""
    if terms is None:
        return True
    return matches_any(terms, parm.name(), parm_label(parm))
