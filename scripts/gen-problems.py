#!/usr/bin/env python3
"""Generate the per-problem statement and judge files the browser fetches."""

from __future__ import annotations

import argparse
import functools
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "problem-bank" / "problems.json"
JUDGE_SOURCE = ROOT / "problem-bank" / "judges.json"
# The exercise a candidate is actually given. A candidate shown the practice
# problem as published recognises it and recites an answer, which is not what an
# interview measures, so the page carries a scenario written around the same
# contract and the source title, wording and constraints stay on the server.
VARIANT_SOURCE = ROOT / "problem-bank" / "variants.json"
# Solution notes for the report reviewer, with the license they came under.
GUIDE_SOURCE = ROOT / "problem-bank" / "guides.json"
# One file per problem, fetched on demand, rather than one module holding all
# 150. Two reasons, and the second is the important one:
#
#   - A candidate downloaded 288 KB of statements and 215 KB of judges to work
#     on one problem, parsed as JavaScript source rather than as JSON.
#   - The judge holds the expected output of every test case. Shipping all of
#     them handed a candidate the answers to the other 149 problems along with
#     their own. Their own is still there, because the runner is in the browser;
#     this bounds the exposure rather than closing it. See the note on
#     `apply_test_results` in src/agent.rs for where that boundary is drawn.
OUTPUT_DIR = ROOT / "web" / "problems"
JUDGE_OUTPUT_DIR = ROOT / "web" / "judges"
PAGE_MAP_OUTPUT = ROOT / "web" / "problem-pages.json"
# The exercise a link naming nothing the bank has opens, `DEFAULT_PROBLEM_ID` in
# src/agent/problems.rs. Marked in the page map rather than written into the
# loader, which every page serves: the loader would otherwise carry a published
# id on every load.
DEFAULT_PROBLEM_ID = "two-sum"
DIRS = (OUTPUT_DIR, JUDGE_OUTPUT_DIR)
RUST_TOPICS = ROOT / "src" / "agent" / "problem_topics.rs"
RUST_VARIANTS = ROOT / "src" / "agent" / "problem_variants.rs"
RUST_GUIDES = ROOT / "src" / "agent" / "problem_guides.rs"
REACTO_STAGES = ("repeat", "example", "algorithm", "coding", "test", "optimizations")
NEUTRAL_DIRECTIONS = {
    "repeat": "Ask the candidate to restate the inputs, outputs, constraints, and ambiguities.",
    "example": "Ask the candidate to choose and trace an ordinary example and a boundary case.",
    "algorithm": "Ask for the candidate's approach, correctness argument, and complexity.",
    "coding": "Ask the candidate to implement their stated approach and explain major decisions.",
    "test": "Ask the candidate to predict useful cases and expected results before running them.",
    "optimizations": "Ask for complexity, an uncovered edge case, and a justified optimization or cleanup.",
}
# No competencies: they are the topic tags, and "Dynamic Programming" beside
# the problem names the technique before the candidate has said a word.
PUBLIC_METADATA_KEYS = {
    "difficulty",
    "reactoStages",
    "followUpDirections",
}
VARIANT_KEYS = {
    "title",
    "brief",
    "contract",
    "examples",
    "clarifications",
    "followUps",
    "hints",
}
MANIFEST = ROOT / "scripts" / "top-interview-150.json"
CACHE = ROOT / "problem-bank" / "leetcode"
ENDPOINT = "https://leetcode.com/graphql"
LIST_ENDPOINT = "https://leetcode.com/api/problems/all/"
STUDY_PLAN_QUERY = """
query studyPlanV2Detail($planSlug: String!) {
  studyPlanV2Detail(planSlug: $planSlug) {
    planSubGroups {
      name
      questions {
        titleSlug
      }
    }
  }
}"""

DETAIL_QUERY = """
query questionData($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    questionFrontendId
    difficulty
    isPaidOnly
    codeSnippets {
      langSlug
      code
    }
    exampleTestcases
    metaData
  }
}"""


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


def named(value: object) -> bool:
    """True when a GraphQL field arrived as a usable, non-blank string."""
    return isinstance(value, str) and bool(value.strip())


def field(value: object, key: str) -> object:
    """One level into a GraphQL object, or None when the shape is wrong.

    A plain `.get` assumes every level really is an object. When one is not,
    the caller gets an AttributeError from deep inside a comprehension instead
    of the RuntimeError its guards were written to raise.
    """
    return value.get(key) if isinstance(value, dict) else None


def listed(value: object, key: str) -> list:
    """A list-valued GraphQL field, or empty when it is anything else.

    `field` alone is not enough for the two keys that get iterated: a scalar
    there means `for x in 5`, which is a TypeError raised from inside a
    comprehension rather than the guard the caller documented. Empty is the
    same answer null already gave, so a wrong-typed field fails the slug checks
    below rather than inventing a third behaviour.
    """
    found = field(value, key)
    return found if isinstance(found, list) else []


def read_json(path: Path) -> object:
    return json.loads(path.read_text())


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(value, indent=2)}\n")


def request_json(url: str, *, body: object | None = None) -> object:
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://leetcode.com/",
    }
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=headers, method="POST" if data else "GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"{url} returned {error.code}") from error


def repeated(items: list) -> list:
    return sorted({item for item in items if items.count(item) > 1})


