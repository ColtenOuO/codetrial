import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "gen_problems", ROOT / "scripts/gen-problems.py"
)
GEN = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(GEN)


class ProblemMetadataGeneratorTests(unittest.TestCase):
    def test_rejects_duplicate_ids_and_invalid_topic_lists(self):
        valid = {"id": "one", "difficulty": "Easy", "topics": ["Array"]}
        for problems in (
            [valid, valid],
            [{**valid, "topics": []}],
            [{**valid, "topics": ["Array", "Array"]}],
            [{**valid, "difficulty": "Extreme"}],
        ):
            # Handed in by path: the gate runs these cases on a thread pool.
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "problems.json"
                path.write_text(json.dumps(problems))
                with self.assertRaises(RuntimeError):
                    GEN.validated_problems(path)

    def test_public_projection_has_a_closed_schema_and_ordered_stages(self):
        problem = {
            "id": "one",
            "difficulty": "Medium",
            "topics": ["Dynamic Programming"],
        }
        metadata = GEN.public_metadata(problem)
        self.assertEqual(set(metadata), GEN.PUBLIC_METADATA_KEYS)
        self.assertNotIn("Dynamic Programming", json.dumps(metadata))
        self.assertEqual(metadata["reactoStages"], list(GEN.REACTO_STAGES))
        self.assertEqual(
            [item["stage"] for item in metadata["followUpDirections"]],
            list(GEN.REACTO_STAGES),
        )
        serialized = json.dumps(metadata).lower()
        for forbidden in ("optimal", "pitfall", "hintladder", "expecteddiscussion"):
            self.assertNotIn(forbidden, serialized)


