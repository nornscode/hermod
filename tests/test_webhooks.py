"""Tests for webhook event routing."""

from hermod.webhooks import route_event

REPOS = ["nornscode/norns", "nornscode/mimir"]
USER = "amackera"


def _pr_payload(owner, repo, number, action, author="amackera", **extra):
    payload = {
        "action": action,
        "pull_request": {
            "number": number,
            "user": {"login": author},
        },
        "repository": {"full_name": f"{owner}/{repo}"},
        **extra,
    }
    return payload


def test_opened_by_user():
    payload = _pr_payload("nornscode", "norns", 42, "opened")
    result = route_event("pull_request", payload, REPOS, USER)
    assert result is not None
    agent_id, event_type, _ = result
    assert agent_id == "pr:nornscode/norns#42"
    assert event_type == "pull_request"


def test_opened_by_other_user():
    payload = _pr_payload("nornscode", "norns", 42, "opened", author="someone-else")
    result = route_event("pull_request", payload, REPOS, USER)
    assert result is None


def test_unconfigured_repo():
    payload = _pr_payload("other", "repo", 1, "opened")
    result = route_event("pull_request", payload, REPOS, USER)
    assert result is None


def test_review_requested_for_user():
    payload = _pr_payload("nornscode", "norns", 10, "review_requested", author="other")
    payload["requested_reviewer"] = {"login": "amackera"}
    result = route_event("pull_request", payload, REPOS, USER)
    assert result is not None
    assert result[0] == "pr:nornscode/norns#10"


def test_closed_event():
    payload = _pr_payload("nornscode", "norns", 42, "closed")
    result = route_event("pull_request", payload, REPOS, USER)
    assert result is not None
    assert result[0] == "pr:nornscode/norns#42"


def test_review_submitted():
    payload = {
        "action": "submitted",
        "pull_request": {"number": 42},
        "review": {"state": "approved"},
        "repository": {"full_name": "nornscode/norns"},
    }
    result = route_event("pull_request_review", payload, REPOS, USER)
    assert result is not None
    assert result[0] == "pr:nornscode/norns#42"


def test_check_run():
    payload = {
        "action": "completed",
        "check_run": {
            "conclusion": "failure",
            "pull_requests": [{"number": 42}],
        },
        "repository": {"full_name": "nornscode/norns"},
    }
    result = route_event("check_run", payload, REPOS, USER)
    assert result is not None
    assert result[0] == "pr:nornscode/norns#42"


def test_unhandled_event_type():
    payload = {"repository": {"full_name": "nornscode/norns"}}
    result = route_event("issues", payload, REPOS, USER)
    assert result is None
