#!/usr/bin/env python3
"""Keep the "ready for maintainer review" label on the open pull requests whose
author has answered every review thread, and off every other one.

A pull request is ready when it is not a draft, merges cleanly, has been
reviewed, every thread a reviewer opened is resolved, nobody reviewed its head
without leaving a thread, and its head's checks are green with a gates lane
among them. The label comes off as soon as one stops holding, so a push takes it
off while the checks run and a new review takes it off until the author answers
it. The threads and checks are read as the triage sweep reads them
(.claude/skills/codetrial-triage/pr-sweep.sh), so the label and a sweep agree
on whose move it is:

- A thread the author opened is a note to reviewers, not a finding left for the
  author, so it neither counts toward nor blocks the label. A bot's thread
  counts like a person's.
- An outdated thread that nobody resolved is still open: the push may have
  moved the line without answering it.
- Resolving is the author's answer, so it counts whoever pressed it. A changes
  request does not block either: the label is how the author says the
  requested work is done and the reviewer is owed a look.
- A person's latest review keeps the label off when it was left on the current
  head with no thread, whether it approves, requests changes or comments: there
  is nothing to resolve, so only a push answers it. A bot's review does not, or
  a bot reviewing every push would hold the label off for good.
- Only the latest run of each check name stands, since a rerun leaves the old
  one beside it. A skipped check is neither a pass nor a failure, but a head on
  which no gates lane passed was not tested, so it is not ready.

When the answer cannot be read in full (GitHub is still computing mergeability,
more than 100 threads or reviews, more than 50 checks) the label is left as it is, for the
next run to settle.

    ready-label.py [--dry-run] [REPO]

Prints one line per label it adds or removes. Exit 2 when the API fails or the
label does not exist in REPO, having written nothing.
"""

import argparse
import json
import subprocess
import sys
from urllib.parse import quote

LABEL = "ready for maintainer review"

QUERY = """
query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 50, after: $endCursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number isDraft mergeable headRefOid author { login }
        labels(first: 100) { nodes { name } }
        commits(last: 1) { nodes { commit {
          statusCheckRollup { contexts(first: 50) { totalCount nodes {
            __typename
            ... on CheckRun { databaseId name status conclusion }
            ... on StatusContext { context state } } } } } } }
        reviews(last: 100) { totalCount nodes {
          author { __typename login } state submittedAt
          commit { oid } comments { totalCount } } }
        reviewThreads(first: 100) { totalCount
          nodes { isResolved comments(first: 1) { nodes { author { login } } } } }
      }
    }
  }
}
"""

