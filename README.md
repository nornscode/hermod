# Hermod

GitHub PR shepherd agent. Watches your repos, tracks PRs you're involved in, and DMs you on Slack when something needs your attention.

Second reference implementation of [Norns](https://github.com/nornscode/norns), built to exercise long-sleep, event-driven, human-in-the-loop durable execution. Where [Mimir](https://github.com/nornscode/mimir) tests stateless Q&A, Hermod tests the hard parts: agents that sleep for days, wake on external events, interact with humans, and survive restarts without losing state.

## Docs

- **[Background](docs/background.md)** - why Hermod exists, how it relates to Norns, how the scaffold was built
- **[Architecture](docs/architecture.md)** - process model, state machine, module responsibilities, data flow
- **[Next Steps](docs/next-steps.md)** - phased plan from Norns integration through acceptance demo

## Architecture

Three processes:

1. **`hermod-server`** - FastAPI app receiving GitHub webhooks and Slack events, routing them to Norns
2. **`hermod-worker`** - Norns worker running the PR agent logic (state machine, Slack DMs, GitHub API queries)
3. **Norns runtime** - Elixir process handling durable execution (runs separately)

## Setup

```bash
uv sync
cp config.yaml config.local.yaml  # edit with your values

# Set environment variables
export HERMOD_GITHUB_TOKEN=ghp_...
export HERMOD_GITHUB_WEBHOOK_SECRET=...
export HERMOD_SLACK_BOT_TOKEN=xoxb-...
export HERMOD_SLACK_SIGNING_SECRET=...
export HERMOD_NORNS_API_KEY=nrn_...
```

## Running

```bash
# Terminal 1: webhook/Slack receiver
hermod-server

# Terminal 2: Norns worker
hermod-worker
```

## Slash Commands

- `/hermod list` - active PR agents and their states
- `/hermod debug pr:owner/repo#N` - show event log
- `/hermod ignore pr:owner/repo#N` - stop tracking a PR

## Tests

```bash
uv run pytest
```

## Status

Scaffold complete. State machine implemented, webhook routing tested, Slack parsing tested. Norns runtime integration points are stubbed with `TODO` markers. See [Next Steps](docs/next-steps.md) for the path to v0.1.
