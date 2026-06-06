# Hermod

PR shepherd for GitHub. Tracks pull requests you're involved in, pings you on Slack when they go stale or CI breaks, and lets you snooze or set conditions from the DM thread.

Built on [Norns](https://github.com/nornscode/norns). Second reference agent after [Mimir](https://github.com/nornscode/norns-mimir-agent), designed to exercise the parts of durable execution that Mimir doesn't touch: long sleeps, event-driven wake-ups, and human-in-the-loop interaction that survives restarts.

## How it works

```
GitHub webhooks ──► hermod-server ──► Norns ──► hermod-worker
                                                    ├── GitHub API (read-only)
Slack DMs/commands ──────┘                          └── Slack DMs
```

Three processes:

1. **hermod-server** - FastAPI app. Receives GitHub webhooks and Slack events, forwards to Norns.
2. **hermod-worker** - Norns worker. Runs the state machine, queries GitHub, sends Slack DMs.
3. **Norns runtime** - handles durability, timers, event log. Runs separately.

Each PR gets one durable agent (`pr:owner/repo#42`). The agent sleeps between events. Kill the worker, redeploy, restart Norns - state reconstructs from the event log.

## State machine

```
opened (author) ──► watching ──► pinged_user ──► snoozed ──► (re-evaluate)
                        │              │
                   CI failure          ├──► waiting_for_ci
                   (immediate)         ├──► waiting_for_review_count
                                       └──► closed (ignore)

PR merged/closed from any state ──► closed
```

Seven states. Staleness timers (48h author, 24h reviewer) trigger pings. CI failures ping immediately. User responds in Slack with exact phrases: `snooze 1d`, `ignore`, `wait until CI green`, `wake me when 2 reviews`.

## Setup

```bash
uv sync
cp config.yaml config.local.yaml  # edit with your values
```

Environment variables:

```bash
export HERMOD_GITHUB_TOKEN=ghp_...
export HERMOD_GITHUB_WEBHOOK_SECRET=...
export HERMOD_SLACK_BOT_TOKEN=xoxb-...
export HERMOD_SLACK_SIGNING_SECRET=...
export HERMOD_NORNS_API_KEY=nrn_...
```

## Running

```bash
hermod-server   # terminal 1
hermod-worker   # terminal 2
```

## Slash commands

- `/hermod list` - active PR agents and their states
- `/hermod debug pr:owner/repo#N` - event log for an agent
- `/hermod ignore pr:owner/repo#N` - stop tracking a PR

## Status

Scaffold. State machine implemented, webhook routing and Slack parsing tested (21 tests pass). Norns integration points are stubbed. See [next steps](docs/next-steps.md).

## Docs

- [Background](docs/background.md) - why this exists, relationship to Norns
- [Architecture](docs/architecture.md) - process model, state machine, module map
- [Next steps](docs/next-steps.md) - integration plan, acceptance demo, cleanup checklist

## License

MIT