def check_plan_slugs(slugs: list[str]) -> None:
    """What any copy of the study plan has to satisfy to be usable here.

    The count is not checked: naming exactly the bank already fixes it, and a
    second literal 150 is a second thing to update. `tests/browser/
    leetcode-import.test.js` owns that number, because how many problems ship
    is a product decision rather than a consistency one.
    """
    unique = set(slugs)
    if len(unique) != len(slugs):
        raise RuntimeError(f"duplicate slugs in the plan: {repeated(slugs)}")
    bank = {problem["id"] for problem in read_json(SOURCE)}
    if unique != bank:
        raise RuntimeError(
            "plan diverges from problem-bank; port it first. "
            f"plan adds {sorted(unique - bank)}, plan drops {sorted(bank - unique)}"
        )


def read_manifest() -> list[str]:
    """The study-plan slugs, checked against the bank they are supposed to name.

    Three callers need the same answer, so the rule lives here rather than in
    each: `--fetch` walks these slugs, `--check` gates on them, and the sync
    holds a freshly downloaded plan to the same standard before overwriting the
    file. Before this, only the sync checked, and only when a human ran it with
    a network.
    """
    manifest = read_json(MANIFEST)
    slugs = [slug for section in manifest["sections"] for slug in section["slugs"]]
    check_plan_slugs(slugs)
    return slugs


def plan_sections(body: object) -> list[dict]:
    """The sections a study-plan response describes, or a RuntimeError saying why not.

    Pure, and separate from the sync for that reason: no network, no files, so
    the guards below can be exercised directly instead of through a function
    that would write the tracked manifest as a side effect of being tested.
    """
    errors = field(body, "errors")
    if errors:
        first = errors[0] if isinstance(errors, list) else errors
        raise RuntimeError(f"studyPlanV2Detail: {field(first, 'message') or first}")
    plan = field(field(body, "data"), "studyPlanV2Detail")
    if not plan:
        raise RuntimeError("LeetCode has no top-interview-150 plan")
    sections = []
    for group in listed(plan, "planSubGroups"):
        topic = field(group, "name")
        members = [
            field(question, "titleSlug") for question in listed(group, "questions")
        ]
        if not named(topic) or not members:
            raise RuntimeError(f"plan section is unnamed or empty: {topic!r}")
        if not all(named(slug) for slug in members):
            raise RuntimeError(f"plan section {topic!r} is missing a slug")
        sections.append({"topic": topic, "slugs": members})
    return sections


def sync_study_plan() -> None:
    """Refresh the manifest from the live study plan.

    Everything that can refuse runs before the write, because the manifest is
    checked in: a bad response has to leave the committed file alone rather
    than replace it with something the next reader reconstructs from history.
    """
    body = request_json(
        ENDPOINT,
        body={
            "query": STUDY_PLAN_QUERY,
            "variables": {"planSlug": "top-interview-150"},
        },
    )
    sections = plan_sections(body)
    slugs = [slug for section in sections for slug in section["slugs"]]
    check_plan_slugs(slugs)
    write_json(
        MANIFEST,
        {
            "source": "https://leetcode.com/studyplan/top-interview-150/",
            "sections": sections,
        },
    )
    print(f"synced {len(slugs)} problems in {len(sections)} sections")


# Which judge argType a LeetCode metaData type needs, derived from the entries
# already in judges.json. `Node` is deliberately absent: the same name covers
# graph nodes, next-pointer trees, random-pointer lists and quad trees, and only
# the statement says which, so a scaffold that guessed would be worse than one
# that says it does not know.
ARG_TYPES = {
    "ListNode": "linkedList",
    "ListNode[]": "linkedListArray",
    "TreeNode": "binaryTree",
}

# LeetCode's language slug for each language this repo ships a starter for.
STARTER_LANGS = {
    "python": "python3",
    "javascript": "javascript",
    "c": "c",
    "cpp": "cpp",
    "java": "java",
}


def plan_drift() -> int:
    """Say how the published plan differs from what this repo holds."""
    body = request_json(
        ENDPOINT,
        body={
            "query": STUDY_PLAN_QUERY,
            "variables": {"planSlug": "top-interview-150"},
        },
    )
    live = {slug for section in plan_sections(body) for slug in section["slugs"]}
    bank = {problem["id"] for problem in read_json(SOURCE)}
    added, dropped = sorted(live - bank), sorted(bank - live)
    if not added and not dropped:
        print(f"in sync: {len(live)} problems")
        return 0
    for slug in added:
        print(f"plan adds:  {slug}")
    for slug in dropped:
        print(f"plan drops: {slug}")
    print(
        "\nport these first, then run --sync-study-plan. For each added slug:\n"
        "  python3 scripts/gen-problems.py --scaffold SLUG",
        file=sys.stderr,
    )
    return 1


def problem_title(slug: str, force: bool) -> str:
    body = cached_problem_list(force)
    for item in body["stat_status_pairs"]:
        if item["stat"]["question__title_slug"] == slug:
            return item["stat"]["question__title"]
    raise RuntimeError(f"{slug}: not in LeetCode's problem list")


