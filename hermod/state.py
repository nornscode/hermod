"""PR agent state machine — states, transitions, and the state dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class State(str, Enum):
    WATCHING_AS_AUTHOR = "watching_as_author"
    WATCHING_AS_REVIEWER = "watching_as_reviewer"
    PINGED_USER = "pinged_user"
    SNOOZED = "snoozed"
    WAITING_FOR_CI = "waiting_for_ci"
    WAITING_FOR_REVIEW_COUNT = "waiting_for_review_count"
    CLOSED = "closed"


class UserRole(str, Enum):
    AUTHOR = "author"
    REVIEWER = "reviewer"


@dataclass
class PRAgentState:
    """Per-PR agent state, reconstructed from the Norns event log."""

    agent_id: str  # "pr:nornscode/norns#42"
    owner: str
    repo: str
    number: int
    user_role: UserRole
    current_state: State
    pinged_at: Optional[datetime] = None
    snoozed_until: Optional[datetime] = None
    waiting_for_review_count: Optional[int] = None
    last_known_pr_state: dict = field(default_factory=dict)

    @classmethod
    def from_agent_id(cls, agent_id: str, user_role: UserRole) -> PRAgentState:
        """Parse an agent_id like 'pr:nornscode/norns#42' into a state object."""
        # Strip "pr:" prefix
        rest = agent_id.removeprefix("pr:")
        # Split "owner/repo#number"
        repo_part, number_str = rest.rsplit("#", 1)
        owner, repo = repo_part.split("/", 1)

        initial_state = (
            State.WATCHING_AS_AUTHOR
            if user_role == UserRole.AUTHOR
            else State.WATCHING_AS_REVIEWER
        )

        return cls(
            agent_id=agent_id,
            owner=owner,
            repo=repo,
            number=int(number_str),
            user_role=user_role,
            current_state=initial_state,
        )


def make_agent_id(owner: str, repo: str, number: int) -> str:
    """Build a stable agent ID from PR coordinates."""
    return f"pr:{owner}/{repo}#{number}"
