"""PR agent — event handlers and state machine transitions.

This module defines the Norns tools that implement the per-PR agent logic.
Each tool is invoked by the Norns runtime when an event arrives for an agent_id.
The agent state is reconstructed from the event log by Norns; we just define
how to handle each event type and what side effects to produce.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from norns import tool

from hermod.github_client import GitHubClient
from hermod.slack_client import SlackClient, parse_response
from hermod.state import PRAgentState, State, UserRole, make_agent_id

logger = logging.getLogger("hermod.agent")

# These are set by the worker at startup
_github: GitHubClient | None = None
_slack: SlackClient | None = None
_norns_client = None  # NornsClient, set by worker
_config = None  # Config, set by worker


def init(github: GitHubClient, slack: SlackClient, norns_client, config):
    """Initialize module-level clients. Called once at worker startup."""
    global _github, _slack, _norns_client, _config
    _github = github
    _slack = slack
    _norns_client = norns_client
    _config = config


# ---------------------------------------------------------------------------
# Event handling tools — called by the Norns runtime
# ---------------------------------------------------------------------------


@tool(side_effect=True)
def handle_github_event(agent_id: str, event_type: str, payload: str) -> str:
    """Handle an incoming GitHub webhook event for this PR agent.

    The webhook receiver forwards events here via norns.send_event.
    """
    state = _load_state(agent_id, payload)
    if state is None:
        return json.dumps({"error": "Could not reconstruct state"})

    event_data = json.loads(payload)
    action = event_data.get("action", "")

    # PR closed or merged → terminal state
    if event_type == "pull_request" and action in ("closed",):
        state.current_state = State.CLOSED
        logger.info(f"{agent_id}: PR closed/merged → closed")
        return json.dumps({"state": state.current_state.value, "terminal": True})

    # PR reopened
    if event_type == "pull_request" and action == "reopened":
        watching = (
            State.WATCHING_AS_AUTHOR
            if state.user_role == UserRole.AUTHOR
            else State.WATCHING_AS_REVIEWER
        )
        state.current_state = watching
        _set_staleness_timer(agent_id, state)
        logger.info(f"{agent_id}: PR reopened → {watching.value}")
        return json.dumps({"state": state.current_state.value})

    # Review requested for our user
    if event_type == "pull_request" and action == "review_requested":
        requested = event_data.get("requested_reviewer", {}).get("login", "")
        if requested.lower() == _config.github.user.lower():
            state.user_role = UserRole.REVIEWER
            state.current_state = State.WATCHING_AS_REVIEWER
            _set_staleness_timer(agent_id, state)
            logger.info(f"{agent_id}: review requested → watching_as_reviewer")

    # Review request removed
    if event_type == "pull_request" and action == "review_request_removed":
        removed = event_data.get("requested_reviewer", {}).get("login", "")
        if removed.lower() == _config.github.user.lower():
            # If we were watching as reviewer, check if we're still the author
            if state.user_role == UserRole.REVIEWER:
                pr_author = event_data.get("pull_request", {}).get("user", {}).get("login", "")
                if pr_author.lower() == _config.github.user.lower():
                    state.user_role = UserRole.AUTHOR
                    state.current_state = State.WATCHING_AS_AUTHOR
                    _set_staleness_timer(agent_id, state)
                else:
                    # No longer relevant — close
                    state.current_state = State.CLOSED
                    return json.dumps({"state": "closed", "terminal": True})

    # Review submitted
    if event_type == "pull_request_review" and action == "submitted":
        if state.current_state == State.WAITING_FOR_REVIEW_COUNT:
            snapshot = _fetch_snapshot(state)
            if (
                state.waiting_for_review_count is not None
                and snapshot["review_count"] >= state.waiting_for_review_count
            ):
                state.current_state = State.PINGED_USER
                _ping_user(state, f"You asked to be woken at {state.waiting_for_review_count} review(s) — you have {snapshot['review_count']} now.")
                return json.dumps({"state": state.current_state.value})
        elif state.current_state in (State.WATCHING_AS_AUTHOR, State.WATCHING_AS_REVIEWER):
            # Reset the staleness timer — there's activity
            _set_staleness_timer(agent_id, state)

    # CI status events
    if event_type in ("check_run", "status"):
        ci_conclusion = _extract_ci_conclusion(event_type, event_data)

        if state.current_state == State.WAITING_FOR_CI:
            if ci_conclusion == "success":
                state.current_state = State.PINGED_USER
                _ping_user(state, "CI is green. Ready to merge/review?")
            elif ci_conclusion == "failure":
                state.current_state = State.PINGED_USER
                _ping_user(state, "CI failed.")
            return json.dumps({"state": state.current_state.value})

        # CI failure while watching → immediate ping
        if ci_conclusion == "failure" and state.current_state in (
            State.WATCHING_AS_AUTHOR,
            State.WATCHING_AS_REVIEWER,
        ):
            state.current_state = State.PINGED_USER
            _ping_user(state, "CI failed on your PR.")
            return json.dumps({"state": state.current_state.value})

    return json.dumps({"state": state.current_state.value})


@tool(side_effect=True)
def handle_staleness_timer(agent_id: str, payload: str) -> str:
    """Handle a staleness timer firing. Re-evaluate PR state and decide whether to ping."""
    state = _load_state(agent_id, payload)
    if state is None:
        return json.dumps({"error": "Could not reconstruct state"})

    if state.current_state == State.CLOSED:
        return json.dumps({"state": "closed", "terminal": True})

    # If we're in a snoozed state, go back to watching and re-evaluate
    if state.current_state == State.SNOOZED:
        watching = (
            State.WATCHING_AS_AUTHOR
            if state.user_role == UserRole.AUTHOR
            else State.WATCHING_AS_REVIEWER
        )
        state.current_state = watching

    # Fetch fresh state from GitHub
    snapshot = _fetch_snapshot(state)

    # Check if the PR is closed upstream
    if snapshot.get("state") == "closed":
        state.current_state = State.CLOSED
        logger.info(f"{agent_id}: PR closed upstream → closed")
        return json.dumps({"state": "closed", "terminal": True})

    # Decide whether to ping
    should_ping = False
    reason = ""

    if state.user_role == UserRole.AUTHOR:
        if snapshot["review_count"] == 0:
            hours = _config.rules.author_staleness_hours
            reason = f"You opened this {hours}h ago. No reviews submitted yet."
            should_ping = True
        else:
            # There are reviews — reset timer and keep watching
            _set_staleness_timer(agent_id, state)
            return json.dumps({"state": state.current_state.value})

    elif state.user_role == UserRole.REVIEWER:
        hours = _config.rules.reviewer_staleness_hours
        reason = f"You were requested as a reviewer {hours}h ago. Still pending."
        should_ping = True

    if should_ping:
        state.current_state = State.PINGED_USER
        _ping_user(state, reason, snapshot=snapshot)
    else:
        _set_staleness_timer(agent_id, state)

    return json.dumps({"state": state.current_state.value})


@tool(side_effect=True)
def handle_slack_response(agent_id: str, response_text: str, payload: str) -> str:
    """Handle a user's Slack DM response for this PR agent."""
    state = _load_state(agent_id, payload)
    if state is None:
        return json.dumps({"error": "Could not reconstruct state"})

    parsed = parse_response(response_text)

    if parsed.action == "snooze":
        state.current_state = State.SNOOZED
        # Set a durable timer via Norns
        # TODO: norns.set_timer(agent_id, "staleness", parsed.snooze_seconds)
        duration_text = _format_duration(parsed.snooze_seconds or 0)
        _slack.send_reply(f"Snoozed for {duration_text}. I'll check back then.")
        logger.info(f"{agent_id}: snoozed for {duration_text}")
        return json.dumps({"state": "snoozed", "snooze_seconds": parsed.snooze_seconds})

    if parsed.action == "ignore":
        state.current_state = State.CLOSED
        _slack.send_reply(f"Ignoring `{agent_id}`. I won't ping you about this PR again.")
        logger.info(f"{agent_id}: ignored → closed")
        return json.dumps({"state": "closed", "terminal": True})

    if parsed.action == "wait_ci":
        state.current_state = State.WAITING_FOR_CI
        _slack.send_reply("I'll ping you when CI reports back.")
        logger.info(f"{agent_id}: waiting for CI")
        return json.dumps({"state": "waiting_for_ci"})

    if parsed.action == "wait_reviews":
        state.current_state = State.WAITING_FOR_REVIEW_COUNT
        state.waiting_for_review_count = parsed.review_count
        _slack.send_reply(f"I'll ping you when you have {parsed.review_count} review(s).")
        logger.info(f"{agent_id}: waiting for {parsed.review_count} review(s)")
        return json.dumps({
            "state": "waiting_for_review_count",
            "review_count": parsed.review_count,
        })

    # Unknown response
    _slack.send_error_reply()
    return json.dumps({"state": state.current_state.value, "parse_error": True})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_state(agent_id: str, payload: str) -> PRAgentState | None:
    """Reconstruct agent state from the event payload.

    In the full implementation this replays the Norns event log.
    For now the payload carries the current state snapshot.
    """
    try:
        data = json.loads(payload)
        role = UserRole(data.get("user_role", "author"))
        state = PRAgentState.from_agent_id(agent_id, role)
        state.current_state = State(data.get("current_state", state.current_state.value))
        state.waiting_for_review_count = data.get("waiting_for_review_count")
        state.last_known_pr_state = data.get("last_known_pr_state", {})
        return state
    except Exception as e:
        logger.error(f"Failed to load state for {agent_id}: {e}")
        return None


