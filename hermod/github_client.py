"""GitHub API client — fetches current PR state on demand."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("hermod.github")


class GitHubClient:
    """Thin wrapper around the GitHub REST API for PR state queries."""

    def __init__(self, token: str):
        self._client = httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15.0,
        )

    def close(self):
        self._client.close()

    def get_pr(self, owner: str, repo: str, number: int) -> dict:
        """Fetch the current state of a pull request."""
        resp = self._client.get(f"/repos/{owner}/{repo}/pulls/{number}")
        resp.raise_for_status()
        return resp.json()

    def get_pr_reviews(self, owner: str, repo: str, number: int) -> list[dict]:
        """Fetch all reviews for a pull request."""
        resp = self._client.get(f"/repos/{owner}/{repo}/pulls/{number}/reviews")
        resp.raise_for_status()
        return resp.json()

    def get_combined_status(self, owner: str, repo: str, ref: str) -> dict:
        """Fetch the combined commit status for a ref."""
        resp = self._client.get(f"/repos/{owner}/{repo}/commits/{ref}/status")
        resp.raise_for_status()
        return resp.json()

    def get_check_runs(self, owner: str, repo: str, ref: str) -> list[dict]:
        """Fetch check runs for a ref (Actions-style CI)."""
        resp = self._client.get(f"/repos/{owner}/{repo}/commits/{ref}/check-runs")
        resp.raise_for_status()
        return resp.json().get("check_runs", [])

    def get_pr_snapshot(self, owner: str, repo: str, number: int) -> dict:
        """Build a complete snapshot of PR state for Slack messages."""
        pr = self.get_pr(owner, repo, number)
        reviews = self.get_pr_reviews(owner, repo, number)
        head_sha = pr.get("head", {}).get("sha", "")

        # CI status — try check runs first, fall back to commit status
        ci_state = "unknown"
        if head_sha:
            try:
                check_runs = self.get_check_runs(owner, repo, head_sha)
                if check_runs:
                    conclusions = [cr.get("conclusion") for cr in check_runs if cr.get("conclusion")]
                    if all(c == "success" for c in conclusions) and conclusions:
                        ci_state = "green"
                    elif any(c == "failure" for c in conclusions):
                        ci_state = "red"
                    else:
                        ci_state = "pending"
                else:
                    status = self.get_combined_status(owner, repo, head_sha)
                    ci_state = status.get("state", "unknown")
            except Exception as e:
                logger.warning(f"Failed to fetch CI status: {e}")

        # Count substantive reviews (not just comments)
        review_states = [r.get("state") for r in reviews]
        approved_count = sum(1 for s in review_states if s == "APPROVED")
        changes_requested = sum(1 for s in review_states if s == "CHANGES_REQUESTED")

        return {
            "title": pr.get("title", ""),
            "url": pr.get("html_url", ""),
            "state": pr.get("state", ""),
            "mergeable": pr.get("mergeable"),
            "mergeable_state": pr.get("mergeable_state", ""),
            "ci_state": ci_state,
            "review_count": len(reviews),
            "approved_count": approved_count,
            "changes_requested": changes_requested,
            "head_sha": head_sha,
            "author": pr.get("user", {}).get("login", ""),
            "requested_reviewers": [
                r.get("login", "") for r in pr.get("requested_reviewers", [])
            ],
        }