class VariantValidationTests(unittest.TestCase):
    """What stands between the published problem and the candidate's page."""

    problem = {
        "id": "coin-change",
        "title": "Coin Change",
        "difficulty": "Medium",
        "topics": ["Dynamic Programming"],
        "statement": ["Given coins and an amount, return the fewest coins."],
        "examples": [{"input": "coins = [1,2,5], amount = 11", "output": "3"}],
        "constraints": ["0 <= amount <= 10^4"],
        "starterCode": {
            "python": "class Solution:\n    def coinChange(self, coins, amount):\n        pass\n",
            "javascript": "function coinChange(coins, amount) {\n}\n",
            "c": "int coinChange(int* coins, int coinsSize, int amount) {\n}\n",
        },
    }
    judge = {
        "kind": "function",
        "entry": "coinChange",
        "checker": "exact",
        "paramNames": ["coins", "amount"],
        "cases": [
            {"label": "makes amount", "input": [[1, 2, 5], 11], "expected": 3},
            {"label": "coins overshoot", "input": [[5, 7], 1], "expected": -1},
        ],
    }
    variant = {
        "title": "Kiosk Token Payout",
        "entry": "fewestTokens",
        "parameters": {"coins": "tokens"},
        "brief": ["Implement fewestTokens(tokens, amount) for the kiosk."],
        "contract": "fewestTokens(tokens, amount) returns the fewest tokens summing to amount, or -1.",
        "examples": [{"case": 1}],
        "clarifications": [
            {"question": "Unlimited supply?", "answer": "Yes."},
            {"question": "Impossible amount?", "answer": "Return -1."},
            {"question": "How large?", "answer": "Up to 10^4."},
        ],
        "followUps": ["Limited counts?", "Return the tokens?"],
        "hints": ["Try 6 with 1, 3, 4.", "Smaller amounts first?", "What is zero?"],
    }

    def rejects(self, message, judge=None, **changes):
        variant = {**self.variant, **changes}
        with self.assertRaisesRegex(RuntimeError, message):
            GEN.validated_variant(self.problem, judge or self.judge, variant)

    def test_the_declared_names_replace_the_published_ones_everywhere_they_ship(self):
        posed = GEN.validated_variant(self.problem, self.judge, self.variant)
        self.assertEqual(posed["judge"]["entry"], "fewestTokens")
        self.assertEqual(posed["judge"]["paramNames"], ["tokens", "amount"])
        for code in posed["problem"]["starterCode"].values():
            self.assertIn("fewestTokens(", code)
            self.assertNotIn("coin", code.lower())
        # A label is read on every run, and only the declared words move in it.
        self.assertEqual(
            [case["label"] for case in posed["judge"]["cases"]],
            ["makes amount", "tokens overshoot"],
        )
        # The bank keeps what LeetCode publishes; only the posed copies move.
        self.assertEqual(self.judge["entry"], "coinChange")
        self.assertEqual(
            posed["examples"], [{"input": "tokens = [5,7], amount = 1", "output": "-1"}]
        )

    def test_the_source_title_and_site_stay_out_of_what_the_candidate_reads(self):
        self.rejects("names the source title", title="Coin Change Kiosk")
        self.rejects(
            "names the source title",
            brief=["A coin change problem: implement fewestTokens(tokens, amount)."],
        )
        self.rejects("source site", hints=["As on LeetCode.", "b", "c"])
        self.rejects(
            "contract names the source title", contract="Coin change, renamed."
        )

    def test_a_function_problem_takes_a_new_entry_point(self):
        self.rejects("not a new name", entry="coinChange")
        self.rejects("not a new name", entry="COIN_change")
        variant = {key: value for key, value in self.variant.items() if key != "entry"}
        with self.assertRaisesRegex(RuntimeError, "declares a new entry"):
            GEN.validated_variant(self.problem, self.judge, variant)
        self.rejects("declares a new entry", className="KioskPayout")
        self.rejects("renames only judge parameters", parameters={"money": "cash"})
        self.rejects("is the published name", parameters={"coins": "coinChange"})
        self.rejects("is the published name", terms={"Coin": "CoinChange"})

    def test_a_class_problem_takes_a_new_class_name_everywhere_it_is_called(self):
        problem = {
            **self.problem,
            "id": "lru-cache",
            "title": "LRU Cache",
            "starterCode": {
                "python": "class LRUCache:\n    def get(self, key): pass\n",
                "c": "LRUCache* lRUCacheCreate(int capacity) {}\nint lRUCacheGet(LRUCache* obj, int key) {}\n",
            },
            # Published as calls; the judge case holds the same arguments.
            "examples": [{"input": "LRUCache(2), get(1)", "output": "[null,-1]"}],
        }
        judge = {
            "kind": "class",
            "entry": "lru",
            "className": "LRUCache",
            "checker": "exact",
            "cases": [
                {
                    "label": "fill",
                    "input": [["LRUCache", "get"], [[2], [1]]],
                    "expected": [None, -1],
                },
                {
                    "label": "evict",
                    "input": [["LRUCache", "get"], [[1], [3]]],
                    "expected": [None, -1],
                },
            ],
        }
        variant = {
            **{
                key: value
                for key, value in self.variant.items()
                if key not in ("entry", "parameters")
            },
            "className": "ThumbnailStore",
            "brief": ["Implement ThumbnailStore(capacity) with get(key)."],
            "contract": "ThumbnailStore keeps the most recent entries.",
            "examples": [{"case": 1}],
        }
        # The published call-style example is case 0, argument for argument.
        with self.assertRaisesRegex(RuntimeError, "show published case 0"):
            GEN.validated_variant(
                problem, judge, {**variant, "examples": [{"case": 0}]}
            )
        posed = GEN.validated_variant(problem, judge, variant)
        # C spells the class as a prefix on every function.
        self.assertIn("thumbnailStoreGet(", posed["problem"]["starterCode"]["c"])
        self.assertEqual(posed["judge"]["className"], "ThumbnailStore")
        self.assertNotIn(
            "entry", posed["judge"], "the published camel-case name ships nowhere"
        )
        self.assertEqual(
            posed["judge"]["cases"][0]["input"][0], ["ThumbnailStore", "get"]
        )
        self.assertIn(
            "class ThumbnailStore:", posed["problem"]["starterCode"]["python"]
        )
        # Everything that ships; the posed bank entry keeps its published examples
        # on the server.
        shipped = [posed["judge"], posed["problem"]["starterCode"], posed["examples"]]
        self.assertNotIn("LRUCache", json.dumps(shipped))
        self.assertNotIn("lRUCache", json.dumps(shipped))
        with self.assertRaisesRegex(RuntimeError, "declares a new className"):
            GEN.validated_variant(
                problem, judge, {k: v for k, v in variant.items() if k != "className"}
            )
        with self.assertRaisesRegex(RuntimeError, "not a new name"):
            GEN.validated_variant(problem, judge, {**variant, "className": "LruCache"})

    def test_the_title_counts_however_it_is_spaced_but_not_as_a_near_miss(self):
        self.assertTrue(GEN.names_source("LRU Cache", "Implement LRUCache now."))
        self.assertTrue(GEN.names_source("Min Stack", "MinStack* minStackCreate() {"))
        self.assertTrue(GEN.names_source("3Sum", "This is basically 3 Sum."))
        self.assertFalse(GEN.names_source("3Sum", "Those 3 sums cancel."))
        self.assertFalse(
            GEN.names_source("Candy", "Hand out candy."), "one ordinary word"
        )
        # A parameter named like a title word does not excuse naming the title.
        self.assertTrue(GEN.names_source("Merge Intervals", "Classic merge intervals."))

    def test_published_sample_text_is_refused_wherever_the_browser_gets_it(self):
        sentence = {"label": "sentence", "input": [[1], 2], "expected": "coin change"}
        judge = {**self.judge, "cases": [*self.judge["cases"], sentence]}
        self.rejects("judge case 'sentence' names the source title", judge=judge)
        problem = {
            **self.problem,
            "starterCode": {"python": "# Coin Change\ndef fewestTokens(): pass\n"},
        }
        with self.assertRaisesRegex(
            RuntimeError, "python starter names the source title"
        ):
            GEN.validated_variant(problem, self.judge, self.variant)

    def test_the_brief_names_the_function_the_judge_calls(self):
        self.rejects("never names fewestTokens", brief=["Pay out the amount."])

    def test_examples_are_judge_cases_and_not_only_the_published_ones(self):
        self.rejects("names one judge case", examples=[{"case": 2}])
        self.rejects("names one judge case", examples=[{"input": "coins = [5,7]"}])
        self.rejects("show published case 0", examples=[{"case": 0}])
        # Nor when every case is a published one: the judge needs one of its own.
        problem = {
            **self.problem,
            "examples": [
                *self.problem["examples"],
                {"input": "coins = [5,7], amount = 1", "output": "-1"},
            ],
        }
        with self.assertRaisesRegex(RuntimeError, "show published case 1"):
            GEN.validated_variant(problem, self.judge, self.variant)
        # Not even beside one of the judge's own: "pwwkew" names its problem
        # however many other examples keep it company.
        self.rejects("show published case 0", examples=[{"case": 1}, {"case": 0}])
        # The same input spelled another way is still the published one.
        problem = {
            **self.problem,
            "examples": [{"input": "coins = [1, 2, 5], amount = 11.00", "output": "3"}],
        }
        with self.assertRaisesRegex(RuntimeError, "show published case 0"):
            GEN.validated_variant(
                problem, self.judge, {**self.variant, "examples": [{"case": 0}]}
            )
        # A tree with its gaps is not the same tree without them.
        self.assertNotEqual(
            GEN.input_values("root = [1,2,null,3]"), GEN.input_values("root = [1,2,3]")
        )

    def test_prose_may_not_walk_through_a_published_example(self):
        # The published example here is coins 1, 2, 5 and amount 11.
        self.rejects(
            "repeats a published example",
            hints=["Try 1, 2, 5 and 11 by hand.", "b", "c"],
        )
        self.rejects(
            "repeats a published example",
            followUps=["What about 11 with 5, 2 and 1?", "b"],
        )
        GEN.validated_variant(
            self.problem,
            self.judge,
            {**self.variant, "hints": ["Try 1, 2, 4 and 11 by hand.", "b", "c"]},
        )
        self.assertTrue(
            GEN.quotes_example(("paper", "title"), "Line up paper and title.")
        )
        self.assertFalse(
            GEN.quotes_example(("paper", "title"), "A newspaper headline.")
        )
        # A small board of 0s and 1s is not quoted by an output of 0s and 1s, but
        # a longer run of few values in the published order is.
        board = (1.0, 1.0, 1.0, 0.0)
        self.assertFalse(
            GEN.quotes_example(board, "board becomes [[0,0,0],[1,1,1],[0,0,0]]")
        )
        self.assertTrue(
            GEN.quotes_example((3.0, 2.0, 2.0, 3.0, 3.0), "Take 3, 2, 2, 3 with 3.")
        )
        # An example's own output text is read like any other prose.
        self.rejects(
            "repeats a published example",
            examples=[{"case": 1, "output": "-1, unlike 1, 2, 5 and 11"}],
        )

    def test_a_published_argument_is_published_whatever_comes_with_it(self):
        self.assertEqual(
            GEN.example_arguments("head = [1,2,3,4,5], k = 2"),
            [(1.0, 2.0, 3.0, 4.0, 5.0)],
        )
        self.assertEqual(
            GEN.example_arguments('s = "a, b, c: done", n = 3'), [("a, b, c: done",)]
        )
        # An escaped backslash does not escape the quote after it.
        self.assertEqual(
            GEN.example_arguments(r's = "ab\\", t = "hello, world"'),
            [("hello, world",)],
        )
        # The published coins with a different amount are still the published coins.
        problem = {
            **self.problem,
            "examples": [{"input": "coins = [1,2,5,7], amount = 11", "output": "2"}],
        }
        judge = {
            **self.judge,
            "cases": [
                *self.judge["cases"],
                {"label": "same coins", "input": [[1, 2, 5, 7], 3], "expected": 2},
            ],
        }
        with self.assertRaisesRegex(RuntimeError, "show published case 2"):
            GEN.validated_variant(
                problem, judge, {**self.variant, "examples": [{"case": 2}]}
            )

    def test_an_example_may_say_its_output_the_way_a_reader_needs_it(self):
        posed = GEN.validated_variant(
            self.problem,
            self.judge,
            {**self.variant, "examples": [{"case": 1, "output": "-1, nothing fits"}]},
        )
        self.assertEqual(posed["examples"][0]["output"], "-1, nothing fits")

    def test_hints_are_three_spoken_rungs(self):
        self.rejects("hints must hold 3..3", hints=["only", "two"])
        self.rejects("no backticks", hints=["Use `dp`.", "b", "c"])
        self.rejects("printable ASCII", hints=["Try \u2264 6.", "b", "c"])

    def test_a_case_label_may_not_name_its_source_or_the_trick(self):
        for label in (
            "leetcode sample",
            "example board",
            "coinchange overflow",
            "greedy fails",
            "min heap order",
        ):
            judge = {
                **self.judge,
                "cases": [{"label": label, "input": [[1], 1], "expected": 1}],
            }
            with self.assertRaisesRegex(RuntimeError, "gives it away"):
                GEN.check_labels("coin-change", judge)
        GEN.check_labels("coin-change", self.judge)
        twice = {**self.judge, "cases": [self.judge["cases"][0]] * 2}
        with self.assertRaisesRegex(RuntimeError, "labels repeat"):
            GEN.check_labels("coin-change", twice)
        # Distinct in the bank, the same once a parameter rename reaches them.
        judge = {
            **self.judge,
            "cases": [
                {**self.judge["cases"][0], "label": "tokens run out"},
                {**self.judge["cases"][1], "label": "coins run out"},
            ],
        }
        self.rejects("labels repeat", judge=judge)
        # A rename is checked as it ships, not as the bank spelled it.
        self.rejects("gives it away", parameters={"coins": "heap"})
        # A published name counts however it is spaced.
        spaced = {
            **self.judge,
            "cases": [{"label": "coin change edge", "input": [[1], 1], "expected": 1}],
        }
        with self.assertRaisesRegex(RuntimeError, "gives it away"):
            GEN.check_labels("coin-change", spaced)

    def test_the_page_carries_the_scenario_and_only_the_source_title(self):
        posed = GEN.validated_variant(self.problem, self.judge, self.variant)
        page = GEN.candidate_problem({**posed, "variant": self.variant})
        self.assertEqual(page["page"], "kiosk-token-payout")
        self.assertEqual(
            set(page),
            {
                "page",
                "title",
                "source",
                "difficulty",
                "brief",
                "examples",
                "starterCode",
                "interviewMetadata",
            },
        )
        # Named once, in the field the page shows small, and nowhere else.
        self.assertEqual(page.pop("source"), "Coin Change")
        self.assertNotIn("Coin Change", json.dumps(page))

    def test_rust_literals_survive_quotes_backslashes_and_non_ascii(self):
        self.assertEqual(GEN.rust_str('say "hi" \\ go'), '"say \\"hi\\" \\\\ go"')
        self.assertEqual(GEN.rust_str("10\u2264n"), '"10\\u{2264}n"')


