"""Configuration loader — reads config.yaml with env var interpolation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class GitHubConfig:
    user: str
    webhook_secret: str
    token: str
    repos: list[str]


@dataclass
class SlackConfig:
    bot_token: str
    signing_secret: str
    user_id: str


@dataclass
class NornsConfig:
    runtime_url: str
    api_key: str


@dataclass
class RulesConfig:
    author_staleness_hours: int = 48
    reviewer_staleness_hours: int = 24


@dataclass
class Config:
    github: GitHubConfig
    slack: SlackConfig
    norns: NornsConfig
    rules: RulesConfig


_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _interpolate(value: str) -> str:
    """Replace ${VAR} references with environment variable values."""
    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        env_val = os.environ.get(var_name)
        if env_val is None:
            raise ValueError(f"Environment variable {var_name} is not set")
        return env_val
    return _ENV_VAR_RE.sub(replacer, value)


def _interpolate_recursive(obj):
    """Recursively interpolate env vars in a parsed YAML structure."""
    if isinstance(obj, str):
        return _interpolate(obj)
    if isinstance(obj, dict):
        return {k: _interpolate_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate_recursive(item) for item in obj]
    return obj


def load_config(path: str | Path = "config.yaml") -> Config:
    """Load and validate configuration from a YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f)

    data = _interpolate_recursive(raw)

    gh = data["github"]
    sl = data["slack"]
    nr = data["norns"]
    ru = data.get("rules", {})

    return Config(
        github=GitHubConfig(
            user=gh["user"],
            webhook_secret=gh["webhook_secret"],
            token=gh["token"],
            repos=gh["repos"],
        ),
        slack=SlackConfig(
            bot_token=sl["bot_token"],
            signing_secret=sl["signing_secret"],
            user_id=sl["user_id"],
        ),
        norns=NornsConfig(
            runtime_url=nr["runtime_url"],
            api_key=nr["api_key"],
        ),
        rules=RulesConfig(
            author_staleness_hours=ru.get("author_staleness_hours", 48),
            reviewer_staleness_hours=ru.get("reviewer_staleness_hours", 24),
        ),
    )
