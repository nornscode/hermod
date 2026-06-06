"""Norns worker — connects to the runtime and handles PR agent events."""

from __future__ import annotations

import logging

from norns import Agent, Norns

from hermod import agent as agent_module
from hermod.agent import all_tools
from hermod.config import load_config
from hermod.github_client import GitHubClient
from hermod.slack_client import SlackClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("hermod.worker")

SYSTEM_PROMPT = """\
You are Hermod, a GitHub PR shepherd. You track pull requests for a single \
user, monitor staleness and CI status, and notify them via Slack when a PR \
needs attention.

You do not write code, review PRs, or take any action on GitHub. You only \
observe and notify. You follow the state machine exactly and use pattern \
matching for user responses — no natural language interpretation.

When handling events, update the agent state and trigger side effects \
(Slack messages, timer resets) as specified by the state machine.
"""


def main():
    config = load_config()

    # Initialize clients
    github = GitHubClient(token=config.github.token)
    slack = SlackClient(
        bot_token=config.slack.bot_token,
        user_id=config.slack.user_id,
    )

    # Wire up the agent module with its dependencies
    agent_module.init(github=github, slack=slack, norns_client=None, config=config)

    agent = Agent(
        name="hermod-pr-agent",
        model="claude-sonnet-4-20250514",
        system_prompt=SYSTEM_PROMPT,
        tools=all_tools,
        mode="task",
        max_steps=10,
        on_failure="retry_last_step",
    )

    norns = Norns(config.norns.runtime_url, api_key=config.norns.api_key)

    logger.info("Starting Hermod worker...")
    norns.run(agent)


if __name__ == "__main__":
    main()
