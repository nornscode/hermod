"""Tests for the state module."""

from hermod.state import PRAgentState, State, UserRole, make_agent_id


def test_make_agent_id():
    assert make_agent_id("nornscode", "norns", 42) == "pr:nornscode/norns#42"


def test_from_agent_id_author():
    state = PRAgentState.from_agent_id("pr:nornscode/norns#42", UserRole.AUTHOR)
    assert state.owner == "nornscode"
    assert state.repo == "norns"
    assert state.number == 42
    assert state.user_role == UserRole.AUTHOR
    assert state.current_state == State.WATCHING_AS_AUTHOR


def test_from_agent_id_reviewer():
    state = PRAgentState.from_agent_id("pr:amackera/hermod#7", UserRole.REVIEWER)
    assert state.owner == "amackera"
    assert state.repo == "hermod"
    assert state.number == 7
    assert state.current_state == State.WATCHING_AS_REVIEWER