def _fetch_snapshot(state: PRAgentState) -> dict:
    """Fetch a fresh PR snapshot from GitHub and cache it in state."""
    snapshot = _github.get_pr_snapshot(state.owner, state.repo, state.number)
    state.last_known_pr_state = snapshot
    return snapshot


def _ping_user(state: PRAgentState, reason: str, snapshot: dict | None = None):
    """Send a Slack DM ping for this PR."""
    if snapshot is None:
        snapshot = _fetch_snapshot(state)

    state.pinged_at = datetime.now(timezone.utc)

    _slack.send_ping(
        agent_id=state.agent_id,
        pr_title=snapshot.get("title", f"{state.owner}/{state.repo}#{state.number}"),
        pr_url=snapshot.get("url", f"https://github.com/{state.owner}/{state.repo}/pull/{state.number}"),
        reason=reason,
        ci_state=snapshot.get("ci_state", "unknown"),
        review_count=snapshot.get("review_count", 0),
        mergeable=snapshot.get("mergeable"),
    )


def _set_staleness_timer(agent_id: str, state: PRAgentState):
    """Set (or reset) the staleness timer via Norns."""
    if state.user_role == UserRole.AUTHOR:
        hours = _config.rules.author_staleness_hours
    else:
        hours = _config.rules.reviewer_staleness_hours

    seconds = hours * 3600
    # TODO: norns.set_timer(agent_id, "staleness", seconds)
    logger.info(f"{agent_id}: staleness timer set for {hours}h")


def _extract_ci_conclusion(event_type: str, event_data: dict) -> str | None:
    """Extract the CI conclusion from a check_run or status event."""
    if event_type == "check_run":
        check_run = event_data.get("check_run", {})
        conclusion = check_run.get("conclusion")
        return conclusion  # "success", "failure", "neutral", etc.

    if event_type == "status":
        return event_data.get("state")  # "success", "failure", "pending", "error"

    return None


def _format_duration(seconds: int) -> str:
    """Format seconds into a human-readable duration."""
    if seconds >= 86400:
        days = seconds // 86400
        return f"{days}d"
    hours = seconds // 3600
    return f"{hours}h"


# Export all tools for worker registration
all_tools = [handle_github_event, handle_staleness_timer, handle_slack_response]
