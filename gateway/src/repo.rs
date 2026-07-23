//! SurrealDB queries for audiobooks and chapter episodes.
//!
//! Native `id` / `datetime` values are projected to strings in SurrealQL so the
//! SDK can deserialize into plain serde types. Write statements use `RETURN NONE`
//! to avoid round-tripping RecordId/Datetime back through serde.

use serde_json::Value;
use surrealdb::engine::any::Any;
use surrealdb::Surreal;

use crate::models::{Audiobook, BookFigure, ChapterEpisode, ProfileLite, SourceLite};

type DbResult<T> = Result<T, surrealdb::Error>;

const AB_FIELDS: &str =
    "type::string(id) AS id, name, source_id, briefing, chapter_count, created";

pub async fn list_audiobooks(db: &Surreal<Any>) -> DbResult<Vec<Audiobook>> {
    let q = format!("SELECT {AB_FIELDS} FROM audiobook ORDER BY created DESC");
    db.query(q).await?.take(0)
}

pub async fn get_audiobook(db: &Surreal<Any>, full_id: &str) -> DbResult<Option<Audiobook>> {
    let q = format!("SELECT {AB_FIELDS} FROM audiobook WHERE type::string(id) = $id");
    let mut rows: Vec<Audiobook> = db.query(q).bind(("id", full_id.to_string())).await?.take(0)?;
    Ok(rows.drain(..).next())
}

pub async fn get_chapters(db: &Surreal<Any>, audiobook_full_id: &str) -> DbResult<Vec<ChapterEpisode>> {
    let q = "SELECT type::string(id) AS id, name, chapter_index, chapter_title, audio_file \
             FROM episode WHERE type::string(audiobook) = $ab ORDER BY chapter_index ASC";
    db.query(q)
        .bind(("ab", audiobook_full_id.to_string()))
        .await?
        .take(0)
}

/// Returns the audio_file paths of the deleted chapters (for on-disk cleanup).
pub async fn delete_audiobook(db: &Surreal<Any>, full_id: &str) -> DbResult<Vec<String>> {
    #[derive(serde::Deserialize)]
    struct AudioOnly {
        audio_file: Option<String>,
    }
    let mut files: Vec<AudioOnly> = db
        .query("SELECT audio_file FROM episode WHERE type::string(audiobook) = $ab")
        .bind(("ab", full_id.to_string()))
        .await?
        .take(0)?;
    let paths: Vec<String> = files.drain(..).filter_map(|f| f.audio_file).collect();

    db.query(
        "DELETE episode WHERE type::string(audiobook) = $ab RETURN NONE; \
         DELETE audiobook WHERE type::string(id) = $ab RETURN NONE;",
    )
    .bind(("ab", full_id.to_string()))
    .await?
    .check()?;
    Ok(paths)
}

pub async fn get_episode_profile_lite(
    db: &Surreal<Any>,
    name: &str,
) -> DbResult<Option<ProfileLite>> {
    let mut rows: Vec<ProfileLite> = db
        .query("SELECT name, num_segments, default_briefing FROM episode_profile WHERE name = $n")
        .bind(("n", name.to_string()))
        .await?
        .take(0)?;
    Ok(rows.drain(..).next())
}

pub async fn speaker_profile_exists(db: &Surreal<Any>, name: &str) -> DbResult<bool> {
    let rows: Vec<ProfileLite> = db
        .query("SELECT name FROM speaker_profile WHERE name = $n")
        .bind(("n", name.to_string()))
        .await?
        .take(0)?;
    Ok(!rows.is_empty())
}

pub async fn get_source_lite(db: &Surreal<Any>, full_id: &str) -> DbResult<Option<SourceLite>> {
    let mut rows: Vec<SourceLite> = db
        .query("SELECT full_text, title FROM source WHERE type::string(id) = $id")
        .bind(("id", full_id.to_string()))
        .await?
        .take(0)?;
    Ok(rows.drain(..).next())
}