FAILED = {"FAILURE", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE"}


def check_state(ctx):
    """Name, kind and state (ok, skipped, pending, held, failing) of one check."""
    if ctx.get("__typename") == "CheckRun":
        conclusion = ctx.get("conclusion")
        if ctx.get("status") != "COMPLETED":
            state = "pending"
        elif conclusion == "ACTION_REQUIRED":
            state = "held"
        elif conclusion in FAILED:
            state = "failing"
        elif conclusion in ("SKIPPED", "NEUTRAL"):
            state = "skipped"
        else:
            state = "ok"
        return ctx.get("name"), "run", ctx.get("databaseId") or 0, state
    status = ctx.get("state")
    if status in ("PENDING", "EXPECTED"):
        state = "pending"
    elif status in ("FAILURE", "ERROR"):
        state = "failing"
    else:
        state = "ok"
    return ctx.get("context"), "status", 0, state


def latest_checks(contexts):
    """The latest state of each check name: a rerun supersedes the run before."""
    latest = {}
    for ctx in contexts:
        name, kind, run_id, state = check_state(ctx)
        key = (kind, name)
        if key not in latest or run_id >= latest[key][0]:
            latest[key] = (run_id, state)
    return {name: state for (_, name), (_, state) in latest.items()}


def verdict(pr):
    """("ready" | "not" | "unknown", reason) for one pull request."""
    rollup = (pr["commits"]["nodes"] or [{}])[0].get("commit", {})
    contexts = (rollup.get("statusCheckRollup") or {}).get("contexts") or {}
    threads = pr["reviewThreads"]
    if pr["mergeable"] == "UNKNOWN":
        return "unknown", "mergeability unknown"
    if threads["totalCount"] > 100:
        return "unknown", "more than 100 threads"
    if pr["reviews"]["totalCount"] > 100:
        return "unknown", "more than 100 reviews"
    if contexts.get("totalCount", 0) > 50:
        return "unknown", "more than 50 checks"
    if pr["isDraft"]:
        return "not", "draft"
    if pr["mergeable"] != "MERGEABLE":
        return "not", "conflicts"

    author = (pr.get("author") or {}).get("login")

    def opener(thread):
        first = thread["comments"]["nodes"]
        return ((first[0].get("author") if first else None) or {}).get("login")

    reviewer = [t for t in threads["nodes"] if opener(t) != author]
    reviews = sorted(
        (
            r
            for r in pr["reviews"]["nodes"]
            if (r.get("author") or {}).get("__typename") != "Bot"
            and (r.get("author") or {}).get("login") != author
            and r["state"] in ("COMMENTED", "CHANGES_REQUESTED", "APPROVED")
        ),
        key=lambda r: r["submittedAt"] or "",
    )
    if not reviewer and not reviews:
        return "not", "not reviewed"
    unresolved = sum(1 for t in reviewer if not t["isResolved"])
    if unresolved:
        return "not", f"{unresolved} open thread(s)"
    # A review left on this head with no thread of its own asked nothing the
    # author could resolve: the reviewer has looked at this code, and only a
    # push gives them something new to look at.
    if reviews:
        last = reviews[-1]
        if (last.get("commit") or {}).get("oid") == pr["headRefOid"] and not last[
            "comments"
        ]["totalCount"]:
            return "not", f"reviewed by {last['author']['login']} since the last push"

    checks = latest_checks(contexts.get("nodes") or [])
    blocking = [
        f"{state}: {name}"
        for name, state in sorted(checks.items())
        if state in ("pending", "held", "failing")
    ]
    if blocking:
        return "not", ", ".join(blocking)
    if not any(
        state == "ok" and name.startswith("gates (") for name, state in checks.items()
    ):
        return "not", "no gates lane passed"
    return "ready", f"all {len(reviewer)} thread(s) resolved, checks green"


def changes(prs):
    """(number, "add" | "remove", reason) for each pull request to relabel."""
    out = []
    for pr in sorted(prs, key=lambda p: p["number"]):
        has = any(label["name"] == LABEL for label in pr["labels"]["nodes"])
        answer, why = verdict(pr)
        if answer == "ready" and not has:
            out.append((pr["number"], "add", why))
        elif answer == "not" and has:
            out.append((pr["number"], "remove", why))
    return out


def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=False)


def open_prs(repo):
    """Every open pull request, or None when the API fails."""
    owner, name = repo.split("/", 1)
    result = gh(
        "api",
        "graphql",
        "--paginate",
        "-F",
        f"owner={owner}",
        "-F",
        f"name={name}",
        "-f",
        f"query={QUERY}",
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return None
    # --paginate prints one JSON document per page, back to back.
    decoder, text, pos, prs = json.JSONDecoder(), result.stdout, 0, []
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos == len(text):
            return prs
        page, pos = decoder.raw_decode(text, pos)
        prs += page["data"]["repository"]["pullRequests"]["nodes"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("repo", nargs="?", default="sysprog21/codetrial")
    args = parser.parse_args(argv)

    encoded = quote(LABEL, safe="")
    if (
        not args.dry_run
        and gh("api", f"repos/{args.repo}/labels/{encoded}", "--silent").returncode
    ):
        print(f'label "{LABEL}" does not exist in {args.repo}', file=sys.stderr)
        return 2
    prs = open_prs(args.repo)
    if prs is None:
        return 2

    status = 0
    for number, action, why in changes(prs):
        print(f"#{number}: {action} ({why})")
        if args.dry_run:
            continue
        if action == "add":
            call = (
                "api",
                f"repos/{args.repo}/issues/{number}/labels",
                "-f",
                f"labels[]={LABEL}",
                "--silent",
            )
        else:
            call = (
                "api",
                "-X",
                "DELETE",
                f"repos/{args.repo}/issues/{number}/labels/{encoded}",
                "--silent",
            )
        result = gh(*call)
        if result.returncode:
            sys.stderr.write(result.stderr)
            status = 2
    return status


if __name__ == "__main__":
    sys.exit(main())