class GuideValidationTests(unittest.TestCase):
    """The reviewer's notes, held to the rules the import used to be trusted with."""

    problems = [{"id": "two-sum"}, {"id": "3sum"}]
    license = [
        "MIT License",
        "Copyright (c) 2024 Someone",
        "Permission is hereby granted, free of charge",
    ]

    def guides(self, **changes):
        # Handed in rather than patched onto the module: the gate runs these
        # cases on a thread pool, and a patched global is every case's global.
        document = {
            "source": "https://example.test",
            "note": "n",
            "license": self.license,
            "notes": {"3sum": "Pin one value.", "two-sum": "Remember what went by."},
        }
        document.update(changes)
        return document

    def test_notes_come_back_in_bank_order_and_become_a_rust_table(self):
        guides = GEN.validated_guides(self.problems, self.guides())
        self.assertEqual(list(guides), ["two-sum", "3sum"])
        table = GEN.rust_guides(guides)
        self.assertIn('("3sum", "Pin one value."),', table)
        self.assertIn("problem-bank/guides.json", table)

    def test_notes_without_their_license_or_for_unknown_problems_are_refused(self):
        with self.assertRaisesRegex(RuntimeError, "license"):
            GEN.validated_guides(self.problems, self.guides(license=["MIT License"]))
        with self.assertRaisesRegex(RuntimeError, "does not have"):
            GEN.validated_guides(self.problems, self.guides(notes={"lru-cache": "x"}))

    def test_page_furniture_left_by_the_import_is_refused(self):
        for furniture in GEN.GUIDE_FURNITURE:
            notes = {"3sum": f"Sort. {furniture} here."}
            with self.assertRaisesRegex(RuntimeError, "still carries"):
                GEN.validated_guides(self.problems, self.guides(notes=notes))


