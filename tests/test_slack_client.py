"""Tests for Slack response parsing."""

from hermod.slack_client import parse_response


def test_snooze_days():
    r = parse_response("snooze 2d")
    assert r.action == "snooze"
    assert r.snooze_seconds == 2 * 86400


def test_snooze_hours():
    r = parse_response("snooze 4h")
    assert r.action == "snooze"
    assert r.snooze_seconds == 4 * 3600


def test_snooze_case_insensitive():
    r = parse_response("  Snooze 1D  ")
    assert r.action == "snooze"
    assert r.snooze_seconds == 86400


def test_ignore():
    r = parse_response("ignore")
    assert r.action == "ignore"


def test_ignore_case():
    r = parse_response("  IGNORE  ")
    assert r.action == "ignore"


def test_wait_ci():
    r = parse_response("wait until CI green")
    assert r.action == "wait_ci"


def test_wait_reviews_singular():
    r = parse_response("wake me when 1 review")
    assert r.action == "wait_reviews"
    assert r.review_count == 1


def test_wait_reviews_plural():
    r = parse_response("wake me when 3 reviews")
    assert r.action == "wait_reviews"
    assert r.review_count == 3


def test_unknown():
    r = parse_response("do something else")
    assert r.action == "unknown"


def test_empty():
    r = parse_response("")
    assert r.action == "unknown"
