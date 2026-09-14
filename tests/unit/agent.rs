//! The `tests` module of `src/agent.rs`, which declares this file by path.
//! Everything here reaches into `src/agent.rs` through `super`, so private
//! items are in scope.

use super::{MIN_WRITTEN_CHARS, RuntimeState, added_characters, code_written};

fn count(template: &str, code: &str) -> usize {
    let template = template.chars().collect::<Vec<_>>();
    let code = code.chars().collect::<Vec<_>>();
    added_characters(&template, &code)
}

/// Exact counts, not only which side of the threshold they fall on: a table
/// that stopped counting shared characters, or a trim that dropped a differing
/// prefix, still agrees with the threshold on most whole starters.
#[test]
fn added_characters_counts_what_was_typed_in_order() {
    assert_eq!(count("return0;", "return0;"), 0, "nothing typed");
    assert_eq!(
        count("", "abc"),
        3,
        "an empty starter credits every character"
    );
    assert_eq!(count("abc", ""), 0, "deleting everything types nothing");

    // A differing first or last character is not part of the shared prefix or
    // suffix the table skips.
    assert_eq!(count("abc", "xbc"), 1);
    assert_eq!(count("abc", "abx"), 1);
    // After the trim, what is left still shares characters with the starter.
    assert_eq!(count("p1q2r", "p1Xq2Yr"), 2);
    assert_eq!(count("abcde", "aXbYcZdWe"), 4);
    assert_eq!(count("a1b2c3", "a9b8c7"), 3);
    // Deleting a comment is not typing, and does not cancel what was typed.
    assert_eq!(
        count("f(){//Thinkoutloud!return0;}", "f(){returnsqrt(x);}"),
        7
    );
}

/// Past the table's bound only what the lengths prove counts: a forged buffer
/// gets the length difference and no quadratic work, so these differ from the
/// exact answers (2000 and 5000) on purpose.
#[test]
fn added_characters_stops_counting_exactly_past_its_table() {
    assert_eq!(count(&"a".repeat(3000), &"b".repeat(2000)), 0);
    assert_eq!(count(&"a".repeat(1000), &"b".repeat(5000)), 4000);
    // At the bound the count is still exact.
    assert_eq!(count(&"a".repeat(1000), &"b".repeat(4000)), 4000);
}

/// The written-code threshold on both sides: four added characters are a
/// keystroke or two, five are the shortest answers the bank's starters take.
#[test]
fn code_written_starts_at_five_added_characters() {
    assert_eq!(MIN_WRITTEN_CHARS, 5);
    let written = |template: &str, code: &str| {
        code_written(&RuntimeState {
            code: code.to_string(),
            code_templates: [("python".to_string(), template.to_string())].into(),
            ..RuntimeState::default()
        })
    };
    assert!(!written("return 0", "return 0"));
    assert!(
        !written("return 0", "return x+yz0"),
        "four characters added in order"
    );
    assert!(
        written("return 0", "return xy+yz0"),
        "five characters added in order"
    );

    // Shorter than its starter, so the length proves nothing and the table
    // decides: the comment went and a real answer came.
    assert!(written("pass # think", "return a*b"));
}
