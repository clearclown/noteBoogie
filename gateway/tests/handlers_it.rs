//! Integration tests that drive the handlers through reinhardt-web's own
//! `ServerRouter` (routing, extractors, Request/Response) — no TCP, no Docker.
//!
//! DB-backed scenarios live in a single test because each `#[tokio::test]` owns
//! its runtime, and the in-memory SurrealDB connection (installed in the process
//! global) must outlive every request that uses it.

use bytes::Bytes;
use gateway::{db, handlers};
use reinhardt::{Handler, Method, Request, Response, ServerRouter, StatusCode};
use serde_json::Value;

fn router() -> ServerRouter {
    let mut r = ServerRouter::new()
        .endpoint(handlers::health)
        .endpoint(handlers::list_audiobooks)
        .endpoint(handlers::get_audiobook)
        .endpoint(handlers::delete_audiobook)
        .endpoint(handlers::generate_audiobook);
    let _ = r.register_all_routes();
    r
}

fn get(path: &str) -> Request {
    Request::builder()
        .method(Method::GET)
        .uri(path)
        .body(Bytes::new())
        .build()
        .unwrap()
}

fn delete(path: &str) -> Request {
    Request::builder()
        .method(Method::DELETE)
        .uri(path)
        .body(Bytes::new())
        .build()
        .unwrap()
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

fn json_body(resp: &Response) -> Value {
    serde_json::from_slice(&resp.body).expect("response body is JSON")
}

#[tokio::test]
async fn health_returns_ok() {
    let resp = router().handle(get("/health")).await.unwrap();
    assert_eq!(resp.status, StatusCode::OK);
    assert_eq!(json_body(&resp)["status"], "ok");
}

#[tokio::test]
async fn unknown_route_errors_at_router_level() {
    // The router reports an unmatched route as Err(NotFound); the HTTP server
    // layer is what turns that into a 404 response to the client.
    let res = router().handle(get("/no-such-route")).await;
    assert!(res.is_err(), "expected routing error for unknown path");
}

#[tokio::test]
async fn db_backed_handler_flows() {
    // One in-memory DB for the whole test (kept alive by this runtime).
    let conn = db::connect_mem().await;
    conn.query(
        "CREATE type::thing('episode_profile','book_navigator') SET \
         name='book_navigator', num_segments=3, default_briefing='メンター briefing' RETURN NONE;\
         CREATE type::thing('speaker_profile','book_navigator_mentor') SET \
         name='book_navigator_mentor' RETURN NONE;",
    )
    .await
    .unwrap()
    .check()
    .unwrap();
    db::set_for_test(conn);

    // --- 400: neither source_id nor content ---
    let resp = router()
        .handle(post_json("/audiobooks/generate", r#"{"audiobook_name":"X"}"#))
        .await
        .unwrap();
    assert_eq!(resp.status, StatusCode::BAD_REQUEST);
    assert!(json_body(&resp)["error"]
        .as_str()
        .unwrap()
        .contains("source_id or content"));

    // --- 400: unknown episode profile ---
    let body = r##"{"audiobook_name":"X","content":"# A\nbody","episode_profile":"does_not_exist"}"##;
    let resp = router().handle(post_json("/audiobooks/generate", body)).await.unwrap();
    assert_eq!(resp.status, StatusCode::BAD_REQUEST);

    // --- 404: get missing ---
    let resp = router()
        .handle(get("/audiobooks/audiobook:missing"))
        .await
        .unwrap();
    assert_eq!(resp.status, StatusCode::NOT_FOUND);

    // --- 201: generate from inline markdown with two H1 chapters ---
    // Chapter bodies must clear the tiny-chapter fold threshold (200 chars).
    let filler = "本文。".repeat(100);
    let content = format!("# 第一章 序\nA {filler}\n\n# 第二章 本論\nB {filler}");
    let body = serde_json::json!({"audiobook_name": "Roundtrip Book", "content": content}).to_string();
    let resp = router().handle(post_json("/audiobooks/generate", &body)).await.unwrap();
    assert_eq!(resp.status, StatusCode::CREATED);
    let created = json_body(&resp);
    assert_eq!(created["chapter_count"], 2);
    assert_eq!(created["status"], "processing");
    let id = created["audiobook_id"].as_str().unwrap().to_string();

    // --- GET detail: audiobook + two ordered, named chapters (audio pending) ---
    let resp = router().handle(get(&format!("/audiobooks/{id}"))).await.unwrap();
    assert_eq!(resp.status, StatusCode::OK);
    let detail = json_body(&resp);
    assert_eq!(detail["name"], "Roundtrip Book");
    let chapters = detail["chapters"].as_array().unwrap();
    assert_eq!(chapters.len(), 2);
    assert_eq!(chapters[0]["chapter_index"], 0);
    assert_eq!(chapters[0]["name"], "第1章：第一章 序");
    assert_eq!(chapters[1]["name"], "第2章：第二章 本論");
    assert!(chapters[0]["audio_file"].is_null());

    // --- list shows the audiobook ---
    let resp = router().handle(get("/audiobooks")).await.unwrap();
    assert_eq!(resp.status, StatusCode::OK);
    let list = json_body(&resp);
    assert!(list
        .as_array()
        .unwrap()
        .iter()
        .any(|a| a["id"].as_str() == Some(id.as_str())));

    // --- DELETE cascades, then 404 ---
    let resp = router().handle(delete(&format!("/audiobooks/{id}"))).await.unwrap();
    assert_eq!(resp.status, StatusCode::OK);
    let resp = router().handle(get(&format!("/audiobooks/{id}"))).await.unwrap();
    assert_eq!(resp.status, StatusCode::NOT_FOUND);
}
