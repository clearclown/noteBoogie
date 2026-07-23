//! End-to-end generation flow against an in-process mock gRPC sidecar.
//!
//! Covers what handlers_it cannot: the background `tokio::spawn` loop in
//! `generate_audiobook` — the sidecar client call, per-chapter result
//! persistence (relative audio path + transcript array wrapping), and the
//! keep-going behavior when one chapter fails.

use std::time::Duration;

use bytes::Bytes;
use gateway::sidecar::pb::podcast_sidecar_server::{PodcastSidecar, PodcastSidecarServer};
use gateway::sidecar::pb::{
    CreatePodcastRequest, CreatePodcastResponse, PingRequest, PingResponse,
};
use gateway::{db, handlers, sidecar};
use reinhardt::{Handler, Method, Request, Response, ServerRouter, StatusCode};
use serde_json::Value;
use tonic::{Request as TonicRequest, Response as TonicResponse, Status};

struct MockSidecar;

#[tonic::async_trait]
impl PodcastSidecar for MockSidecar {
    async fn ping(
        &self,
        _request: TonicRequest<PingRequest>,
    ) -> Result<TonicResponse<PingResponse>, Status> {
        Ok(TonicResponse::new(PingResponse {
            ok: true,
            version: "mock".into(),
        }))
    }

    async fn create_podcast(
        &self,
        request: TonicRequest<CreatePodcastRequest>,
    ) -> Result<TonicResponse<CreatePodcastResponse>, Status> {
        let req = request.into_inner();
        // Failure injection: a chapter whose content carries the marker fails
        // permanently — the gateway loop must log and continue.
        if req.content.contains("FAILME") {
            return Err(Status::internal("mock generation failure"));
        }
        Ok(TonicResponse::new(CreatePodcastResponse {
            final_output_file_path: format!(
                "/tmp/mockdata/podcasts/episodes/{}/audio/{}.mp3",
                req.episode_name, req.episode_name
            ),
            // podcast-creator returns the transcript as an ARRAY of dialogues.
            transcript_json: r#"[{"speaker":"Mentor","dialogue":"こんにちは"}]"#.into(),
            outline_json: r#"{"segments":[]}"#.into(),
        }))
    }
}

fn router() -> ServerRouter {
    let mut r = ServerRouter::new()
        .endpoint(handlers::get_audiobook)
        .endpoint(handlers::generate_audiobook);
    let _ = r.register_all_routes();
    r
}

fn post_json(path: &str, body: &str) -> Request {
    Request::builder()
        .method(Method::POST)
        .uri(path)
        .header("content-type", "application/json")
        .body(Bytes::from(body.to_owned()))
        .build()
        .unwrap()
}

fn get(path: &str) -> Request {
    Request::builder()
        .method(Method::GET)
        .uri(path)
        .body(Bytes::new())
        .build()
        .unwrap()
}

fn json_body(resp: &Response) -> Value {
    serde_json::from_slice(&resp.body).expect("response body is JSON")
}

async fn spawn_mock_sidecar() -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        tonic::transport::Server::builder()
            .add_service(PodcastSidecarServer::new(MockSidecar))
            .serve_with_incoming(tokio_stream::wrappers::TcpListenerStream::new(listener))
            .await
            .unwrap();
    });
    format!("http://{addr}")
}

/// Poll the audiobook detail until `pred` holds (or time out).
async fn wait_for(id: &str, pred: impl Fn(&Value) -> bool) -> Value {
    for _ in 0..100 {
        let resp = router().handle(get(&format!("/audiobooks/{id}"))).await.unwrap();
        let detail = json_body(&resp);
        if pred(&detail) {
            return detail;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    panic!("timed out waiting for generation state");
}

#[tokio::test]
async fn background_generation_persists_results_and_survives_failures() {
    let addr = spawn_mock_sidecar().await;
    // The background loop reads the sidecar address from the environment.
    unsafe { std::env::set_var("SIDECAR_GRPC_ADDR", &addr) };

    // Direct client sanity check against the mock.
    assert!(sidecar::ping(&addr).await.unwrap());

    let conn = db::connect_mem().await;
    conn.query(
        "CREATE type::thing('episode_profile','book_navigator') SET \
         name='book_navigator', num_segments=3, default_briefing='b' RETURN NONE;\
         CREATE type::thing('speaker_profile','book_navigator_mentor') SET \
         name='book_navigator_mentor' RETURN NONE;",
    )
    .await
    .unwrap()
    .check()
    .unwrap();
    db::set_for_test(conn);

    // --- Happy path: two chapters generate end to end ---
    let filler = "本文。".repeat(100);
    let content = format!("# 第一章\nA {filler}\n\n# 第二章\nB {filler}");
    let body = serde_json::json!({"audiobook_name": "Mock Book", "content": content}).to_string();
    let resp = router().handle(post_json("/audiobooks/generate", &body)).await.unwrap();
    assert_eq!(resp.status, StatusCode::CREATED);
    let id = json_body(&resp)["audiobook_id"].as_str().unwrap().to_string();

    let detail = wait_for(&id, |d| {
        d["chapters"]
            .as_array()
            .map(|cs| cs.iter().all(|c| c["audio_file"].is_string()))
            .unwrap_or(false)
    })
    .await;

    let chapters = detail["chapters"].as_array().unwrap();
    assert_eq!(chapters.len(), 2);
    for chapter in chapters {
        let audio = chapter["audio_file"].as_str().unwrap();
        // Persisted RELATIVE to PODCASTS_FOLDER (#1030), not the sidecar's
        // absolute path.
        assert!(
            audio.starts_with("episodes/") && audio.ends_with(".mp3"),
            "relative audio path, got {audio}"
        );
    }

    // Transcript array was wrapped into the option<object> schema shape.
    let db = db::get().unwrap();
    #[derive(serde::Deserialize)]
    struct Row {
        transcript: Option<serde_json::Value>,
    }
    let rows: Vec<Row> = db
        .query("SELECT transcript FROM episode WHERE type::string(audiobook) = $ab")
        .bind(("ab", id.clone()))
        .await
        .unwrap()
        .take(0)
        .unwrap();
    assert_eq!(rows.len(), 2);
    for row in rows {
        let t = row.transcript.expect("transcript persisted");
        assert_eq!(t["transcript"][0]["dialogue"], "こんにちは");
    }

    // --- Failure path: chapter 1 fails, chapter 2 still generates ---
    let content = format!("# 第一章\nFAILME {filler}\n\n# 第二章\nOK {filler}");
    let body = serde_json::json!({"audiobook_name": "Half Fail", "content": content}).to_string();
    let resp = router().handle(post_json("/audiobooks/generate", &body)).await.unwrap();
    let id = json_body(&resp)["audiobook_id"].as_str().unwrap().to_string();

    let detail = wait_for(&id, |d| {
        // Done when the SECOND chapter has audio.
        d["chapters"]
            .as_array()
            .and_then(|cs| cs.get(1))
            .map(|c| c["audio_file"].is_string())
            .unwrap_or(false)
    })
    .await;
    let chapters = detail["chapters"].as_array().unwrap();
    assert!(
        chapters[0]["audio_file"].is_null(),
        "failed chapter stays audio-less"
    );
    assert!(chapters[1]["audio_file"].is_string(), "later chapter unaffected");

    // The failure is persisted and surfaced to clients (migration 26) — the
    // UI must be able to distinguish "failed" from "still generating".
    let error = chapters[0]["generation_error"]
        .as_str()
        .expect("generation_error persisted for the failed chapter");
    assert!(error.contains("mock generation failure"), "got: {error}");
    assert!(chapters[1]["generation_error"].is_null());
}
