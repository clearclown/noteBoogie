//! Integration tests for the repository layer against an in-memory SurrealDB.
//! Hermetic: each test gets its own isolated `mem://` database, no Docker needed.

use gateway::db::connect_mem;
use gateway::repo;
use serde_json::json;

#[tokio::test]
async fn create_and_list_audiobook() {
    let db = connect_mem().await;
    let id = repo::create_audiobook(&db, "ab1", "My Book", Some("source:s1"), "brief", 2)
        .await
        .unwrap();
    assert_eq!(id, "audiobook:ab1");

    let rows = repo::list_audiobooks(&db).await.unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].id.as_deref(), Some("audiobook:ab1"));
    assert_eq!(rows[0].name.as_deref(), Some("My Book"));
    assert_eq!(rows[0].chapter_count, Some(2));
    assert_eq!(rows[0].source_id.as_deref(), Some("source:s1"));
}

#[tokio::test]
async fn get_audiobook_some_and_none() {
    let db = connect_mem().await;
    repo::create_audiobook(&db, "abx", "X", None, "b", 0)
        .await
        .unwrap();

    let found = repo::get_audiobook(&db, "audiobook:abx").await.unwrap();
    assert!(found.is_some());
    assert_eq!(found.unwrap().name.as_deref(), Some("X"));

    let missing = repo::get_audiobook(&db, "audiobook:nope").await.unwrap();
    assert!(missing.is_none());
}

#[tokio::test]
async fn chapters_are_returned_in_index_order() {
    let db = connect_mem().await;
    repo::create_audiobook(&db, "abc", "Book", None, "b", 3)
        .await
        .unwrap();
    let ep = json!({"name": "book_navigator"});
    let sp = json!({"name": "book_navigator_mentor"});
    // Insert out of order to prove ORDER BY chapter_index.
    for (part, idx, title) in [("e2", 2, "Third"), ("e0", 0, "First"), ("e1", 1, "Second")] {
        repo::create_chapter_episode(
            &db, part, "abc", &format!("第{}章：{}", idx + 1, title), &ep, &sp, "brief",
            "body text", idx, title,
        )
        .await
        .unwrap();
    }

    let chapters = repo::get_chapters(&db, "audiobook:abc").await.unwrap();
    let indices: Vec<i64> = chapters.iter().map(|c| c.chapter_index.unwrap()).collect();
    assert_eq!(indices, vec![0, 1, 2]);
    assert_eq!(chapters[0].chapter_title.as_deref(), Some("First"));
    assert!(chapters.iter().all(|c| c.audio_file.is_none()));
}

#[tokio::test]
async fn set_episode_result_records_audio_and_json() {
    let db = connect_mem().await;
    repo::create_audiobook(&db, "abr", "Book", None, "b", 1)
        .await
        .unwrap();
    let ep = json!({"name": "book_navigator"});
    let sp = json!({"name": "book_navigator_mentor"});
    let eid = repo::create_chapter_episode(
        &db, "er0", "abr", "第1章：Intro", &ep, &sp, "brief", "body", 0, "Intro",
    )
    .await
    .unwrap();

    repo::set_episode_result(
        &db,
        &eid,
        "file:///tmp/ch0.mp3",
        r#"{"transcript":[{"speaker":"Mentor","dialogue":"hi"}]}"#,
        r#"{"segments":[]}"#,
    )
    .await
    .unwrap();

    let chapters = repo::get_chapters(&db, "audiobook:abr").await.unwrap();
    assert_eq!(chapters.len(), 1);
    assert_eq!(chapters[0].audio_file.as_deref(), Some("file:///tmp/ch0.mp3"));
}

#[tokio::test]
async fn delete_audiobook_cascades_and_returns_audio_paths() {
    let db = connect_mem().await;
    repo::create_audiobook(&db, "abd", "Book", None, "b", 2)
        .await
        .unwrap();
    let ep = json!({"name": "book_navigator"});
    let sp = json!({"name": "book_navigator_mentor"});
    let e0 = repo::create_chapter_episode(&db, "ed0", "abd", "c0", &ep, &sp, "b", "x", 0, "C0")
        .await
        .unwrap();
    repo::create_chapter_episode(&db, "ed1", "abd", "c1", &ep, &sp, "b", "y", 1, "C1")
        .await
        .unwrap();
    repo::set_episode_result(&db, &e0, "file:///tmp/c0.mp3", "null", "null")
        .await
        .unwrap();

    let removed_files = repo::delete_audiobook(&db, "audiobook:abd").await.unwrap();
    assert_eq!(removed_files, vec!["file:///tmp/c0.mp3".to_string()]);

    // Audiobook + all chapters are gone.
    assert!(repo::get_audiobook(&db, "audiobook:abd").await.unwrap().is_none());
    assert!(repo::get_chapters(&db, "audiobook:abd").await.unwrap().is_empty());
}

#[tokio::test]
async fn episode_profile_lite_and_speaker_existence() {
    let db = connect_mem().await;
    db.query(
        "CREATE type::thing('episode_profile','book_navigator') SET \
         name='book_navigator', num_segments=3, default_briefing='メンター' RETURN NONE;\
         CREATE type::thing('speaker_profile','book_navigator_mentor') SET \
         name='book_navigator_mentor' RETURN NONE;",
    )
    .await
    .unwrap()
    .check()
    .unwrap();

    let p = repo::get_episode_profile_lite(&db, "book_navigator")
        .await
        .unwrap()
        .expect("profile exists");
    assert_eq!(p.name, "book_navigator");
    assert_eq!(p.num_segments, Some(3));
    assert_eq!(p.default_briefing.as_deref(), Some("メンター"));

    assert!(repo::get_episode_profile_lite(&db, "missing").await.unwrap().is_none());
    assert!(repo::speaker_profile_exists(&db, "book_navigator_mentor").await.unwrap());
    assert!(!repo::speaker_profile_exists(&db, "nope").await.unwrap());
}

#[tokio::test]
async fn source_lite_reads_full_text_and_title() {
    let db = connect_mem().await;
    db.query(
        "CREATE type::thing('source','s1') SET full_text='# 第一章\\n本文', title='テスト本' RETURN NONE",
    )
    .await
    .unwrap()
    .check()
    .unwrap();

    let s = repo::get_source_lite(&db, "source:s1")
        .await
        .unwrap()
        .expect("source exists");
    assert!(s.full_text.unwrap().contains("第一章"));
    assert_eq!(s.title.as_deref(), Some("テスト本"));

    assert!(repo::get_source_lite(&db, "source:missing").await.unwrap().is_none());
}
