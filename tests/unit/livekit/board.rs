//! The `tests` module of `src/livekit/board.rs`, which declares this file by
//! path. Everything here reaches into that file through `super`, so it is a
//! unit test and not an integration test: private items are in scope.

use super::*;

const CANDIDATE: &str = "candidate-4f2c";

fn refusal(
    mode: InterviewMode,
    topic: &str,
    sender: &str,
    declared_length: Option<u64>,
) -> Option<&'static str> {
    board_stream_refusal(mode, topic, sender, CANDIDATE, declared_length)
}

#[test]
fn accepts_a_board_from_the_candidate() {
    assert_eq!(
        refusal(
            InterviewMode::Whiteboard,
            TOPIC_BOARD_IMAGE,
            CANDIDATE,
            Some(96_318)
        ),
        None
    );

    // A browser that did not declare a length still gets a board through: the
    // ceiling is then enforced while reading, which is what `drain` does.
    assert_eq!(
        refusal(
            InterviewMode::Whiteboard,
            TOPIC_BOARD_IMAGE,
            CANDIDATE,
            None
        ),
        None
    );
}

#[test]
fn refuses_a_board_no_interview_asked_for() {
    // The editor's own interview has no board, so a stream on this topic is a
    // client sending one anyway.
    assert_eq!(
        refusal(
            InterviewMode::Coding,
            TOPIC_BOARD_IMAGE,
            CANDIDATE,
            Some(4_096)
        ),
        Some("this interview has no whiteboard")
    );
    assert_eq!(
        refusal(
            InterviewMode::Whiteboard,
            "lk.agent.pre-connect-audio-buffer",
            CANDIDATE,
            Some(4_096)
        ),
        Some("not the board topic")
    );
}

#[test]
fn refuses_a_board_from_anyone_but_the_candidate() {
    // Every participant holds `canPublishData`, so the identity is the only
    // thing between the interviewer's eyes and a board an observer drew.
    assert_eq!(
        refusal(
            InterviewMode::Whiteboard,
            TOPIC_BOARD_IMAGE,
            "observer-9a11",
            Some(4_096)
        ),
        Some("not the candidate")
    );
}

#[test]
fn refuses_a_header_larger_than_a_board() {
    assert_eq!(
        refusal(
            InterviewMode::Whiteboard,
            TOPIC_BOARD_IMAGE,
            CANDIDATE,
            Some(MAX_BOARD_BYTES as u64 + 1)
        ),
        Some("declared larger than a board may be")
    );

    // The ceiling itself is allowed: a bound refused at its own value is a
    // different bound from the one the browser is written against.
    assert_eq!(
        refusal(
            InterviewMode::Whiteboard,
            TOPIC_BOARD_IMAGE,
            CANDIDATE,
            Some(MAX_BOARD_BYTES as u64)
        ),
        None
    );
}

/// The browser's own output, not a restatement of it: the cases come from
/// `web/lib.js` by way of the wire-fixture generator, so a producer that
/// starts spelling the attribute differently fails here rather than in an
/// interview.
#[test]
fn reads_the_stroke_count_the_browser_sent() {
    let fixture: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string("tests/fixtures/board-stream.json")
            .expect("the board wire fixture is generated into tests/fixtures"),
    )
    .expect("the fixture is JSON");
    assert_eq!(
        fixture["topic"].as_str(),
        Some(TOPIC_BOARD_IMAGE),
        "the browser and the agent disagree about the board's topic"
    );
    let cases = fixture["cases"].as_array().expect("cases is an array");

    // Asserted, because a fixture that lost its cases would otherwise pass this
    // test by having nothing to check.
    assert_eq!(cases.len(), 3);
    let counts = cases
        .iter()
        .map(|case| {
            let attributes = case["options"]["attributes"]
                .as_object()
                .expect("a stream header carries attributes")
                .iter()
                .map(|(key, value)| {
                    (
                        key.clone(),
                        value.as_str().expect("attributes are strings").to_string(),
                    )
                })
                .collect::<HashMap<_, _>>();
            strokes_from_attributes(&attributes)
        })
        .collect::<Vec<_>>();
    assert_eq!(counts, vec![3, 214, 0]);
}

#[test]
fn a_board_with_no_usable_count_still_arrives() {
    assert_eq!(strokes_from_attributes(&HashMap::new()), 0);
    assert_eq!(
        strokes_from_attributes(&HashMap::from([(
            "strokes".to_string(),
            "many".to_string()
        )])),
        0
    );
    assert_eq!(
        strokes_from_attributes(&HashMap::from([("strokes".to_string(), "-4".to_string())])),
        0
    );
}

/// The floor between two boards, at its edge: a whole interval since the last
/// one is not too soon, and a millisecond short of it is.
#[test]
fn a_board_waits_out_the_send_interval_and_no_longer() {
    let sent = Instant::now();
    assert!(!too_soon(None, sent), "the first board is never too soon");
    assert!(too_soon(Some(sent), sent));
    assert!(too_soon(
        Some(sent),
        sent + BOARD_SEND_INTERVAL - Duration::from_millis(1)
    ));
    assert!(!too_soon(Some(sent), sent + BOARD_SEND_INTERVAL));
}

