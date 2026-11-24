"""Tests for the game-engine workflow guides (agent UX batch 2, P4).

Six guides were added for Houdini -> Unreal Engine 5.8 work (landscape, game
assets, VAT, Niagara, roads/rivers, mesh QA). get_workflow_guide serves them
through workflow_guide_text, which runs str.format over the file: one stray
brace in a VEX snippet raises KeyError/ValueError at the agent's call, not at
import. So every topic and alias is rendered here through that same path.
"""

from __future__ import annotations

# Built-in
import re

# Third-party
import pytest

# Internal
from fxhoudinimcp.prompts.workflows import (
    _GUIDE_ALIASES,
    _SIM_ALIASES,
    houdini_workflow,
    simulation_setup,
    workflow_guide_text,
)

_NEW_TOPICS = [
    "unreal_landscape",
    "game_assets",
    "vat",
    "niagara",
    "roads_rivers",
    "mesh_qa",
]

_ALIASES = {
    "ue": "unreal_landscape",
    "unreal": "unreal_landscape",
    "landscape": "unreal_landscape",
    "UE": "unreal_landscape",
    "game": "game_assets",
    "houdini_engine": "game_assets",
    "vertex_animation": "vat",
    "river": "roads_rivers",
    "road": "roads_rivers",
    "roads": "roads_rivers",
    "qa": "mesh_qa",
    "mesh-qa": "mesh_qa",
    "mesh qa": "mesh_qa",
}

_LEFTOVER = re.compile(r"\{[a-z_]+\}")


@pytest.mark.parametrize("topic", _NEW_TOPICS)
def test_new_guide_renders_through_the_tool_path(topic):
    text = workflow_guide_text(topic, "a test build")
    assert text.strip()
    assert not _LEFTOVER.search(text), _LEFTOVER.findall(text)
    assert "a test build" in text


@pytest.mark.parametrize("topic", _NEW_TOPICS)
def test_new_guide_ends_with_a_verify_section(topic):
    text = workflow_guide_text(topic)
    assert "## Verify" in text
    verify = text.split("## Verify", 1)[1]
    # The checks name MCP tools, not just prose.
    assert re.search(
        r"`(verify_network|get_attrib_stats|get_volume_info|get_bounding_box|start_render|get_parameters)\(",
        verify,
    )


@pytest.mark.parametrize("topic", _NEW_TOPICS)
def test_new_guide_is_also_served_by_the_prompt(topic):
    assert houdini_workflow(topic) == workflow_guide_text(topic)


@pytest.mark.parametrize(("alias", "topic"), sorted(_ALIASES.items()))
def test_alias_resolves_to_its_guide(alias, topic):
    assert workflow_guide_text(alias, "same framing") == workflow_guide_text(topic, "same framing")


def test_every_guide_alias_points_at_a_shipped_file():
    for alias, topic in _GUIDE_ALIASES.items():
        assert workflow_guide_text(alias).strip(), alias
        assert topic in _NEW_TOPICS, (alias, topic)


def test_guide_aliases_do_not_shadow_or_change_sim_aliases():
    assert not set(_GUIDE_ALIASES) & set(_SIM_ALIASES)
    # simulation_setup does not learn the guide aliases: "road" is not a sim.
    assert simulation_setup("road").startswith("You are setting up a road simulation")
    # And the existing sim aliases still win where they did.
    assert "FLIP inside an ocean" in workflow_guide_text("water")


def test_unreal_landscape_opens_with_the_layer_blend_rule():
    first = workflow_guide_text("unreal_landscape").lstrip().splitlines()[0]
    assert "unreal_material" in first
    assert "Layer Blend" in first


def test_discipline_carries_the_session_lessons_and_still_formats():
    text = workflow_guide_text("discipline")
    assert not _LEFTOVER.search(text)
    for phrase in (
        "validate_vex",
        "LIST EVERY CONSUMER OF THE OLD ONE",
        "fresh namespace per call",
        "pane_name=",
        "@primnum",
        "MIN/MAX SPREAD OF THE DRIVEN ATTRIBUTE",
    ):
        assert phrase in text, phrase


def test_heightfields_guide_states_the_two_cell_limit_and_output_semantics():
    text = workflow_guide_text("heightfields")
    assert "narrower than ~2 cells" in text
    assert "heightfield_output exports layers to image files" in text
    assert "convertheightfield turns a heightfield into polygons" in text


def test_get_workflow_guide_docstring_lists_the_new_topics():
    # Internal
    from fxhoudinimcp.tools.help import get_workflow_guide

    fn = getattr(get_workflow_guide, "fn", get_workflow_guide)
    doc = fn.__doc__ or ""
    for topic in [*_NEW_TOPICS, "ue", "river", "qa"]:
        assert topic in doc, topic
