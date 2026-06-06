"""HTTP server — receives GitHub webhooks and Slack events, forwards to Norns."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

import uvicorn
from fastapi import FastAPI, Header, Request, Response

from hermod.config import load_config
from hermod.slack_client import SlackClient, parse_response
from hermod.webhooks import route_event, verify_signature

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("hermod.server")

app = FastAPI(title="Hermod", version="0.1.0")

# Loaded at startup
_config = None
_slack: SlackClient | None = None

# Track known agent IDs (agents that have been spawned).
# In production this would query Norns; for now, an in-memory set.
_known_agents: set[str] = set()


@app.on_event("startup")
def startup():
    global _config, _slack
    _config = load_config()
    _slack = SlackClient(
        bot_token=_config.slack.bot_token,
        user_id=_config.slack.user_id,
    )
    logger.info(
        f"Hermod server started — watching {len(_config.github.repos)} repos "
        f"for user '{_config.github.user}'"
    )


# ---------------------------------------------------------------------------
# GitHub webhook endpoint
# ---------------------------------------------------------------------------


@app.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
):
    """Receive a GitHub webhook, validate it, route to the correct PR agent."""
    body = await request.body()

    # Verify signature
    if _config.github.webhook_secret and x_hub_signature_256:
        if not verify_signature(body, x_hub_signature_256, _config.github.webhook_secret):
            logger.warning("Invalid webhook signature")
            return Response(status_code=401, content="Invalid signature")

    if x_github_event is None:
        return Response(status_code=400, content="Missing X-GitHub-Event header")

    payload = json.loads(body)

    result = route_event(
        event_type=x_github_event,
        payload=payload,
        configured_repos=_config.github.repos,
        configured_user=_config.github.user,
    )

    if result is None:
        logger.debug(f"Dropped {x_github_event} event (not relevant)")
        return {"status": "ignored"}

    agent_id, event_type, event_payload = result

    # Determine if this is a spawn event or a forward-to-existing event
    action = payload.get("action", "")
    is_spawn = (
        (x_github_event == "pull_request" and action == "opened")
        or (x_github_event == "pull_request" and action == "review_requested")
    )

    if not is_spawn and agent_id not in _known_agents:
        logger.debug(f"No agent for {agent_id}, dropping {x_github_event}.{action}")
        return {"status": "ignored", "reason": "no_agent"}

    # Forward to Norns
    # TODO: norns.send_event(agent_id, {"event_type": event_type, **event_payload})
    _known_agents.add(agent_id)
    logger.info(f"Forwarded {x_github_event}.{action} → {agent_id}")

    return {"status": "forwarded", "agent_id": agent_id}


# ---------------------------------------------------------------------------
# Slack events endpoint
# ---------------------------------------------------------------------------


@app.post("/webhooks/slack/events")
async def slack_events(request: Request):
    """Handle Slack Events API callbacks (DM responses from the user)."""
    body = await request.body()

    # Verify Slack signature
    if _config.slack.signing_secret:
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        sig = request.headers.get("X-Slack-Signature", "")
        if not _verify_slack_signature(body, timestamp, sig, _config.slack.signing_secret):
            return Response(status_code=401, content="Invalid signature")

    payload = json.loads(body)

    # Handle Slack URL verification challenge
    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}

    # Handle message events (DM responses)
    if payload.get("type") == "event_callback":
        event = payload.get("event", {})
        if event.get("type") == "message" and not event.get("bot_id"):
            text = event.get("text", "").strip()
            user = event.get("user", "")

            # Only handle messages from the configured user
            if user == _config.slack.user_id and text:
                logger.info(f"Slack response from user: {text!r}")
                # TODO: determine which agent_id this response is for
                # (from thread context or by asking the user)
                # Then: norns.send_event(agent_id, {"type": "slack_response", "text": text})

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Slack slash commands
# ---------------------------------------------------------------------------


@app.post("/webhooks/slack/commands")
async def slack_commands(request: Request):
    """Handle /hermod slash commands."""
    form = await request.form()
    command = form.get("command", "")
    text = (form.get("text", "") or "").strip()
    user_id = form.get("user_id", "")

    if command != "/hermod":
        return {"response_type": "ephemeral", "text": f"Unknown command: {command}"}

    parts = text.split(maxsplit=1)
    subcommand = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    if subcommand == "list":
        return _handle_list()

    if subcommand == "debug" and args:
        return _handle_debug(args.strip())

    if subcommand == "ignore" and args:
        return _handle_ignore(args.strip())

    return {
        "response_type": "ephemeral",
        "text": (
            "Usage:\n"
            "  `/hermod list` — list active PR agents\n"
            "  `/hermod debug pr:owner/repo#N` — show event log\n"
            "  `/hermod ignore pr:owner/repo#N` — stop tracking a PR"
        ),
    }


def _handle_list() -> dict:
    """List all active PR agents."""
    if not _known_agents:
        return {"response_type": "ephemeral", "text": "No active PR agents."}

    lines = []
    for agent_id in sorted(_known_agents):
        # TODO: query Norns for current state
        lines.append(f"`{agent_id}`  state: unknown")

    return {"response_type": "ephemeral", "text": "\n".join(lines)}


def _handle_debug(agent_id: str) -> dict:
    """Show the event log for a specific agent."""
    if agent_id not in _known_agents:
        return {"response_type": "ephemeral", "text": f"No agent found: `{agent_id}`"}

    # TODO: query Norns event log for this agent_id
    return {
        "response_type": "ephemeral",
        "text": f"Event log for `{agent_id}` (not yet connected to Norns runtime)",
    }


def _handle_ignore(agent_id: str) -> dict:
    """Force-terminate an agent."""
    if agent_id not in _known_agents:
        return {"response_type": "ephemeral", "text": f"No agent found: `{agent_id}`"}

    # TODO: send terminate event to Norns
    _known_agents.discard(agent_id)
    return {"response_type": "ephemeral", "text": f"Ignored `{agent_id}`. Agent terminated."}


def _verify_slack_signature(body: bytes, timestamp: str, signature: str, secret: str) -> bool:
    """Verify Slack request signature."""
    if not timestamp or not signature:
        return False

    # Check timestamp freshness (5 minutes)
    if abs(time.time() - int(timestamp)) > 300:
        return False

    sig_basestring = f"v0:{timestamp}:{body.decode()}"
    computed = "v0=" + hmac.new(
        secret.encode(), sig_basestring.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


def main():
    """Entry point for hermod-server."""
    uvicorn.run("hermod.server:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