/// What `drain` hands the room loop for `chunks`, if anything.
async fn drained(chunks: Vec<Result<Vec<u8>, &'static str>>) -> Option<BoardSnapshot> {
    let (tx, mut rx) = channel(BOARD_QUEUE);
    drain(futures_util::stream::iter(chunks), 5, tx).await;
    rx.try_recv().ok()
}

#[tokio::test]
async fn a_board_is_read_whole_up_to_the_bound_and_not_past_it() {
    let board = drained(vec![Ok(vec![1, 2]), Ok(vec![3])])
        .await
        .expect("a board in pieces arrives whole");
    assert_eq!(board.bytes, [1, 2, 3]);
    assert_eq!(board.strokes, 5);

    // Exactly the bound is a board; one byte past it, even in a later chunk, is
    // not, and the first chunk alone is not enough to decide that.
    let full = drained(vec![Ok(vec![0; MAX_BOARD_BYTES - 1]), Ok(vec![0])]).await;
    assert_eq!(full.map(|board| board.bytes.len()), Some(MAX_BOARD_BYTES));
    assert!(
        drained(vec![Ok(vec![0; MAX_BOARD_BYTES]), Ok(vec![0])])
            .await
            .is_none()
    );
}

#[tokio::test]
async fn a_broken_or_empty_stream_is_no_board() {
    assert!(
        drained(vec![Ok(vec![1, 2]), Err("reset")]).await.is_none(),
        "half a JPEG is not shown"
    );
    assert!(drained(Vec::new()).await.is_none());
}

/// A Gemini Live socket that completes setup and reports every frame it is
/// sent afterwards.
async fn live_session() -> (
    GeminiLiveSession,
    tokio::sync::mpsc::UnboundedReceiver<String>,
) {
    use futures_util::SinkExt;
    use tokio_tungstenite::tungstenite::protocol::Message;

    let config = crate::config::load_from_pairs([
        ("LIVEKIT_URL", "wss://example.livekit.cloud"),
        ("LIVEKIT_API_KEY", "devkey"),
        ("LIVEKIT_API_SECRET", "devsecret"),
        ("GOOGLE_API_KEY", "google"),
    ])
    .unwrap();
    let boot = crate::runtime::bootstrap(&config, "interview-board", Some("two-sum"), 45);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let (frames, received) = tokio::sync::mpsc::unbounded_channel();
    tokio::spawn(async move {
        let (socket, _) = listener.accept().await.unwrap();
        let mut socket = tokio_tungstenite::accept_async(socket).await.unwrap();
        let _ = socket.next().await;
        socket
            .send(Message::Text(r#"{"setupComplete":{}}"#.into()))
            .await
            .unwrap();
        while let Some(Ok(frame)) = socket.next().await {
            if let Message::Text(text) = frame {
                let _ = frames.send(text.to_string());
            }
        }
    });
    let session = crate::gemini::open_live_session_at(&format!("ws://{address}"), &boot, None)
        .await
        .unwrap();
    (session, received)
}

async fn next_frame(frames: &mut tokio::sync::mpsc::UnboundedReceiver<String>) -> String {
    tokio::time::timeout(Duration::from_secs(5), frames.recv())
        .await
        .expect("a frame within five seconds")
        .expect("the socket is still open")
}

/// A board that arrives is counted, kept, and shown; one that arrives inside
/// the interval is counted and kept but waits, so `read_board` still answers
/// with it.
#[tokio::test]
async fn a_board_is_counted_kept_and_sent_once_per_interval() {
    let (mut gemini, mut frames) = live_session().await;
    let (mut board, _rx) = Board::new();
    let mut state = RuntimeState {
        interview_mode: InterviewMode::Whiteboard,
        ..RuntimeState::default()
    };

    let first = BoardSnapshot {
        bytes: vec![0xff, 0xd8, 0xff],
        strokes: 4,
    };
    pump_board(&mut board, &mut gemini, &mut state, first)
        .await
        .unwrap();
    assert_eq!(state.board_snapshots, 1);
    assert_eq!(state.board_strokes, 4);
    assert!(state.last_board_at_ms.is_some());
    let sent = board.last_sent.expect("the first board goes out");
    let frame = next_frame(&mut frames).await;
    assert!(frame.contains("image/jpeg"), "{frame}");
    assert!(frame.contains("/9j/"), "the board's own bytes: {frame}");

    let second = BoardSnapshot {
        bytes: vec![0xff, 0xd8, 0x00],
        strokes: 6,
    };
    pump_board(&mut board, &mut gemini, &mut state, second)
        .await
        .unwrap();
    assert_eq!(state.board_snapshots, 2);
    assert_eq!(state.board_strokes, 6);
    assert_eq!(board.latest.as_deref(), Some(&[0xff, 0xd8, 0x00][..]));
    assert_eq!(board.last_sent, Some(sent), "too soon to go out");

    // `read_board` is an explicit ask, so it is answered inside the interval.
    resend(&mut board, &mut gemini).await.unwrap();
    assert!(board.last_sent > Some(sent));
    let frame = next_frame(&mut frames).await;
    assert!(frame.contains("/9gA"), "the newer board: {frame}");
}

#[tokio::test]
async fn asking_for_a_board_before_one_arrived_sends_nothing() {
    let (mut gemini, mut frames) = live_session().await;
    let (mut board, _rx) = Board::new();
    resend(&mut board, &mut gemini).await.unwrap();
    assert_eq!(board.last_sent, None);
    assert!(frames.try_recv().is_err());
}
