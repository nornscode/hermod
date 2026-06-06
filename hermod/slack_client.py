"""Slack client — sends DMs and parses user responses."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from slack_sdk import WebClient

logger = logging.getLogger("hermod.slack")


@dataclass
class UserResponse:
    """Parsed user response from a Slack DM."""

    action: str  # "snooze", "ignore", "wait_ci", "wait_reviews", "unknown"
    snooze_seconds: Optional[int] = None
    review_count: Optional[int] = None
    raw_text: str = ""


# Response patterns (case-insensitive)
_SNOOZE_RE = re.compile(r"snooze\s+(\d+)\s*(d|h)", re.IGNORECASE)
_IGNORE_RE = re.compile(r"^ignore$", re.IGNORECASE)
_WAIT_CI_RE = re.compile(r"wait\s+until\s+ci\s+green", re.IGNORECASE)
_WAIT_REVIEWS_RE = re.compile(r"wake\s+me\s+when\s+(\d+)\s+reviews?", re.IGNORECASE)


def parse_response(text: str) -> UserResponse:
    """Parse a user's DM reply into a structured response."""
    text = text.strip()

    m = _SNOOZE_RE.search(text)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        seconds = amount * 3600 if unit == "h" else amount * 86400
        return UserResponse(action="snooze", snooze_seconds=seconds, raw_text=text)

    if _IGNORE_RE.match(text):
        return UserResponse(action="ignore", raw_text=text)

    if _WAIT_CI_RE.search(text):
        return UserResponse(action="wait_ci", raw_text=text)

    m = _WAIT_REVIEWS_RE.search(text)
    if m:
        count = int(m.group(1))
        return UserResponse(action="wait_reviews", review_count=count, raw_text=text)

    return UserResponse(action="unknown", raw_text=text)


class SlackClient:
    """Sends DMs to the configured user and handles responses."""

    def __init__(self, bot_token: str, user_id: str):
        self._client = WebClient(token=bot_token)
        self._user_id = user_id
        self._dm_channel: Optional[str] = None

    def _ensure_dm_channel(self) -> str:
        """Open (or reuse) a DM channel with the configured user."""
        if self._dm_channel:
            return self._dm_channel

        resp = self._client.conversations_open(users=[self._user_id])
        self._dm_channel = resp["channel"]["id"]
        return self._dm_channel

    def send_ping(
        self,
        agent_id: str,
        pr_title: str,
        pr_url: str,
        reason: str,
        ci_state: str,
        review_count: int,
        mergeable: bool | None,
    ) -> str:
        """Send a PR ping DM. Returns the message timestamp (for threading)."""
        channel = self._ensure_dm_channel()

        mergeable_text = "Mergeable" if mergeable else "Not mergeable" if mergeable is False else "Unknown"

        text = (
            f"*<{pr_url}|{pr_title}>*\n"
            f"`{agent_id}`\n\n"
            f"{reason}\n\n"
            f"CI: {ci_state} · Reviews: {review_count} · {mergeable_text}\n\n"
            f"Reply with:\n"
            f"  `snooze 1d`           wait a day, then check again\n"
            f"  `snooze 4h`           wait 4 hours\n"
            f"  `ignore`              stop tracking this PR\n"
            f"  `wait until CI green`\n"
            f"  `wake me when 1 review`"
        )

        resp = self._client.chat_postMessage(channel=channel, text=text)
        ts = resp["ts"]
        logger.info(f"Sent ping for {agent_id} (ts={ts})")
        return ts

    def send_reply(self, text: str, thread_ts: Optional[str] = None):
        """Send a message (optionally threaded) to the user's DM channel."""
        channel = self._ensure_dm_channel()
        self._client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)

    def send_error_reply(self, thread_ts: Optional[str] = None):
        """Send the 'I didn't understand' help message."""
        self.send_reply(
            "I didn't understand. Try: `snooze 1d`, `ignore`, "
            "`wait until CI green`, or `wake me when 1 review`.",
            thread_ts=thread_ts,
        )
