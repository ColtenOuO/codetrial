//! The problem bank as the agent sees it: variants, judges and starters.
//!
//! Split out of `tests/agent.rs`, which is still the test target: cargo
//! discovers only `tests/*.rs`, so this compiles as a module of that one
//! binary rather than relinking the crate for a file of its own.
//!
//! `include_str!` resolves against this file, so every fixture path here is
//! one directory deeper than it was in the parent.

use super::*;

#[test]
fn problem_bank_matches_contract() {
    assert!(PROBLEMS.len() >= 6);
    for problem in PROBLEMS {
        assert!(!problem.id.is_empty());
        assert!(!problem.title.is_empty());
        assert!(!problem.summary.is_empty());
        assert!(!problem.optimal.is_empty());
        assert!(!problem.pitfalls.is_empty());

        // Every problem in this table has a variant, which the generator cannot
        // see: it holds the bank to variants.json, and the text rules there,
        // but not to what `PROBLEMS` lists.
        let variant = problem.variant();
        assert_eq!(variant.hints.len(), 3, "{}", problem.id);

        // The ladder this replaced was the optimal approach cut into three
        // pieces, so the second request heard the technique by name and the
        // third heard the algorithm. A rung may point at the idea; it may not
        // carry the private walkthrough's wording.
        for hint in variant.hints {
            assert!(
                shared_run(hint, problem.optimal) < 5,
                "{} hint repeats the optimal approach: {hint}",
                problem.id
            );
        }
    }
    assert_eq!(get_problem(None).id, DEFAULT_PROBLEM_ID);
    assert_eq!(get_problem(Some("missing")).id, DEFAULT_PROBLEM_ID);
}

#[test]
fn problem_bank_matches_imported_golden() {
    let mut problems: Vec<_> = PROBLEMS
        .iter()
        .map(|problem| {
            json!({
                "difficulty": problem.difficulty,
                "id": problem.id,
                "optimal": problem.optimal,
                "pitfalls": problem.pitfalls,
                "summary": problem.summary,
                "title": problem.title,
            })
        })
        .collect();
    problems.sort_by(|left, right| {
        left["id"]
            .as_str()
            .unwrap_or_default()
            .cmp(right["id"].as_str().unwrap_or_default())
    });
    let actual = json!({
        "defaultProblemId": DEFAULT_PROBLEM_ID,
        "problems": problems,
    });
    let path = "tests/golden/problems.json";

    if std::env::var_os("UPDATE_PROBLEM_GOLDEN").is_some() {
        let mut text = serde_json::to_string_pretty(&actual).expect("problems should serialize");
        text.push('\n');
        std::fs::write(path, text).expect("problem fixture should write");
    }

    let expected: Value =
        serde_json::from_str(&std::fs::read_to_string(path).expect("problem fixture should read"))
            .expect("golden problem fixture should parse");

    assert_eq!(actual, expected);
}

#[test]
fn every_problem_has_bounded_ordered_question_metadata() {
    assert_eq!(PROBLEMS.len(), 151);
    for problem in PROBLEMS {
        let metadata = problem.question_metadata();
        assert_eq!(metadata.difficulty, problem.difficulty, "{}", problem.id);
        assert!(
            (1..=8).contains(&metadata.competencies.len()),
            "{}",
            problem.id
        );
        let unique = metadata
            .competencies
            .iter()
            .collect::<std::collections::HashSet<_>>();
        assert_eq!(unique.len(), metadata.competencies.len(), "{}", problem.id);
        assert_eq!(metadata.reacto_stages, &ReactoStage::ALL, "{}", problem.id);
        assert_eq!(metadata.follow_up_directions.len(), ReactoStage::ALL.len());
        assert!(
            metadata
                .expected_discussion_points
                .contains(&problem.optimal)
        );
        for (follow_up, stage) in metadata.follow_up_directions.iter().zip(ReactoStage::ALL) {
            assert_eq!(follow_up.stage, stage, "{}", problem.id);
            assert!(!follow_up.direction.trim().is_empty());
        }
    }
    assert!(topics_for("not-a-problem").is_none());
}
