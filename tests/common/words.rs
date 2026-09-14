//! Comparing prose word by word, for the tests that hold interviewer text to
//! what it must not repeat.
//!
//! A file of its own rather than a function in `mod.rs`, because every test
//! that declares `mod common` compiles all of it, and a helper only two of
//! them call is dead code in the rest, which `clippy -D warnings` refuses. The
//! two that need it include this file by `#[path]`.

/// The longest run of consecutive words two texts share, compared as lowercase
/// letters and digits so punctuation cannot hide a copied phrase.
pub fn shared_run(left: &str, right: &str) -> usize {
    let (left, right) = (words(left), words(right));
    let mut longest = 0;
    for start in 0..left.len() {
        for other in 0..right.len() {
            let run = left[start..]
                .iter()
                .zip(&right[other..])
                .take_while(|(a, b)| a == b)
                .count();
            longest = longest.max(run);
        }
    }
    longest
}

/// Lowercase ASCII words, split on anything that is not a letter or a digit
/// and inside identifiers where their case changes, the way `spelled_words` in
/// scripts/problem_bank/rules.py splits them: `minStackCreate` is min, stack,
/// create, and `LRUCache` is lru, cache.
pub fn words(text: &str) -> Vec<String> {
    let characters = text.chars().collect::<Vec<_>>();
    let mut words = Vec::new();
    let mut current = String::new();
    for (at, &character) in characters.iter().enumerate() {
        if !character.is_ascii_alphanumeric() {
            if !current.is_empty() {
                words.push(std::mem::take(&mut current));
            }
            continue;
        }
        let previous = at.checked_sub(1).map(|before| characters[before]);
        let next = characters.get(at + 1);
        let lower_then_upper = character.is_ascii_uppercase()
            && previous
                .is_some_and(|before| before.is_ascii_lowercase() || before.is_ascii_digit());
        let acronym_ends = character.is_ascii_uppercase()
            && previous.is_some_and(|before| before.is_ascii_uppercase())
            && next.is_some_and(char::is_ascii_lowercase);
        if (lower_then_upper || acronym_ends) && !current.is_empty() {
            words.push(std::mem::take(&mut current));
        }
        current.push(character.to_ascii_lowercase());
    }
    if !current.is_empty() {
        words.push(current);
    }
    words
}

/// Whether `text` names a published problem by its title: the rule
/// `names_source` in scripts/problem_bank/rules.py applies, so the generator,
/// the prompt tests and the behaviour check agree.
///
/// A title that is a single ordinary word, "Candy" or "Triangle", is exempt,
/// because a scenario uses the word. Any other title counts when a run of
/// consecutive words spells it with the spaces gone: "LRUCache", "lru cache"
/// and "3 Sum" all name their problems, and "those 3 sums" does not.
pub fn names_title(title: &str, text: &str) -> bool {
    if title
        .chars()
        .all(|character| character.is_ascii_alphabetic())
    {
        return false;
    }
    let target = words(title).concat();
    let text = words(text);
    (0..text.len()).any(|start| {
        let mut joined = String::new();
        for word in &text[start..] {
            joined.push_str(word);
            if joined == target {
                return true;
            }
            if joined.len() >= target.len() {
                return false;
            }
        }
        false
    })
}