def scaffold(slug: str, force: bool) -> int:
    """Print what LeetCode can supply for a problem this repo does not have.

    Not the whole entry, and it says so where it stops. The statement is left
    empty because the fetcher does not ask for LeetCode's prose, and the judge
    cases carry inputs without expected values because `exampleTestcases` is
    inputs only. Both have to be written by someone who has read the problem.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    _, detail = cached_detail(slug, force)
    if detail.get("isPaidOnly"):
        raise RuntimeError(f"{slug}: paid-only, so the runner cannot serve it")
    problem, judge = scaffold_entries(slug, problem_title(slug, force), detail)

    print(f"# problem-bank/problems.json entry for {slug}")
    print(json.dumps(problem, indent=2))
    print(f"\n# problem-bank/judges.json entry for {slug}")
    print(json.dumps(judge, indent=2))
    missing = ["statement", "examples", "constraints", "topics"]
    if None in judge["argTypes"] and "Node" in judge["paramTypes"]:
        missing.append("argTypes for Node")
    print(
        f"\nstill yours to write: {', '.join(missing)}, an expected value for each "
        f"of the {len(judge['cases'])} cases, and the house starter comment, which "
        "LeetCode's snippets leave as an empty body.",
        file=sys.stderr,
    )
    return 0


def scaffold_entries(slug: str, title: str, detail: dict) -> tuple[dict, dict]:
    """The two bank entries a LeetCode detail can fill in on its own.

    Pure, so the shape can be checked without the network. What it leaves empty
    it leaves empty on purpose: the fetcher does not ask for LeetCode's prose,
    and `exampleTestcases` carries inputs without the expected values, so the
    statement and every `expected` are written by someone who read the problem.
    """
    meta = json.loads(detail["metaData"])
    snippets = {item["langSlug"]: item["code"] for item in detail["codeSnippets"]}
    params = meta.get("params", [])

    problem = {
        "id": slug,
        "title": title,
        "difficulty": detail["difficulty"],
        "topics": [],
        "statement": [],
        "examples": [],
        "constraints": [],
        "starterCode": {
            language: snippets[wanted]
            for language, wanted in STARTER_LANGS.items()
            if wanted in snippets
        },
    }

    # exampleTestcases is one value per line, the params in order, repeating.
    lines = detail["exampleTestcases"].splitlines()
    width = max(len(params), 1)
    cases = [
        {"input": [json.loads(value) for value in lines[at : at + width]]}
        for at in range(0, len(lines) - width + 1, width)
    ]
    judge = {
        "kind": "function",
        "entry": meta["name"],
        "checker": "exact",
        "argTypes": [ARG_TYPES.get(param["type"]) for param in params],
        "cases": cases,
        "paramNames": [param["name"] for param in params],
        "paramTypes": [param["type"] for param in params],
        "returnType": meta["return"]["type"],
    }
    output = ARG_TYPES.get(meta["return"]["type"])
    if output:
        judge["outputType"] = output

    return problem, judge


def cached_problem_list(force: bool) -> object:
    file = CACHE / "all-problems.json"
    if file.exists() and not force:
        return read_json(file)
    body = request_json(LIST_ENDPOINT)
    write_json(file, body)
    return body


def paid_slugs(force: bool) -> set[str]:
    body = cached_problem_list(force)
    return {
        item["stat"]["question__title_slug"]
        for item in body["stat_status_pairs"]
        if item["paid_only"]
    }


def cached_detail(slug: str, force: bool) -> tuple[bool, object]:
    file = CACHE / f"{slug}.json"
    if file.exists() and not force:
        return True, read_json(file)
    body = request_json(
        ENDPOINT, body={"query": DETAIL_QUERY, "variables": {"titleSlug": slug}}
    )
    detail = field(field(body, "data"), "question")
    if not detail:
        raise RuntimeError(f"{slug}: missing question detail")
    write_json(file, detail)
    return False, detail


def fetch(limit: int | None, delay_ms: int, force: bool) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    slugs = read_manifest()
    wanted = slugs[:limit] if limit is not None else slugs
    paid = paid_slugs(force)
    fetched = cached = 0
    skipped: list[str] = []
    for index, slug in enumerate(wanted):
        if slug in paid:
            skipped.append(slug)
            continue
        was_cached, detail = cached_detail(slug, force)
        if detail.get("isPaidOnly"):
            skipped.append(slug)
            continue
        fetched += 1
        cached += int(was_cached)
        if not was_cached and index + 1 < len(wanted):
            time.sleep(delay_ms / 1000)
    print(
        json.dumps(
            {
                "requested": len(wanted),
                "fetched": fetched,
                "cached": cached,
                "skipped": skipped,
                "cacheDir": str(CACHE),
            },
            indent=2,
        )
    )


def validated_problems() -> list[dict]:
    problems = read_json(SOURCE)
    if not isinstance(problems, list):
        raise RuntimeError("problem bank must be a JSON array")
    seen: set[str] = set()
    for problem in problems:
        problem_id = problem.get("id") if isinstance(problem, dict) else None
        if not named(problem_id) or problem_id in seen:
            raise RuntimeError(f"missing or duplicate problem id: {problem_id!r}")
        seen.add(problem_id)
        if problem.get("difficulty") not in {"Easy", "Medium", "Hard"}:
            raise RuntimeError(f"{problem_id}: unknown difficulty")
        topics = problem.get("topics")
        if not isinstance(topics, list) or not 1 <= len(topics) <= 8:
            raise RuntimeError(f"{problem_id}: topics must contain 1..8 values")
        cleaned = [topic.strip() for topic in topics if named(topic)]
        if len(cleaned) != len(topics) or len(set(cleaned)) != len(cleaned):
            raise RuntimeError(f"{problem_id}: topics must be non-empty and unique")
    return problems


def public_metadata(problem: dict) -> dict:
    return {
        "difficulty": problem["difficulty"],
        "reactoStages": list(REACTO_STAGES),
        "followUpDirections": [
            {"stage": stage, "direction": NEUTRAL_DIRECTIONS[stage]}
            for stage in REACTO_STAGES
        ],
    }


def compact(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def case_input(judge: dict, case: dict) -> str:
    """A judge case written the way an example on the page writes its input."""
    if judge["kind"] == "class":
        operations, arguments = case["input"]
        return f"{compact(operations)}\n{compact(arguments)}"
    return ", ".join(
        f"{name} = {compact(value)}"
        for name, value in zip(judge["paramNames"], case["input"])
    )


def input_values(text: str) -> tuple:
    """The numbers and quoted strings an example input holds, in order.

    Names are dropped, parameters and operations alike, and numbers compare by
    value, so two spellings of the same input agree.
    """
    values = []
    # `null` is a value too: a tree written level by level with its gaps, and
    # dropping them makes two different trees read the same.
    for quoted, number, literal in re.findall(
        r'"((?:[^"\\]|\\.)*)"|(-?\d+(?:\.\d+)?)|\b(null|true|false)\b', text
    ):
        values.append(float(number) if number else literal or quoted)
    return tuple(values)


def camel_words(text: str) -> str:
    """Letters and digits only, lowercased: `coinChange` and "Coin Change" agree."""
    return "".join(character for character in text.lower() if character.isalnum())


def spelled_words(text: str) -> list[str]:
    """Lowercase words, with identifiers split where their case changes, so
    `minStackCreate` reads as min, stack, create and `LRUCache` as lru, cache."""
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return re.findall(r"[a-z0-9]+", text.lower())


def names_source(title: str, text: str) -> bool:
    """Whether text gives away the published problem it was written from.

    One rule, and `tests/common/words.rs` applies the same one to the prompts
    and to what the interviewer says. A title that is a single ordinary word,
    "Candy" or "Triangle", is also a word a scenario uses, so it is not held
    against anything. Any other title counts when a run of consecutive words
    spells it with the spaces gone, identifiers split at their case changes:
    "LRUCache", "minStackCreate", "lru cache" and "3 Sum" all name their
    problems, and "those 3 sums" does not. Parameter names are not exempt: a
    brief that says "merge intervals" names the problem whatever the parameter
    is called.
    """
    return not title.isalpha() and spells(title, text)


def spells(name: str, text: str) -> bool:
    """Whether a run of consecutive words in text joins into name."""
    target = camel_words(name)
    words = spelled_words(text)
    for start in range(len(words)):
        joined = ""
        for word in words[start:]:
            joined += word
            if joined == target:
                return True
            if len(joined) >= len(target):
                break
    return False


# How much of a published example is recognisable on its own: this many values,
# or one string this long. Both the published-case check and the prose check
# read these, so the two agree on what counts.
RECOGNISABLE_VALUES = 4
RECOGNISABLE_CHARS = 5


@functools.cache
def spoken_numbers(text: str) -> tuple:
    return tuple(value for value in input_values(text) if isinstance(value, float))


def example_arguments(text: str) -> list[tuple]:
    """The values of each argument a published example names, one tuple apiece.

    `head = [1,2,3,4,5], k = 2` gives (1, 2, 3, 4, 5) and (2,). Only an argument
    long enough to be recognised on its own counts: four values or more, or one
    string of five characters or more. The published tree with another k, or the
    published sentence with another word list, is still the published example.
    """
    parts, depth, quoted, start = [], 0, False, 0
    for at, character in enumerate(text):
        # A quote is escaped only behind an odd run of backslashes.
        if character == '"' and (at - len(text[:at].rstrip("\\"))) % 2 == 0:
            quoted = not quoted
        elif quoted:
            continue
        elif character in "[{(":
            depth += 1
        elif character in "]})":
            depth -= 1
        elif character == "," and depth == 0:
            parts.append(text[start:at])
            start = at + 1
    parts.append(text[start:])
    arguments = [input_values(part.split("=", 1)[-1]) for part in parts]
    return [
        values
        for values in arguments
        if len(values) >= RECOGNISABLE_VALUES
        or (
            len(values) == 1
            and isinstance(values[0], str)
            and len(values[0]) >= RECOGNISABLE_CHARS
        )
    ]


def quotes_example(example: tuple, text: str) -> bool:
    """Whether prose repeats a published example's input.

    A hint that walks "3, 0, 6, 1, 5" or lines up "paper and title" hands the
    candidate the published example the page no longer shows. It takes four
    numbers or more holding three distinct values, in any order, since "[1, 6],
    [2, 8], [7, 12] and [10, 16]" is the same input sorted; with fewer distinct
    values only five or more in the published order count, so an output of 0s
    and 1s is not taken for a published board. A string counts from five
    characters, and only as a whole word.
    """
    numbers = tuple(value for value in example if isinstance(value, float))
    if len(numbers) >= RECOGNISABLE_VALUES:
        spoken = spoken_numbers(text)
        distinct = len(set(numbers)) >= 3
        for at in range(len(spoken) - len(numbers) + 1):
            window = spoken[at : at + len(numbers)]
            if (window == numbers and (distinct or len(numbers) >= 5)) or (
                distinct and sorted(window) == sorted(numbers)
            ):
                return True
    return any(
        isinstance(value, str)
        and len(value) >= RECOGNISABLE_CHARS
        and re.search(rf"(?<![\w-]){re.escape(value)}(?![\w-])", text)
        for value in example
    )


def sized(problem_id: str, where: str, value: object, low: int, high: int) -> list:
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise RuntimeError(f"{problem_id}: {where} must hold {low}..{high} entries")
    return value


def spoken_text(problem_id: str, where: str, value: object) -> str:
    """One string of variant prose, held to what both of its readers accept.

    ASCII because the same text is compiled into Rust, where the source stays
    ASCII, and spoken by the interviewer, where a backtick is read aloud.
    """
    if not named(value) or value != value.strip():
        raise RuntimeError(f"{problem_id}: {where} must be non-empty, unpadded text")
    if not value.isascii() or not value.isprintable() or "`" in value:
        raise RuntimeError(
            f"{problem_id}: {where} must be printable ASCII with no backticks"
        )
    return value


def text_list(
    problem_id: str, where: str, value: object, low: int, high: int
) -> list[str]:
    return [
        spoken_text(problem_id, f"{where}[{at}]", item)
        for at, item in enumerate(sized(problem_id, where, value, low, high))
    ]


# Printed beside OK or FAIL in the results panel, so a label saying where a
# case came from, or which approach it defeats, is read by the candidate the
# moment they press Run.
REVEALING_LABEL = re.compile(
    r"leetcode|sample|\bexample|greedy|dynamic|\bheap|pointer|binary search|prefix sum|\bxor\b",
    re.IGNORECASE,
)


def check_labels(problem_id: str, judge: dict) -> None:
    """The labels as they ship, against the judge's published names.

    Checked after renames, so a rename cannot make two labels the same or turn
    one into a word that gives the case away.
    """
    # Two cases under one label are one row twice in the results panel, and a
    # report cannot say which of them failed.
    labels = [case["label"] for case in judge["cases"]]
    if duplicates := repeated(labels):
        raise RuntimeError(f"{problem_id}: case labels repeat: {duplicates}")
    # The published entry point is refused rather than renamed: it is often a
    # plain verb, "rotate by three", and the new name would not read as one.
    published = [name for name in (judge.get("entry"), judge.get("className")) if name]
    for label in labels:
        if REVEALING_LABEL.search(label) or any(
            spells(name, label) for name in published
        ):
            raise RuntimeError(f"{problem_id}: case label {label!r} gives it away")


def posed(problem: dict, judge: dict, variant: dict) -> tuple[dict, dict]:
    """The bank entry and judge with the variant's names in place of the published ones.

    The bank keeps the names LeetCode publishes, so a scaffolded or refreshed
    entry needs no hand edits; the rename is declared once in the variant and
    applied here to everything that ships or reaches the interviewer.
    """
    # Printed beside OK or FAIL, so a label written against a published
    # parameter, "magazine too short", says it to the candidate on every run. The
    # entry point is not renamed there; check_labels refuses it instead.
    worded = {**variant.get("terms", {}), **variant.get("parameters", {})}
    renames = dict(worded)
    if "entry" in variant:
        renames[judge["entry"]] = variant["entry"]
    # C passes an array with its length beside it, `pointsSize` and
    # `pointsColSize`, so a renamed parameter is a prefix there as well.
    prefixes = dict(variant.get("parameters", {}))
    if "className" in variant:
        old, new = judge["className"], variant["className"]
        renames[old] = new
        # C has no classes, so its starters spell the class as a lower-camel
        # prefix on every function: `minStackCreate`, `lRUCacheGet`.
        prefixes[old[0].lower() + old[1:]] = new[0].lower() + new[1:]

    def renamed(text: str) -> str:
        for old, new in renames.items():
            text = re.sub(rf"\b{re.escape(old)}\b", new, text)
        for old, new in prefixes.items():
            text = re.sub(rf"\b{re.escape(old)}(?=[A-Z])", new, text)
        return text

    judge = {
        **judge,
        "cases": [
            {
                **case,
                "label": re.sub(
                    r"\b\w+\b",
                    lambda word: worded.get(word.group(), word.group()),
                    case["label"],
                ),
            }
            for case in judge["cases"]
        ],
    }
    if "entry" in variant:
        judge["entry"] = variant["entry"]
    if "className" in variant:
        # A class judge calls the class by name and lists the constructor as the
        # first operation of every case, and a leftover `entry` on one is the
        # published camel-case name that nothing reads.
        judge.pop("entry", None)
        judge["className"] = variant["className"]
        judge["cases"] = [
            {
                **case,
                "input": [
                    [
                        renames.get(operation, operation)
                        for operation in case["input"][0]
                    ],
                    *case["input"][1:],
                ],
            }
            for case in judge["cases"]
        ]
    if "paramNames" in judge:
        judge["paramNames"] = [renames.get(name, name) for name in judge["paramNames"]]
    problem = {
        **problem,
        "starterCode": {
            language: renamed(code) for language, code in problem["starterCode"].items()
        },
        "constraints": [renamed(line) for line in problem["constraints"]],
    }
    return problem, judge


def validated_variant(problem: dict, judge: dict, variant: object) -> dict:
    """The checks that keep a scenario gradeable and keep the source out of it.

    Gradeable: every example on the page is a real judge case, and the entry
    point the brief names is the one every starter snippet defines and the judge
    calls. Out of it: the source title is nowhere in what the candidate reads,
    and the entry point is not the published name.

    Returns the posed problem, the posed judge and the examples as the page
    shows them.
    """
    problem_id = problem["id"]
    optional = {"entry", "className", "parameters", "terms"}
    if (
        not isinstance(variant, dict)
        or not VARIANT_KEYS <= set(variant) <= VARIANT_KEYS | optional
    ):
        raise RuntimeError(
            f"{problem_id}: a variant has exactly {sorted(VARIANT_KEYS)}"
        )
    title = spoken_text(problem_id, "title", variant["title"])
    brief = text_list(problem_id, "brief", variant["brief"], 1, 3)
    # The exact contract in the scenario's words. The interviewer judges from
    # this rather than from the published statement, which names the problem
    # as often as it states it.
    contract = spoken_text(problem_id, "contract", variant["contract"])
    follow_ups = text_list(problem_id, "followUps", variant["followUps"], 2, 3)
    hints = text_list(problem_id, "hints", variant["hints"], 3, 3)
    for at, item in enumerate(
        sized(problem_id, "clarifications", variant["clarifications"], 3, 6)
    ):
        if not isinstance(item, dict) or set(item) != {"question", "answer"}:
            raise RuntimeError(
                f"{problem_id}: clarifications[{at}] is a question and an answer"
            )
        spoken_text(problem_id, f"clarifications[{at}].question", item["question"])
        spoken_text(problem_id, f"clarifications[{at}].answer", item["answer"])

    # Both kinds are renamed: a function by its entry point, a class by its
    # name, since `LRUCache` on screen names the problem as plainly as a title.
    declared = "entry" if judge["kind"] == "function" else "className"
    if declared not in variant or ({"entry", "className"} - {declared}) & set(variant):
        raise RuntimeError(
            f"{problem_id}: a {judge['kind']} problem declares a new {declared}"
        )
    # Every name a variant gives, entry point, class, parameters and terms, is
    # held to the same list of names it may not bring back.
    published_names = {
        camel_words(name)
        for name in (problem["title"], judge.get("entry"), judge.get("className"))
        if name
    }
    pattern = r"[a-z][A-Za-z0-9]+" if declared == "entry" else r"[A-Z][A-Za-z0-9]+"
    if (
        not re.fullmatch(pattern, str(variant[declared]))
        or camel_words(variant[declared]) in published_names
    ):
        raise RuntimeError(
            f"{problem_id}: {declared} {variant[declared]!r} is not a new name"
        )
    parameters = variant.get("parameters", {})
    if not isinstance(parameters, dict) or not set(parameters) <= set(
        judge.get("paramNames", [])
    ):
        raise RuntimeError(f"{problem_id}: parameters renames only judge parameters")
    # Words a starter snippet carries that are neither the entry point nor a
    # parameter, such as a comment naming the published node type.
    terms = variant.get("terms", {})
    if not isinstance(terms, dict) or not all(
        re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", str(word))
        for pair in terms.items()
        for word in pair
    ):
        raise RuntimeError(
            f"{problem_id}: terms maps one identifier-like word to another"
        )
    # A rename into the published entry point would put it back everywhere the
    # rename reaches, labels included.
    for target in [*parameters.values(), *terms.values()]:
        if camel_words(str(target)) in published_names:
            raise RuntimeError(f"{problem_id}: {target!r} is the published name")
    shipped, graded = posed(problem, judge, variant)
    check_labels(problem_id, {**judge, "cases": graded["cases"]})

    if "leetcode" in json.dumps(variant).lower():
        raise RuntimeError(f"{problem_id}: a variant never names the source site")
    for where, text in [
        ("title", title),
        ("contract", contract),
        *[(f"brief[{at}]", line) for at, line in enumerate(brief)],
    ]:
        if names_source(problem["title"], text):
            raise RuntimeError(f"{problem_id}: {where} names the source title")

    # What the editor opens with, and what the browser runs: the starters and
    # every judge case, labels, inputs and expected values alike.
    for language, code in shipped["starterCode"].items():
        if names_source(problem["title"], code):
            raise RuntimeError(
                f"{problem_id}: the {language} starter names the source title"
            )
    for case in graded["cases"]:
        if names_source(problem["title"], json.dumps(case)):
            raise RuntimeError(
                f"{problem_id}: judge case {case['label']!r} names the source title"
            )
    for at, line in enumerate(shipped["constraints"]):
        if names_source(problem["title"], line):
            raise RuntimeError(
                f"{problem_id}: constraints[{at}] names the source title"
            )

    name = graded["className"] if graded["kind"] == "class" else graded["entry"]
    if not any(re.search(rf"\b{re.escape(name)}\b", line) for line in brief):
        raise RuntimeError(f"{problem_id}: the brief never names {name}")
    for language, code in shipped["starterCode"].items():
        if not re.search(rf"\b{re.escape(name)}\b", code):
            raise RuntimeError(
                f"{problem_id}: the {language} starter does not define {name}"
            )

    cases = graded["cases"]
    # Compared by the values an input holds, not by how it is written: the
    # published examples spell `x = 2.00000` and `addNum(1), addNum(2)` where a
    # judge case holds 2 and a list of operations.
    # A class example is published as calls, `addNum(1), addNum(2)`, or as the
    # judge's two lists; either way only its argument values say which case it is.
    operations = {judge.get("className")} | {
        name
        for case in judge["cases"]
        if judge["kind"] == "class"
        for name in case["input"][0]
    }
    published_inputs = {
        tuple(
            value for value in input_values(example["input"]) if value not in operations
        )
        for example in problem["examples"]
    }
    # A function case is published by any one argument of a published example,
    # a class case only by its whole argument list: its operation names repeat
    # across every case.
    published_arguments = (
        set()
        if judge["kind"] == "class"
        else {
            values
            for example in problem["examples"]
            for values in example_arguments(example["input"])
        }
    )
    published = {
        at
        for at, case in enumerate(judge["cases"])
        if (
            input_values(compact(case["input"][1]))
            if judge["kind"] == "class"
            else input_values(case_input(judge, case))
        )
        in published_inputs
        or any(
            input_values(compact(argument)) in published_arguments
            for argument in case["input"]
        )
    }
    examples = []
    shown = set()
    for at, example in enumerate(
        sized(problem_id, "examples", variant["examples"], 1, 2)
    ):
        if (
            not isinstance(example, dict)
            or not {"case"} <= set(example) <= {"case", "output", "explanation"}
            or example["case"] not in range(len(cases))
        ):
            raise RuntimeError(f"{problem_id}: examples[{at}] names one judge case")
        case = cases[example["case"]]
        shown.add(example["case"])
        # An override only where the judge's expected value is not what a reader
        # should see: the kept prefix of an in-place compaction, say, or one of
        # several accepted orders.
        rendered = {
            "input": case_input(graded, case),
            "output": compact(case["expected"]),
        }
        for key in ("output", "explanation"):
            if key in example:
                rendered[key] = spoken_text(
                    problem_id, f"examples[{at}].{key}", example[key]
                )
        # Judge data is on the page too, and a published sample sentence can
        # carry the title: "This is an example of text justification."
        shown_text = " ".join(rendered.values())
        if names_source(problem["title"], shown_text):
            raise RuntimeError(f"{problem_id}: examples[{at}] names the source title")
        examples.append(rendered)
    prose = [
        *brief,
        contract,
        *follow_ups,
        *hints,
        *(
            text
            for item in variant["clarifications"]
            for text in (item["question"], item["answer"])
        ),
        *(
            example[key]
            for example in variant["examples"]
            for key in ("output", "explanation")
            if key in example
        ),
    ]
    for quoted in published_inputs | published_arguments:
        for text in prose:
            if quotes_example(quoted, text):
                raise RuntimeError(
                    f"{problem_id}: {text[:60]!r} repeats a published example"
                )
    # The published examples are the most recognisable thing about a problem:
    # "pwwkew" or "paper" and "title" name it as surely as the title does. None
    # of them is shown, so a judge built only from them needs a case of its own.
    if shown & published:
        raise RuntimeError(
            f"{problem_id}: examples show published case {min(shown & published)}"
        )
    return {"problem": shipped, "judge": graded, "examples": examples}


def validated_variants(problems: list[dict], judges: dict) -> dict:
    variants = read_json(VARIANT_SOURCE)
    if not isinstance(variants, dict):
        raise RuntimeError("variants must be a JSON object keyed by problem id")
    ids = [problem["id"] for problem in problems]
    if list(variants) != ids:
        raise RuntimeError(
            "variants must list every problem once, in bank order; "
            f"missing {sorted(set(ids) - set(variants))}, extra {sorted(set(variants) - set(ids))}"
        )
    validated = {}
    for problem in problems:
        judge = judges[problem["id"]]
        variant = variants[problem["id"]]
        validated[problem["id"]] = {
            **validated_variant(problem, judge, variant),
            "variant": variant,
        }
    check_page_names([variant["title"] for variant in variants.values()], ids)
    return validated


def page_slug(title: str) -> str:
    """The name the interview URL carries, from the scenario rather than the id.

    The id is LeetCode's slug, and `?problem=coin-change` in the address bar
    names the problem before the page has rendered. The id still keys history,
    tokens and the agent, so it stays inside the page and in the judge's file
    name, where only a reader of the network panel sees it.
    """
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def check_page_names(titles: list[str], ids: list[str]) -> None:
    pages = [page_slug(title) for title in titles]
    if len(set(pages)) != len(pages):
        raise RuntimeError("variant titles must be unique, and unique as page names")
    # An old link carries an id, and the loader tries a page of that name before
    # the alias map, so a page named like another problem's id would take over
    # that problem's links.
    taken = sorted(set(pages) & set(ids))
    if taken:
        raise RuntimeError(f"page names must not equal a problem id: {taken}")


def candidate_problem(entry: dict) -> dict:
    """Everything the interview page is given, and nothing it is not.

    Built up rather than filtered down from the bank entry, so a field added to
    the bank later stays on the server until someone decides the candidate
    should see it.

    The published title rides along as `source`, shown small beside the
    scenario so a candidate can find the problem again afterwards. The scenario
    is still what the page poses: no number, no statement, no published examples.
    """
    posed_problem, variant = entry["problem"], entry["variant"]
    return {
        "page": page_slug(variant["title"]),
        "title": variant["title"],
        "source": posed_problem["title"],
        "difficulty": posed_problem["difficulty"],
        "brief": variant["brief"],
        "examples": entry["examples"],
        "starterCode": posed_problem["starterCode"],
        "interviewMetadata": public_metadata(posed_problem),
    }


def rust_str(text: str) -> str:
    """A Rust string literal. JSON escapes are not all Rust escapes."""
    escaped = "".join(
        {"\\": "\\\\", '"': '\\"', "\n": "\\n"}.get(character, character)
        if character.isascii()
        else f"\\u{{{ord(character):x}}}"
        for character in text
    )
    return f'"{escaped}"'


def rust_strs(items: list[str]) -> str:
    return "&[" + ", ".join(rust_str(item) for item in items) + "]"


def rust_variants(problems: list[dict], variants: dict) -> str:
    rows = []
    for problem in problems:
        entry = variants[problem["id"]]
        variant = entry["variant"]
        clarifications = ", ".join(
            f"({rust_str(item['question'])}, {rust_str(item['answer'])})"
            for item in variant["clarifications"]
        )
        starters = ", ".join(
            f"({rust_str(language)}, {rust_str(code)})"
            for language, code in entry["problem"]["starterCode"].items()
        )
        rows.append(
            "\n".join(
                [
                    f"    ({rust_str(problem['id'])}, ProblemVariant {{",
                    f"        title: {rust_str(variant['title'])},",
                    f"        page: {rust_str(page_slug(variant['title']))},",
                    f"        brief: {rust_strs(variant['brief'])},",
                    f"        contract: {rust_str(variant['contract'])},",
                    f"        constraints: {rust_strs(entry['problem']['constraints'])},",
                    f"        clarifications: &[{clarifications}],",
                    f"        follow_ups: {rust_strs(variant['followUps'])},",
                    f"        hints: {rust_strs(variant['hints'])},",
                    f"        starters: &[{starters}],",
                    "    }),",
                ]
            )
        )
    return (
        "//! Generated by scripts/gen-problems.py; do not edit by hand.\n\n"
        "use super::ProblemVariant;\n\n"
        "#[rustfmt::skip]\n"
        "pub const PROBLEM_VARIANTS: &[(&str, ProblemVariant)] = &[\n"
        + "\n".join(rows)
        + "\n];\n"
    )


# What an import leaves behind when it drops the code a page was built around:
# headings and links pointing at nothing. A reviewer told to find "the full
# solution here" is reading a web page, not notes.
GUIDE_FURNITURE = (
    "```",
    "You can find the full",
    "Java Solution",
    "Explanation of the Solution",
)


def validated_guides(problems: list[dict], guides: object = None) -> dict:
    """The notes by problem, in bank order. `guides` is the parsed document,
    read from problem-bank/guides.json when it is not handed in."""
    if guides is None:
        guides = read_json(GUIDE_SOURCE)
    if not isinstance(guides, dict) or set(guides) != {
        "source",
        "note",
        "license",
        "notes",
    }:
        raise RuntimeError("guides.json has exactly source, note, license and notes")
    license_text = (
        "\n".join(guides["license"]) if isinstance(guides["license"], list) else ""
    )
    if (
        "Permission is hereby granted" not in license_text
        or "Copyright (c)" not in license_text
    ):
        raise RuntimeError("guides.json must carry the license its notes came under")
    ids = [problem["id"] for problem in problems]
    notes = guides["notes"]
    if not isinstance(notes, dict) or not set(notes) <= set(ids):
        raise RuntimeError(
            f"guides.json notes name problems the bank does not have: {sorted(set(notes) - set(ids))}"
        )
    for problem_id, text in notes.items():
        if not named(text) or not text.isascii():
            raise RuntimeError(f"{problem_id}: a guide note is non-empty ASCII text")
        for furniture in GUIDE_FURNITURE:
            if furniture in text:
                raise RuntimeError(
                    f"{problem_id}: a guide note still carries {furniture!r}"
                )
    return {problem_id: notes[problem_id] for problem_id in ids if problem_id in notes}


def rust_guides(guides: dict) -> str:
    rows = [
        f"    ({rust_str(problem_id)}, {rust_str(text)}),"
        for problem_id, text in guides.items()
    ]
    return (
        "//! Generated by scripts/gen-problems.py; do not edit by hand.\n"
        "//!\n"
        "//! Solution notes for the report reviewer. The source and the MIT license\n"
        "//! they are used under are in problem-bank/guides.json.\n\n"
        "#[rustfmt::skip]\n"
        "pub const PROBLEM_GUIDES: &[(&str, &str)] = &[\n" + "\n".join(rows) + "\n];\n"
    )


def rust_topics(problems: list[dict]) -> str:
    rows = [
        f"    ({rust_str(problem['id'])}, {rust_strs(problem['topics'])}),"
        for problem in problems
    ]
    return (
        """//! Generated by scripts/gen-problems.py; do not edit by hand.\n\n\
