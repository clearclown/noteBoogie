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
        .endpoint(handlers::get_audiobook_figures)
        .endpoint(handlers::get_figure_image)
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
    assert!(chapters[0]["feedback"].is_null(), "unrated chapter -> null feedback");

    // --- feedback set via the API side (episode.feedback) surfaces in the
    //     chapter projection the tracklist renders ---
    let ch0 = chapters[0]["id"].as_str().unwrap().to_string();
    db::get()
        .unwrap()
        .query(format!(
            "UPDATE {ch0} SET feedback = 'up' RETURN NONE"
        ))
        .await
        .unwrap()
        .check()
        .unwrap();
    let resp = router().handle(get(&format!("/audiobooks/{id}"))).await.unwrap();
    let refreshed = json_body(&resp);
    assert_eq!(refreshed["chapters"][0]["feedback"], "up");

    // --- GET detail with a URL-ENCODED id (what the frontend actually sends;
    //     found broken by a live-browser check the mocked e2e couldn't catch) ---
    let encoded = id.replace(':', "%3A");
    let resp = router().handle(get(&format!("/audiobooks/{encoded}"))).await.unwrap();
    assert_eq!(resp.status, StatusCode::OK, "encoded record ids must decode");
    assert_eq!(json_body(&resp)["name"], "Roundtrip Book");

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

    // ===== generate from a source (source_id path) =====
    let db = db::get().unwrap();
    let filler2 = "内容。".repeat(120);
    db.query(
        "CREATE type::thing('source','bk1') SET \
         full_text = $ft, title = '実験の本' RETURN NONE",
    )
    .bind((
        "ft",
        format!("# 序章\nA {filler2}\n\n# 第二章\nB {filler2}\n\n# 第三章\nC {filler2}"),
    ))
    .await
    .unwrap()
    .check()
    .unwrap();

    // --- 404: unknown source ---
    let body = r#"{"audiobook_name":"S","source_id":"source:missing"}"#;
    let resp = router().handle(post_json("/audiobooks/generate", body)).await.unwrap();
    assert_eq!(resp.status, StatusCode::NOT_FOUND);

    // --- 201: max_chapters truncates, briefing_suffix lands in the briefing ---
    let body = serde_json::json!({
        "audiobook_name": "Source Book",
        "source_id": "source:bk1",
        "max_chapters": 2,
        "briefing_suffix": "追加指示：ゆっくり話す"
    })
    .to_string();
    let resp = router().handle(post_json("/audiobooks/generate", &body)).await.unwrap();
    assert_eq!(resp.status, StatusCode::CREATED);
    let created = json_body(&resp);
    assert_eq!(created["chapter_count"], 2, "3 chapters truncated to 2");
    let sid = created["audiobook_id"].as_str().unwrap().to_string();

    let resp = router().handle(get(&format!("/audiobooks/{sid}"))).await.unwrap();
    let detail = json_body(&resp);
    assert_eq!(detail["source_id"], "source:bk1");
    assert!(detail["briefing"]
        .as_str()
        .unwrap()
        .contains("追加指示：ゆっくり話す"));
    assert_eq!(detail["chapters"].as_array().unwrap().len(), 2);

    // ===== figures endpoints =====
    // Audiobook linked to the source ('sid') sees the source's figures.
    let img_path = std::env::temp_dir().join("gateway_fig_it_test.png");
    // Minimal valid PNG (1x1 transparent pixel).
    let png_bytes: &[u8] = &[
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48,
        0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00,
        0x00, 0x1F, 0x15, 0xC4, 0x89, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x44, 0x41, 0x54, 0x78,
        0x9C, 0x62, 0x00, 0x01, 0x00, 0x00, 0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00,
        0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
    ];
    std::fs::write(&img_path, png_bytes).unwrap();
    db.query(
        "CREATE type::thing('book_figure','f2') SET source = type::thing('source','bk1'), \
         page = 9, chapter_index = 1, path = $p, kind = 'figure', caption = '後の図' RETURN NONE; \
         CREATE type::thing('book_figure','f1') SET source = type::thing('source','bk1'), \
         page = 3, chapter_index = 0, path = '/nonexistent/gone.png', kind = 'full_page', caption = NONE RETURN NONE;",
    )
    .bind(("p", img_path.to_string_lossy().to_string()))
    .await
    .unwrap()
    .check()
    .unwrap();

    // --- figures list: ordered by page, caption/chapter_index round-trip ---
    let resp = router()
        .handle(get(&format!("/audiobooks/{sid}/figures")))
        .await
        .unwrap();
    assert_eq!(resp.status, StatusCode::OK);
    let figures = json_body(&resp);
    let figures = figures.as_array().unwrap();
    assert_eq!(figures.len(), 2);
    assert_eq!(figures[0]["page"], 3, "ordered by page ascending");
    assert_eq!(figures[1]["page"], 9);
    assert_eq!(figures[1]["caption"], "後の図");
    assert_eq!(figures[1]["chapter_index"], 1);
    let served_id = figures[1]["id"].as_str().unwrap().to_string();
    let missing_file_id = figures[0]["id"].as_str().unwrap().to_string();

    // --- figures list: audiobook without source_id -> empty array ---
    let body = serde_json::json!({"audiobook_name": "No Source", "content": content}).to_string();
    let resp = router().handle(post_json("/audiobooks/generate", &body)).await.unwrap();
    let nosrc_id = json_body(&resp)["audiobook_id"].as_str().unwrap().to_string();
    let resp = router()
        .handle(get(&format!("/audiobooks/{nosrc_id}/figures")))
        .await
        .unwrap();
    assert_eq!(resp.status, StatusCode::OK);
    assert_eq!(json_body(&resp).as_array().unwrap().len(), 0);

    // --- figures list: missing audiobook -> 404 ---
    let resp = router()
        .handle(get("/audiobooks/audiobook:nope/figures"))
        .await
        .unwrap();
    assert_eq!(resp.status, StatusCode::NOT_FOUND);

    // --- image serving: real file -> 200 + image/png bytes ---
    let resp = router()
        .handle(get(&format!("/figures/{served_id}/image")))
        .await
        .unwrap();
    assert_eq!(resp.status, StatusCode::OK);
    assert_eq!(
        resp.headers.get("content-type").unwrap().to_str().unwrap(),
        "image/png"
    );
    assert_eq!(&resp.body[..], png_bytes);

    // --- image serving: .jpg path -> image/jpeg content type ---
    let jpg_path = std::env::temp_dir().join("gateway_fig_it_test.jpg");
    std::fs::write(&jpg_path, b"\xFF\xD8\xFF\xE0fakejpg").unwrap();
    db.query(
        "CREATE type::thing('book_figure','f3') SET source = type::thing('source','bk1'), \
         page = 12, path = $p, kind = 'figure' RETURN NONE",
    )
    .bind(("p", jpg_path.to_string_lossy().to_string()))
    .await
    .unwrap()
    .check()
    .unwrap();
    let resp = router()
        .handle(get("/figures/book_figure:f3/image"))
        .await
        .unwrap();
    assert_eq!(resp.status, StatusCode::OK);
    assert_eq!(
        resp.headers.get("content-type").unwrap().to_str().unwrap(),
        "image/jpeg"
    );
    let _ = std::fs::remove_file(&jpg_path);

    // --- image serving: record exists but file is gone -> 404 ---
    let resp = router()
        .handle(get(&format!("/figures/{missing_file_id}/image")))
        .await
        .unwrap();
    assert_eq!(resp.status, StatusCode::NOT_FOUND);

    // --- image serving: unknown figure id -> 404 ---
    let resp = router()
        .handle(get("/figures/book_figure:nope/image"))
        .await
        .unwrap();
    assert_eq!(resp.status, StatusCode::NOT_FOUND);

    let _ = std::fs::remove_file(&img_path);
}
