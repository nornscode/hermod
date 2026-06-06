# Background

## Why Hermod exists

Hermod is the second reference implementation of [Norns](https://github.com/nornscode/norns), a BEAM-based durable execution runtime for AI agents.

The first reference implementation, [Mimir](https://github.com/nornscode/mimir), is a product knowledge assistant. It exercises stateless Q&A loops: a user asks a question, the agent searches memory and connected sources, and responds. The agent process is short-lived — it starts, does work, and finishes. Mimir proved that the Norns SDK works, that tool invocation is reliable, and that the worker ↔ runtime protocol holds up. But it didn't touch the hard parts of durable execution.

Hermod exists to exercise those hard parts:

- **Long sleeps.** A PR can sit for days or weeks. The agent process must survive that entire time, sleeping between events, without holding resources.
- **Event-driven wake-ups.** GitHub webhooks, Slack replies, and durable timers all wake the same agent. The runtime must route events to the right process and reconstruct state from the event log.
- **Human-in-the-loop.** The agent pings a human, then waits — potentially forever — for a response. It must handle the response correctly even if the worker process was restarted in between.
- **Durability across restarts.** Killing the Python worker, killing the Norns runtime, deploying new code — none of these should lose agent state. Snoozed agents must still fire on schedule after a restart.

These are the properties that make durable execution interesting, and they're the properties that are hardest to test without a real use case. Hermod is that use case.

## Why a PR shepherd

PRs are a natural fit for durable agents:

- They have a clear lifecycle (opened → reviewed → merged/closed) that maps to a state machine.
- They involve real waiting — for reviews, for CI, for the author to respond.
- Staleness is a genuine problem. PRs rot when nobody nudges.
- The interaction model (agent pings human, human responds with a command) is simple enough for v0.1 but representative of the broader pattern.
- The data is available via well-documented APIs (GitHub webhooks, REST API).

The name comes from Norse mythology. Hermod is the messenger of the gods — he carried messages between realms. Fitting for an agent that carries PR status between GitHub and Slack.

## How we got here

The build spec was written as a single document covering the full v0.1 scope: agent identity, state machine, Slack interaction, GitHub events, configuration, data model, and acceptance criteria. The spec also includes an explicit "out of scope" section to prevent scope creep.

From the spec, the project was scaffolded in one pass:

1. **`state.py`** — the `PRAgentState` dataclass and `State` enum, establishing the agent identity format (`pr:owner/repo#N`) and the seven states from the spec.
2. **`github_client.py`** — thin wrapper around the GitHub REST API, focused on fetching PR snapshots (title, reviews, CI status, mergeable state) for Slack messages.
3. **`slack_client.py`** — Slack DM formatting and exact-match response parsing. No NLP, no LLM — just regex against the five accepted phrases.
4. **`webhooks.py`** — event routing logic that determines which webhook events spawn new agents vs. forward to existing ones, with repo filtering from config.
5. **`agent.py`** — the core state machine as Norns tools. Three tools: `handle_github_event`, `handle_staleness_timer`, `handle_slack_response`. Each takes an `agent_id` and payload, reconstructs state, applies the transition, and produces side effects.
6. **`server.py`** — FastAPI app with endpoints for GitHub webhooks, Slack events, and slash commands.
7. **`worker.py`** — Norns worker entrypoint that wires up the clients and registers the agent.
8. **Tests** — covering state construction, response parsing, and webhook routing.

The scaffold is ~1,100 lines of Python. It compiles, the tests pass, and the module boundaries are clean. The Norns integration points are marked with `TODO` comments at the exact lines where `send_event`, `set_timer`, and event log queries need to go.