/// Create an audiobook with an explicit id; returns the full id ("audiobook:<part>").
pub async fn create_audiobook(
    db: &Surreal<Any>,
    id_part: &str,
    name: &str,
    source_id: Option<&str>,
    briefing: &str,
    chapter_count: i64,
) -> DbResult<String> {
    db.query(
        "CREATE type::thing('audiobook', $aid) SET \
         name = $name, source_id = $src, briefing = $br, chapter_count = $cc RETURN NONE",
    )
    .bind(("aid", id_part.to_string()))
    .bind(("name", name.to_string()))
    .bind(("src", source_id.map(|s| s.to_string())))
    .bind(("br", briefing.to_string()))
    .bind(("cc", chapter_count))
    .await?
    .check()?;
    Ok(format!("audiobook:{id_part}"))
}

/// Create one chapter episode linked to its audiobook; returns the full id.
#[allow(clippy::too_many_arguments)]
pub async fn create_chapter_episode(
    db: &Surreal<Any>,
    id_part: &str,
    audiobook_id_part: &str,
    name: &str,
    episode_profile: &Value,
    speaker_profile: &Value,
    briefing: &str,
    content: &str,
    chapter_index: i64,
    chapter_title: &str,
) -> DbResult<String> {
    db.query(
        "CREATE type::thing('episode', $eid) SET \
         name = $name, episode_profile = $ep, speaker_profile = $sp, briefing = $br, \
         content = $content, audiobook = type::thing('audiobook', $aid), \
         chapter_index = $idx, chapter_title = $title, audio_file = NONE RETURN NONE",
    )
    .bind(("eid", id_part.to_string()))
    .bind(("aid", audiobook_id_part.to_string()))
    .bind(("name", name.to_string()))
    .bind(("ep", episode_profile.clone()))
    .bind(("sp", speaker_profile.clone()))
    .bind(("br", briefing.to_string()))
    .bind(("content", content.to_string()))
    .bind(("idx", chapter_index))
    .bind(("title", chapter_title.to_string()))
    .await?
    .check()?;
    Ok(format!("episode:{id_part}"))
}

/// List a source's separated figures ordered by page (for the gallery).
pub async fn get_figures_for_source(
    db: &Surreal<Any>,
    source_full_id: &str,
) -> DbResult<Vec<BookFigure>> {
    let q = "SELECT type::string(id) AS id, page, chapter_index, kind, caption \
             FROM book_figure WHERE type::string(source) = $src ORDER BY page ASC";
    db.query(q)
        .bind(("src", source_full_id.to_string()))
        .await?
        .take(0)
}

/// Fetch one figure's on-disk image path by figure id (for serving the image).
pub async fn get_figure_path(db: &Surreal<Any>, figure_full_id: &str) -> DbResult<Option<String>> {
    #[derive(serde::Deserialize)]
    struct PathOnly {
        path: Option<String>,
    }
    let mut rows: Vec<PathOnly> = db
        .query("SELECT path FROM book_figure WHERE type::string(id) = $id")
        .bind(("id", figure_full_id.to_string()))
        .await?
        .take(0)?;
    Ok(rows.drain(..).next().and_then(|r| r.path))
}

/// Record a generated chapter's audio + transcript/outline (JSON strings).
pub async fn set_episode_result(
    db: &Surreal<Any>,
    episode_full_id: &str,
    audio_file: &str,
    transcript_json: &str,
    outline_json: &str,
) -> DbResult<()> {
    // The episode schema declares transcript/outline as option<object>, but
    // podcast-creator returns the transcript as an ARRAY of dialogues. Wrap
    // non-object values the same way the Python command does.
    fn as_object(raw: &str, wrap_key: &str) -> Value {
        let v: Value = serde_json::from_str(raw).unwrap_or(Value::Null);
        match v {
            Value::Null | Value::Object(_) => v,
            other => serde_json::json!({ wrap_key: other }),
        }
    }
    let transcript = as_object(transcript_json, "transcript");
    let outline = as_object(outline_json, "outline");
    db.query(
        "UPDATE episode SET audio_file = $audio, transcript = $tr, outline = $ol \
         WHERE type::string(id) = $id RETURN NONE",
    )
    .bind(("id", episode_full_id.to_string()))
    .bind(("audio", audio_file.to_string()))
    .bind(("tr", transcript))
    .bind(("ol", outline))
    .await?
    .check()?;
    Ok(())
}
