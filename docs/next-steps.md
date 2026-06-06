# Next Steps

## Where the scaffold stands

The project structure is complete. All module boundaries are established, the state machine is implemented, tests pass, and the code compiles. What's missing is the wiring to the Norns runtime and the external service setup (GitHub App/webhooks, Slack app).

Every integration point is marked with a `TODO` comment in the source. Here they are, in the order they should be tackled.

## Phase 1: Norns integration

This is the critical path. Everything else works without Norns, but without Norns there's no durability — and durability is the whole point.

### 1.1 — `send_event` in the server

**Files:** `server.py`

The server currently resolves webhook events to agent IDs and logs them. It needs to actually call `norns.send_event(agent_id, event)` to deliver events to the runtime.

This requires:
- Initializing a `NornsClient` in the server's startup hook.
- Calling `send_event` for each routed webhook event and Slack response.
- Handling the case where `send_event` creates a new agent process vs. waking an existing one. The Norns runtime handles this internally, but the server needs to know the API shape.

**Open question:** The current `NornsClient` in the SDK has `send_message` (for conversational agents) but not a generic `send_event` for non-conversational use. Hermod's agents aren't conversational — they receive structured events, not chat messages. This may require a small SDK addition or a creative use of the existing message API (e.g., sending the event as a JSON-encoded message with a known prefix).

### 1.2 — Durable timers

**Files:** `agent.py` (the `_set_staleness_timer` helper and snooze handling)

The agent needs to call `norns.set_timer(agent_id, "staleness", seconds)` when:
- A new watching state is entered (staleness timer).
- The user replies "snooze Nd/Nh" (snooze timer).
- A snoozed agent wakes up and re-enters watching (reset staleness timer).

**Open question:** Does the Norns SDK expose `set_timer` from within a tool handler? The tool runs inside the worker, which has a WebSocket connection to the runtime. Timer management might need to go through the same channel, or it might need a separate REST call. Check the Norns runtime API.

### 1.3 — State reconstruction from event log

**Files:** `agent.py` (`_load_state`)

Currently `_load_state` deserializes state from the event payload. In the real implementation, the Norns runtime replays the event log to reconstruct state. This means:
- Each tool invocation result is persisted as an event.
- On wake, the runtime replays all events for that agent to rebuild `PRAgentState`.
- The tool handlers need to return state diffs (or full state snapshots) that the replay can consume.

This is the deepest Norns integration point. It determines how state is encoded in events and how the replay function works. Look at how Mimir handles this — Mimir uses the conversational model where the event log is the message history. Hermod needs a different pattern since its "messages" are structured events, not chat.

### 1.4 — Event log query for `/hermod debug`

**Files:** `server.py` (`_handle_debug`)

The `/hermod debug pr:owner/repo#N` slash command should return the last 20 events from the Norns event log for that agent. This requires:
- Resolving the agent_id string to a Norns agent ID (integer).
- Calling `norns_client.get_events(run_id)` or an equivalent agent-level event query.
- Formatting the events for Slack display.

**Open question:** The current SDK has `get_events(run_id)` scoped to a single run. Hermod needs events across all runs for an agent (the whole lifecycle). This may need a new SDK method or a direct Norns API call.

## Phase 2: External service setup

### 2.1 — GitHub webhook configuration

**Option A: Personal Access Token + manual webhook setup.**
- Create a fine-grained PAT with read access to the target repos.
- Manually add a webhook to each repo pointing at `https://<hermod-host>/webhooks/github`.
- Subscribe to: `pull_request`, `pull_request_review`, `check_run`, `status`.
- Set the webhook secret to match `HERMOD_GITHUB_WEBHOOK_SECRET`.

**Option B: GitHub App.**
- Register a GitHub App with the same event subscriptions.
- Install it on the target repos.
- More setup upfront, but handles multi-repo webhook management automatically.
- Also provides a higher API rate limit (5,000 → 15,000 req/hr).

Recommendation: start with Option A for speed. Switch to Option B if managing webhooks across repos gets annoying.

### 2.2 — Slack app setup

- Create a Slack app at api.slack.com.
- Bot scopes: `chat:write`, `im:history`, `im:write`, `commands`.
- Event subscriptions: `message.im` (to receive DM replies).
- Slash command: `/hermod` pointing at `https://<hermod-host>/webhooks/slack/commands`.
- Events endpoint: `https://<hermod-host>/webhooks/slack/events`.
- For development: use Socket Mode (no public URL needed) or ngrok.

### 2.3 — Deployment

The server needs a public URL for webhooks. Options:
- **ngrok** for development.
- **Fly.io** for production (the Mimir agent uses this).
- A `Dockerfile` should be straightforward — the server and worker can run as separate processes in the same container or as separate Fly machines.

## Phase 3: Testing the durability story

This is the acceptance demo from the spec. It's not just testing — it's the content for the launch writeup.

### 3.1 — Happy path

1. Open a PR on a configured repo.
2. Verify the agent spawns (`/hermod list`).
3. Wait for the staleness timer (set threshold to 2 minutes for demo).
4. Receive Slack DM. Verify it contains PR title, URL, CI status, review count.
5. Reply "snooze 1m" (or whatever the minimum is for demo).
6. Verify the agent goes quiet and wakes up after the snooze.

### 3.2 — Durability demo

1. With a snoozed agent, kill the Python worker process.
2. Wait. Restart the worker.
3. Verify the snooze timer still fires on schedule.
4. Run `/hermod debug` and show the event log — the restart should be visible in the timestamps (gap where the worker was down) but no state is lost.

### 3.3 — CI failure path

1. Push a commit that breaks CI on a tracked PR.
2. Verify immediate Slack DM (no waiting for staleness timer).
3. Reply "wait until CI green".
4. Fix CI. Push again.
5. Verify Slack DM when CI goes green.

### 3.4 — Full lifecycle

1. Open PR → agent spawns.
2. Get pinged → snooze.
3. Get pinged again → "wake me when 1 review".
4. Submit a review (from another account or ask a collaborator).
5. Get pinged → "ignore".
6. Verify agent terminates, gone from `/hermod list`.

## Phase 4: Cleanup before launch

- [ ] Remove the in-memory `_known_agents` set from `server.py` — replace with Norns agent queries.
- [ ] Add proper error handling for Norns SDK calls (timeouts, connection failures).
- [ ] Add structured logging (JSON) for production.
- [ ] Write the Slack thread → agent_id resolution logic (currently a TODO in the Slack events handler).
- [ ] Add a health check endpoint (`GET /health`).
- [ ] Pin dependency versions in `pyproject.toml`.
- [ ] Write a `Dockerfile`.

## Out of scope (from the spec, repeated here as a reminder)

Do not build these, even if they seem obvious:

- Multi-user support
- Auto-nudging reviewers
- Auto-merging or any GitHub write actions
- LLM-powered triage
- Per-repo custom rules
- Web UI
- Non-GitHub providers
- Non-Slack notification channels
- Digest/summary mode
- Natural language response parsing