class PageNameTests(unittest.TestCase):
    def test_a_page_named_like_another_problems_id_is_refused(self):
        ids = ["rotate-array", "rotate-list"]
        GEN.check_page_names(["Carousel Slot Shift", "Playlist Tail"], ids)
        with self.assertRaisesRegex(RuntimeError, "must not equal a problem id"):
            GEN.check_page_names(["Rotate List", "Playlist Tail"], ids)
        with self.assertRaisesRegex(RuntimeError, "unique as page names"):
            GEN.check_page_names(["Playlist Tail!", "playlist tail"], ids)

    def test_a_page_is_named_for_its_scenario(self):
        self.assertEqual(GEN.page_slug("Kiosk Token Payout"), "kiosk-token-payout")
        self.assertEqual(GEN.page_slug("  H/W: 2-Way  Sync! "), "h-w-2-way-sync")

    def test_no_checker_is_named_after_a_problem(self):
        # Checkers ship in every judge and are this project's own names, so
        # they are named for what they compare rather than for a problem.
        titles = {
            # The bank by path, not `GEN.SOURCE`: another case patches that
            # module global while this one may be running on the pool.
            GEN.camel_words(problem["title"])
            for problem in GEN.read_json(ROOT / "problem-bank" / "problems.json")
        }
        checkers = {
            judge["checker"] for judge in GEN.read_json(GEN.JUDGE_SOURCE).values()
        }
        self.assertIn("indexPair", checkers)
        self.assertEqual({c for c in checkers if GEN.camel_words(c) in titles}, set())


if __name__ == "__main__":
    unittest.main()
