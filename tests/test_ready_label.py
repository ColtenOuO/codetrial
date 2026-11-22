#!/usr/bin/env python3
"""Contract tests for scripts/ready-label.py against a stub `gh`."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ready-label.py"
LABEL = "ready for maintainer review"

# Answers the graphql read from $PAGES, the label lookup from $LABEL_EXISTS,
# and records every write in $CALLS.
STUB = """#!/bin/sh
case "$*" in
*graphql*) [ "$PAGES" = fail ] && exit 1; cat "$PAGES_FILE" ;;
"api -X DELETE "*|*"labels[]="*) echo "$*" >> "$CALLS" ;;
*/labels/*) [ "$LABEL_EXISTS" = yes ] ;;
esac
"""


def thread(author, resolved=True):
    return {
        "isResolved": resolved,
        "comments": {"nodes": [{"author": {"login": author}}]},
    }


def run(name, conclusion="SUCCESS", status="COMPLETED", run_id=1):
    return {
        "__typename": "CheckRun",
        "databaseId": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
    }


def review(author, state="COMMENTED", on="old", threads=1, at="2026-01-01", bot=False):
    """A review by AUTHOR left on commit ON, opening THREADS threads."""
    return {
        "author": {"__typename": "Bot" if bot else "User", "login": author},
        "state": state,
        "submittedAt": at,
        "commit": {"oid": on},
        "comments": {"totalCount": threads},
    }


def pr(number=1, **over):
    """A pull request that is ready unless OVER says otherwise."""
    base = {
        "number": number,
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "author": {"login": "alice"},
        "labels": {"nodes": []},
        "checks": [run("gates (rust)"), run("check")],
        "threads": [thread("maintainer")],
        "reviews": [review("maintainer")],
        "headRefOid": "head",
    }
    base.update(over)
    checks = base.pop("checks")
    threads = base.pop("threads")
    reviews = base.pop("reviews")
    base["reviews"] = {
        "totalCount": base.pop("review_count", len(reviews)),
        "nodes": reviews,
    }
    base["commits"] = {
        "nodes": [
            {
                "commit": {
                    "statusCheckRollup": {
                        "contexts": {
                            "totalCount": base.pop("check_count", len(checks)),
                            "nodes": checks,
                        }
                    }
                }
            }
        ]
    }
    base["reviewThreads"] = {
        "totalCount": base.pop("thread_count", len(threads)),
        "nodes": threads,
    }
    return base


def labeled(**over):
    return pr(labels={"nodes": [{"name": LABEL}]}, **over)


def sweep(prs, *args, pages="ok", label_exists=True):
    """Run the script over PRS; return (status, stdout, recorded writes)."""
    with tempfile.TemporaryDirectory() as tmp:
        gh = Path(tmp, "gh")
        gh.write_text(STUB)
        gh.chmod(0o755)
        page = {"data": {"repository": {"pullRequests": {"nodes": prs}}}}
        Path(tmp, "pages.json").write_text(json.dumps(page))
        calls = Path(tmp, "calls")
        calls.touch()
        env = {
            **os.environ,
            "PATH": f"{tmp}{os.pathsep}{os.environ['PATH']}",
            "PAGES": pages,
            "PAGES_FILE": str(Path(tmp, "pages.json")),
            "LABEL_EXISTS": "yes" if label_exists else "no",
            "CALLS": str(calls),
        }
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args, "o/r"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode, result.stdout, calls.read_text().splitlines()


ADD = f"api repos/o/r/issues/1/labels -f labels[]={LABEL} --silent"
REMOVE = (
    "api -X DELETE repos/o/r/issues/1/labels/ready%20for%20maintainer%20review --silent"
)


class ReadyLabel(unittest.TestCase):
    def assert_adds(self, *prs):
        self.assertEqual(sweep(list(prs))[2], [ADD])

    def assert_leaves(self, *prs):
        self.assertEqual(sweep(list(prs))[2], [])

    def test_every_reviewer_thread_resolved_and_green_adds_the_label(self):
        status, out, calls = sweep([pr()])
        self.assertEqual(status, 0)
        self.assertEqual(calls, [ADD])
        self.assertIn("#1: add", out)

    def test_a_label_already_there_is_not_added_again(self):
        self.assert_leaves(labeled())

    def test_an_open_reviewer_thread_withholds_the_label(self):
        self.assert_leaves(pr(threads=[thread("bob"), thread("bob", False)]))

    def test_an_open_bot_thread_withholds_the_label(self):
        self.assert_leaves(pr(threads=[thread("bob"), thread("bot[bot]", False)]))

    def test_the_authors_own_open_thread_does_not_count(self):
        self.assert_adds(pr(threads=[thread("bob"), thread("alice", False)]))

    def test_only_the_authors_threads_and_reviews_are_not_a_review(self):
        self.assert_leaves(
            pr(threads=[thread("alice")], reviews=[review("alice", on="head")])
        )

    def test_no_review_at_all_withholds_the_label(self):
        self.assert_leaves(pr(threads=[], reviews=[]))

    def test_a_review_without_threads_answered_by_a_push_adds_the_label(self):
        self.assert_adds(
            pr(threads=[], reviews=[review("bob", "CHANGES_REQUESTED", threads=0)])
        )

    def test_a_review_on_this_head_without_a_thread_withholds_the_label(self):
        for state in ("APPROVED", "COMMENTED", "CHANGES_REQUESTED"):
            with self.subTest(state=state):
                reviews = [
                    review("bob"),
                    review("carol", state, "head", 0, "2026-02-01"),
                ]
                self.assert_leaves(pr(reviews=reviews))

    def test_a_new_review_takes_the_label_off(self):
        reviews = [review("bob"), review("bob", "APPROVED", "head", 0, "2026-02-01")]
        status, out, calls = sweep([labeled(reviews=reviews)])
        self.assertEqual((status, calls), (0, [REMOVE]))
        self.assertIn("reviewed by bob since the last push", out)

    def test_a_review_on_this_head_whose_threads_are_resolved_is_answered(self):
        self.assert_adds(pr(reviews=[review("bob", on="head", threads=2)]))

    def test_only_the_latest_review_counts(self):
        reviews = [
            review("bob", "APPROVED", "head", 0, "2026-01-01"),
            review("carol", on="head", at="2026-02-01"),
        ]
        self.assert_adds(pr(reviews=reviews))

    def test_a_bot_or_dismissed_review_on_this_head_does_not_withhold(self):
        for late in (
            review("bot", on="head", threads=0, at="2026-02-01", bot=True),
            review("bob", "DISMISSED", "head", 0, "2026-02-01"),
            review("bob", "PENDING", "head", 0, "2026-02-01"),
        ):
            with self.subTest(late=late):
                self.assert_adds(pr(reviews=[review("bob"), late]))

    def test_a_draft_conflict_or_red_check_withholds_the_label(self):
        for over in (
            {"isDraft": True},
            {"mergeable": "CONFLICTING"},
            {"checks": [run("gates (rust)"), run("lint", "FAILURE")]},
            {"checks": [run("gates (rust)"), run("lint", None, "IN_PROGRESS")]},
            {"checks": [run("gates (rust)", "ACTION_REQUIRED")]},
            {"checks": [run("check")]},
            {"checks": [run("gates (rust)", "SKIPPED")]},
            {"checks": []},
        ):
            with self.subTest(over=over):
                self.assert_leaves(pr(**over))

    def test_a_rerun_that_passed_replaces_the_failure(self):
        checks = [
            run("gates (rust)", "FAILURE", run_id=1),
            run("gates (rust)", run_id=2),
        ]
        self.assert_adds(pr(checks=checks))

    def test_a_skipped_check_beside_a_passing_gates_lane_is_green(self):
        self.assert_adds(pr(checks=[run("gates (rust)"), run("mutants", "SKIPPED")]))

    def test_a_pr_that_stopped_being_ready_loses_the_label(self):
        for over in (
            {"threads": [thread("bob", False)]},
            {"isDraft": True},
            {"mergeable": "CONFLICTING"},
            {"checks": [run("gates (rust)", None, "QUEUED")]},
        ):
            with self.subTest(over=over):
                status, out, calls = sweep([labeled(**over)])
                self.assertEqual((status, calls), (0, [REMOVE]))
                self.assertIn("#1: remove", out)

    def test_an_answer_not_read_in_full_leaves_the_label_alone(self):
        for over in (
            {"mergeable": "UNKNOWN"},
            {"thread_count": 101},
            {"check_count": 51},
            {"review_count": 101},
        ):
            with self.subTest(over=over):
                self.assert_leaves(pr(**over))
                self.assert_leaves(labeled(isDraft=True, **over))

    def test_dry_run_reports_without_writing(self):
        status, out, calls = sweep([pr()], "--dry-run", label_exists=False)
        self.assertEqual((status, calls), (0, []))
        self.assertIn("#1: add", out)

    def test_a_missing_label_or_api_error_writes_nothing(self):
        self.assertEqual(sweep([pr()], label_exists=False)[::2], (2, []))
        self.assertEqual(sweep([pr()], pages="fail")[::2], (2, []))


if __name__ == "__main__":
    unittest.main()
