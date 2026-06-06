"""GitHub webhook event routing — determines agent_id and forwards to Norns."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Optional

from hermod.state import make_agent_id

logger = logging.getLogger("hermod.webhooks")


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify a GitHub webhook HMAC-SHA256 signature."""
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def route_event(
    event_type: str,
    payload: dict,
    configured_repos: list[str],
    configured_user: str,
) -> Optional[tuple[str, str, dict]]:
    """Determine the agent_id for a webhook event.

    Returns (agent_id, event_type, payload) if this event should be forwarded
    to an agent, or None if it should be dropped.
    """
    # Extract repo full name
    repo_full = _extract_repo(event_type, payload)
    if repo_full is None:
        logger.debug(f"No repo in {event_type} event, dropping")
        return None

    if repo_full not in configured_repos:
        logger.debug(f"Repo {repo_full} not in config, dropping")
        return None

    owner, repo = repo_full.split("/", 1)

    # Pull request events
    if event_type == "pull_request":
        pr = payload.get("pull_request", {})
        number = pr.get("number")
        if number is None:
            return None

        action = payload.get("action", "")
        agent_id = make_agent_id(owner, repo, number)

        # Events that can spawn a NEW agent
        if action == "opened":
            pr_author = pr.get("user", {}).get("login", "")
            if pr_author.lower() == configured_user.lower():
                return (agent_id, event_type, payload)
            # Not our PR — don't spawn
            return None

        if action == "review_requested":
            requested = payload.get("requested_reviewer", {}).get("login", "")
            if requested.lower() == configured_user.lower():
                return (agent_id, event_type, payload)
            # Not requested for us — forward only if agent exists (caller checks)
            return (agent_id, event_type, payload)

        # All other PR actions: forward to existing agent only
        if action in ("closed", "reopened", "review_request_removed"):
            return (agent_id, event_type, payload)

        return None

    # Pull request review events
    if event_type == "pull_request_review":
        pr = payload.get("pull_request", {})
        number = pr.get("number")
        if number is None:
            return None
        agent_id = make_agent_id(owner, repo, number)
        return (agent_id, event_type, payload)

    # CI events — check_run and status
    if event_type == "check_run":
        # check_run events include pull_requests array
        check_run = payload.get("check_run", {})
        prs = check_run.get("pull_requests", [])
        if not prs:
            return None
        # Forward to all associated PR agents (usually just one)
        pr = prs[0]  # Take the first one
        number = pr.get("number")
        if number is None:
            return None
        agent_id = make_agent_id(owner, repo, number)
        return (agent_id, event_type, payload)

    if event_type == "status":
        # Status events don't directly reference a PR; we'd need to look up
        # which PRs have this commit as HEAD. For v0.1, skip unless we can
        # find the PR from context.
        # TODO: resolve commit SHA → PR number via GitHub API
        logger.debug("Status event received — PR resolution not yet implemented")
        return None

    logger.debug(f"Unhandled event type: {event_type}")
    return None


def _extract_repo(event_type: str, payload: dict) -> Optional[str]:
    """Extract the repository full name from a webhook payload."""
    repo = payload.get("repository", {})
    return repo.get("full_name")
