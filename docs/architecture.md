# Architecture

## Process model

Hermod runs as three cooperating processes (plus the Norns runtime):

```
┌─────────────────────────────────────────────────────────────────────┐
│                          GitHub                                     │
│  (webhooks: PR events, reviews, CI status)                         │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ POST /webhooks/github
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                    hermod-server                              │
│  FastAPI app (server.py)                                     │
│                                                              │
│  • /webhooks/github      → validate, route, forward to Norns │
│  • /webhooks/slack/events → parse DM responses, forward      │
│  • /webhooks/slack/commands → /hermod list|debug|ignore      │
└──────────────────────┬───────────────────────────────────────┘
                       │ norns.send_event(agent_id, event)
                       ▼
┌──────────────────────────────────────────────────────────────┐
│                    Norns Runtime                              │
│  (Elixir/OTP — runs separately)                              │
│                                                              │
│  • Event log (Postgres)                                      │
│  • Durable timers                                            │
│  • Agent process management                                  │
│  • Tool invocation dispatch                                  │
└──────────────────────┬───────────────────────────────────────┘
                       │ tool tasks via WebSocket
                       ▼
┌──────────────────────────────────────────────────────────────┐
│                    hermod-worker                              │
│  Norns Python worker (worker.py)                             │
│                                                              │
│  Tools:                                                      │
│  • handle_github_event    → state transitions from webhooks  │
│  • handle_staleness_timer → re-evaluate + ping if stale      │
│  • handle_slack_response  → parse reply, update state        │
│                                                              │
│  Side effects:                                               │
│  • Slack DMs (via slack_client.py)                           │
│  • GitHub API queries (via github_client.py)                 │
│  • Timer management (via Norns SDK)                          │
└──────────────────────────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────┐
│                    Slack                                      │
│  DMs to user, slash command responses                        │
└──────────────────────────────────────────────────────────────┘
```

## Agent identity

Each PR gets exactly one agent. The ID is deterministic:

```
pr:{owner}/{repo}#{number}
```

This means the same PR always maps to the same agent, regardless of which event arrives first. When a webhook comes in for PR #42 in nornscode/norns, it always resolves to `pr:nornscode/norns#42`.

## State machine

```
                    ┌──────────────────┐
        PR opened   │ watching_as_     │  review requested
     (user=author)──│ author           │──(user=reviewer)
                    │                  │
                    └───────┬──────────┘
                            │
              staleness     │     CI failure
              timer fires   │     (immediate)
                            ▼
                    ┌──────────────────┐
                    │ pinged_user      │
                    │                  │
                    └───┬──┬──┬──┬─────┘
                        │  │  │  │
          "snooze Nd"───┘  │  │  └───"wake me when N reviews"
                           │  │
             "ignore"──────┘  └──────"wait until CI green"
                  │
                  ▼               ▼                    ▼
          ┌──────────┐   ┌───────────────┐   ┌────────────────────┐
          │ snoozed  │   │ waiting_for_  │   │ waiting_for_       │
          │          │   │ ci            │   │ review_count       │
          └────┬─────┘   └───────┬───────┘   └────────┬───────────┘
               │                 │                     │
          timer fires      CI reports back       threshold reached
               │                 │                     │
               └────────►  pinged_user  ◄──────────────┘

          PR closed/merged (from any state) ──► closed (terminal)
```

Seven states, all defined in `state.py`. Transitions are implemented in `agent.py` across the three tool handlers.

## Module responsibilities

| Module | Responsibility | External deps |
|--------|---------------|---------------|
| `config.py` | Load `config.yaml`, interpolate `${ENV_VARS}` | pyyaml |
| `state.py` | `PRAgentState` dataclass, `State` enum, `make_agent_id()` | none |
| `github_client.py` | Fetch PR state, reviews, CI status from GitHub REST API | httpx |
| `slack_client.py` | Send DMs, parse responses (regex pattern matching) | slack-sdk |
| `webhooks.py` | Route webhook events → agent IDs, filter by configured repos | none |
| `agent.py` | State machine transitions as Norns tools, side effect dispatch | norns-sdk |
| `server.py` | HTTP endpoints (GitHub webhooks, Slack events, slash commands) | fastapi |
| `worker.py` | Norns worker entrypoint, wires up clients, registers agent | norns-sdk |

## Data flow for a typical ping

1. 48 hours pass with no review activity on PR #42.
2. Norns fires the staleness timer → delivers `timer_fired` event to agent `pr:nornscode/norns#42`.
3. Worker receives the event, calls `handle_staleness_timer`.
4. Tool fetches fresh PR state from GitHub API (reviews, CI, mergeable).
5. No reviews found → transition to `pinged_user`.
6. Tool sends Slack DM to the configured user with PR context and response options.
7. User replies "snooze 1d" in the DM thread.
8. Slack Events API delivers the message to hermod-server.
9. Server resolves the thread to agent ID, calls `norns.send_event`.
10. Worker receives the event, calls `handle_slack_response`.
11. Response parsed as snooze → transition to `snoozed`, set 24h timer.
12. Agent sleeps. Worker can be restarted. Timer survives.
