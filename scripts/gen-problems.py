#!/usr/bin/env python3
"""Generate the per-problem statement and judge files the browser fetches."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The modules live beside this script, which runs as a file rather than as
# part of a package.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from problem_bank.bank import (  # noqa: E402, F401 -- the tests and guards read these here
    ARG_TYPES,
    CACHE,
    DEFAULT_PROBLEM_ID,
    DETAIL_QUERY,
    DIRS,
    ENDPOINT,
    GUIDE_SOURCE,
    JUDGE_OUTPUT_DIR,
    JUDGE_SOURCE,
    LIST_ENDPOINT,
    MANIFEST,
    NEUTRAL_DIRECTIONS,
    OUTPUT_DIR,
    PAGE_MAP_OUTPUT,
    PUBLIC_METADATA_KEYS,
    REACTO_STAGES,
    ROOT,
    RUST_GUIDES,
    RUST_TOPICS,
    RUST_VARIANTS,
    SOURCE,
    STARTER_LANGS,
    STUDY_PLAN_QUERY,
    VARIANT_KEYS,
    VARIANT_SOURCE,
    compact,
    field,
    listed,
    named,
    public_metadata,
    read_json,
    repeated,
    validated_problems,
    write_json,
)
from problem_bank.fetch import (  # noqa: E402, F401 -- the tests and guards read these here
    cached_detail,
    cached_problem_list,
    check_plan_slugs,
    fetch,
    paid_slugs,
    plan_drift,
    plan_sections,
    problem_title,
    read_manifest,
    request_json,
    scaffold,
    scaffold_entries,
    sync_study_plan,
)
from problem_bank.rules import (  # noqa: E402, F401 -- the tests and guards read these here
    GUIDE_FURNITURE,
    RECOGNISABLE_CHARS,
    RECOGNISABLE_VALUES,
    REVEALING_LABEL,
    camel_words,
    case_input,
    check_labels,
    check_page_names,
    example_arguments,
    input_values,
    names_source,
    page_slug,
    posed,
    quotes_example,
    sized,
    spelled_words,
    spells,
    spoken_numbers,
    spoken_text,
    text_list,
    validated_guides,
    validated_variant,
    validated_variants,
)
from problem_bank.emit import (  # noqa: E402, F401 -- the tests and guards read these here
    candidate_problem,
    drifted,
    generated,
    orphans,
    rust_guides,
    rust_str,
    rust_strs,
    rust_topics,
    rust_variants,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="fail if the generated files are stale"
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="fetch LeetCode metadata into problem-bank/leetcode",
    )
    parser.add_argument(
        "--sync-study-plan",
        action="store_true",
        help="sync the Top Interview 150 manifest from LeetCode",
    )
    parser.add_argument(
        "--plan-drift",
        action="store_true",
        help="report how the live study plan differs from problem-bank",
    )
    parser.add_argument(
        "--scaffold",
        metavar="SLUG",
        default=None,
        help="print problem-bank and judge entries for one LeetCode problem",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay-ms", type=int, default=250)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    # --check is the CI gate and only reads. Both other modes reach the network
    # and write: sync rewrites the manifest, fetch fills the cache. Either one
    # ahead of --check has it report on staleness it just caused.
    if args.check and (
        args.fetch or args.sync_study_plan or args.plan_drift or args.scaffold
    ):
        parser.error("--check only reads; run it on its own")
    return args


def main() -> int:
    args = parse_args()
    if args.plan_drift:
        return plan_drift()
    if args.scaffold:
        return scaffold(args.scaffold, args.force)
    if args.sync_study_plan:
        sync_study_plan()
    if args.fetch:
        fetch(args.limit, args.delay_ms, args.force)

    try:
        files = generated()
    except (KeyError, RuntimeError, TypeError) as error:
        print(f"{SOURCE.relative_to(ROOT)}: {error}", file=sys.stderr)
        return 1
    if args.check:
        try:
            read_manifest()
        except RuntimeError as error:
            print(f"scripts/top-interview-150.json: {error}", file=sys.stderr)
            return 1
        stale = drifted(files)
        if stale:
            print(
                "stale generated files; run: python3 scripts/gen-problems.py",
                file=sys.stderr,
            )
            for path in stale:
                print(f"  {path}", file=sys.stderr)
            return 1
        return 0

    for directory in DIRS:
        directory.mkdir(parents=True, exist_ok=True)
    for path in orphans(files):
        path.unlink()
    for path, text in files.items():
        path.write_text(text)
    print(
        f"updated {len(files)} files under {' and '.join(str(d.relative_to(ROOT)) for d in DIRS)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