#[rustfmt::skip]\n\
pub const PROBLEM_TOPICS: &[(&str, &[&str])] = &[\n"""
        + "\n".join(rows)
        + "\n];\n"
    )


def generated() -> dict[Path, str]:
    """Every file this script owns, as path -> exact contents."""
    files: dict[Path, str] = {}
    problems = validated_problems()
    variants = validated_variants(problems, read_json(JUDGE_SOURCE))
    # Every consumer that has an id and wants the page or the title reads this
    # map, rather than rebuilding it from the pages or re-deriving the slug.
    # Only the paths that start from a published id or name fetch it: an old
    # link, history saved before pages had names, and the lobby's opt-in toggle.
    # A scenario link loads a page and a judge that carry no published id; the
    # page names its published title once, in the `source` it shows small.
    pages = {}
    for problem in problems:
        entry = variants[problem["id"]]
        page = candidate_problem(entry)
        pages[problem["id"]] = {
            "page": page["page"],
            "title": page["title"],
            "source": page["source"],
            **({"default": True} if problem["id"] == DEFAULT_PROBLEM_ID else {}),
        }
        files[OUTPUT_DIR / f"{page['page']}.json"] = json.dumps(page, indent=2) + "\n"
        files[JUDGE_OUTPUT_DIR / f"{page['page']}.json"] = (
            json.dumps(entry["judge"], indent=2) + "\n"
        )
    files[PAGE_MAP_OUTPUT] = json.dumps(pages, indent=2) + "\n"
    files[RUST_TOPICS] = rust_topics(problems)
    files[RUST_VARIANTS] = rust_variants(problems, variants)
    files[RUST_GUIDES] = rust_guides(validated_guides(problems))
    return files


def orphans(files: dict[Path, str]) -> list[Path]:
    """Generated files this run would not write.

    A problem removed from the bank leaves its statement and its judge on disk
    otherwise, and for the judge that means an answer key still being served for
    a problem nobody can be given any more.
    """
    on_disk = (path for directory in DIRS for path in sorted(directory.glob("*.json")))
    return [path for path in on_disk if path not in files]


def drifted(files: dict[Path, str]) -> list[str]:
    """What `--check` reports, as repo-relative paths, in either direction."""
    changed = [
        str(path.relative_to(ROOT))
        for path, text in files.items()
        if not path.exists() or path.read_text() != text
    ]
    removed = [
        f"{path.relative_to(ROOT)} (no longer in the bank)" for path in orphans(files)
    ]
    return sorted(changed + removed)


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
