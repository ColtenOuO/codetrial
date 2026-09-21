//! The integrity trail: what is signed into it and what it refuses to claim.
//!
//! Split out of `tests/agent.rs`, which is still the test target: cargo
//! discovers only `tests/*.rs`, so this compiles as a module of that one
//! binary rather than relinking the crate for a file of its own.
//!
//! `include_str!` resolves against this file, so every fixture path here is
//! one directory deeper than it was in the parent.

use super::*;

#[test]
fn severe_face_events_preserve_the_integrity_chain() {
    let mut state = RuntimeState::default();
    let severe = integrity_event(IntegrityEventInput {
        seq: 1,
        prev_hash: "",
        event_type: "FACE_MISSING_SEVERE",
        at: "2026-08-15T00:00:00.000Z",
        severity: "critical",
        source: "camera",
        duration_ms: 15_000,
        detail: Some("faces=0;confidence=0.00;duration_ms=15000"),
    });
    apply_data_event(&mut state, "integrity", &severe, TEST_REACTION_COOLDOWN_S);
    assert_eq!(state.integrity_events.len(), 1);

    let previous_hash = severe["hash"].as_str().unwrap();
    let next = integrity_event(IntegrityEventInput {
        seq: 2,
        prev_hash: previous_hash,
        event_type: "FACE_DETECTED",
        at: "2026-08-15T00:00:01.000Z",
        severity: "info",
        source: "camera",
        duration_ms: 0,
        detail: Some("faces=1;confidence=0.99;duration_ms=0"),
    });
    apply_data_event(&mut state, "integrity", &next, TEST_REACTION_COOLDOWN_S);
    assert_eq!(state.integrity_events.len(), 2);
}

/// Both halves of the `detail` bound, held against each other.
///
/// `tests/fixtures/integrity-chain.json` proves an 80-character detail survives
/// the round trip, which catches a browser that outgrows the agent: the
/// regenerated fixture carries a longer detail and the agent refuses it. It
/// cannot catch the other direction. If the agent's bound rises and the
/// browser's does not, every fixture still passes, because a shorter detail is
/// always verifiable; the two would sit diverged until a real interview
/// produced a detail in the gap between them.
#[test]
fn the_integrity_detail_bound_is_the_same_number_on_both_sides() {
    let browser = std::fs::read_to_string("web/lib.js").expect("web/lib.js is readable");
    let declaration = "export const INTEGRITY_DETAIL_MAX = ";
    let start = browser
        .find(declaration)
        .expect("web/lib.js declares INTEGRITY_DETAIL_MAX")
        + declaration.len();
    let rest = &browser[start..];
    let end = rest.find(';').expect("the declaration ends in a semicolon");
    let browser_max: usize = rest[..end]
        .trim()
        .parse()
        .expect("INTEGRITY_DETAIL_MAX is a number");

    assert_eq!(
        browser_max,
        codetrial::agent::MAX_INTEGRITY_TEXT,
        "web/lib.js truncates `detail` at {browser_max} and the agent at {}; the \
         producer and the verifier hash different bytes, so every event longer \
         than the smaller bound is refused and the chain stops there",
        codetrial::agent::MAX_INTEGRITY_TEXT
    );
}

#[test]
fn localized_camera_labels_survive_integrity_verification() {
    let mut state = RuntimeState::default();
    let detail = "camera=Κάμερα Café (046d:086b)";
    apply_data_event(
        &mut state,
        "integrity",
        &integrity_event(IntegrityEventInput {
            seq: 1,
            prev_hash: "",
            event_type: "MEDIA_PREFLIGHT_PASSED",
            at: "2026-08-15T00:00:00.000Z",
            severity: "info",
            source: "preflight",
            duration_ms: 0,
            detail: Some(detail),
        }),
        TEST_REACTION_COOLDOWN_S,
    );
    assert_eq!(state.integrity_events[0]["detail"], detail);
}

/// A review event may name an event that eviction already removed. Refusing to
/// store it is right; refusing every event after it is not, and that is what
/// used to happen because the refusal ran before the chain cursor moved.
///
/// Retention is this process's problem. Tampering is the producer's. Only the
/// second one is allowed to end the chain.
#[test]
fn a_review_event_naming_an_evicted_source_does_not_end_the_chain() {
    let mut state = RuntimeState::default();
    let mut chain = Chain::new();

    // Evidence, not heartbeats. A heartbeat never reaches the buffer at all, so
    // citing one would prove the citation invalid for the wrong reason and no
    // eviction would happen: this test quietly stopped exercising eviction when
    // liveness moved to its own field.
    chain.push(&mut state, "SESSION_START", "info");
    for _ in 0..30 {
        chain.push(&mut state, "CAMERA_STOPPED", "high");
    }
    assert_eq!(state.integrity_events.len(), 25, "the cap must have bitten");
    assert!(
        !state
            .integrity_events
            .iter()
            .any(|event| event["seq"].as_u64() == Some(2)),
        "seq 2 is evidence the cap pushed out, which is what makes the citation stale"
    );

    chain.push_citing(&mut state, "REVIEW_EVENT", "warning", &["2"]);
    assert!(
        !stored_types(&state).contains(&"REVIEW_EVENT"),
        "a review event with an unretained source should not be stored"
    );

    // The chain, however, kept going.
    chain.push(&mut state, "CAMERA_STOPPED", "high");
    assert!(
        stored_types(&state).contains(&"CAMERA_STOPPED"),
        "a retention drop ended the chain: every later event was refused"
    );
}
