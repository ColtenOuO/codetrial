//! The check scripts in scripts/, driven against a real server.
//!
//! Split out of `tests/web.rs`, which is still the test target: cargo
//! discovers only `tests/*.rs`, so this compiles as a module of that one
//! binary rather than relinking the crate for a file of its own.

use super::*;

#[tokio::test]
async fn server_check_accepts_running_rust_server() {
    let (mut config, cookie, db_path) = signed_in_web_config("server-check");
    config.web_dir = Path::new("web").to_path_buf();

    // A credentialled pool, because an empty one is no longer a server that can
    // exist: `run_web` refuses to start without LiveKit credentials, so the
    // check asserts `/api/token` mints rather than 500s.
    config.pool = primary_pool(
        "wss://example.livekit.cloud",
        "server-check-key",
        "server-check-secret",
    );
    let (base, server) = spawn_web_server(config).await;
    let output = tokio::task::spawn_blocking(move || {
        Command::new("sh")
            .arg("scripts/server-check.sh")
            .env("CODETRIAL_WEB_URL", base)
            .env("SERVER_CHECK_SESSION_COOKIE", cookie)
            .output()
    })
    .await
    .unwrap()
    .expect("frontend check should run");

    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );

    server.shutdown().await;
    remove_database(db_path).await;
}

/// The check signs itself in through `/api/login` rather than being handed a
/// cookie, so it works against a server it did not start.
#[tokio::test]
async fn server_check_signs_itself_in_against_an_external_server() {
    let (mut config, _, db_path) = signed_in_web_config("server-check-cookie");
    config.web_dir = Path::new("web").to_path_buf();
    config.pool = primary_pool(
        "wss://example.livekit.cloud",
        "server-check-key",
        "server-check-secret",
    );
    let (base, server) = spawn_web_server(config).await;
    let output = tokio::task::spawn_blocking(move || {
        Command::new("sh")
            .arg("scripts/server-check.sh")
            .env("CODETRIAL_WEB_URL", base)
            .env_remove("SERVER_CHECK_SESSION_COOKIE")
            .output()
    })
    .await
    .unwrap()
    .expect("frontend check should run");

    assert!(
        output.status.success(),
        "stdout: {}\nstderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    server.shutdown().await;
    remove_database(db_path).await;
}
