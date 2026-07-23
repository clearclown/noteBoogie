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

const AB_FIELDS: &str = "type::string(id) AS id, name, source_id, briefing, \
     chapter_count, type::string(created) AS created";

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
    let q = "SELECT type::string(id) AS id, name, chapter_index, chapter_title, audio_file, \
             generation_error, feedback \
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
        // created is set explicitly so schemaless test DBs (mem://) match the
        // SCHEMAFULL default from migration 24.
        "CREATE type::thing('audiobook', $aid) SET \
         name = $name, source_id = $src, briefing = $br, chapter_count = $cc, \
         created = time::now() RETURN NONE",
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

/// Everything needed to re-run one chapter's generation.
#[derive(Debug, serde::Deserialize)]
pub struct ChapterRetryInfo {
    pub content: Option<String>,
    pub briefing: Option<String>,
    pub episode_profile_name: Option<String>,
    pub speaker_profile_name: Option<String>,
}

/// Load a chapter episode's stored inputs for a retry (by full episode id).
pub async fn get_chapter_retry_info(
    db: &Surreal<Any>,
    episode_full_id: &str,
) -> DbResult<Option<ChapterRetryInfo>> {
    let mut rows: Vec<ChapterRetryInfo> = db
        .query(
            "SELECT content, briefing, episode_profile.name AS episode_profile_name, \
             speaker_profile.name AS speaker_profile_name \
             FROM episode WHERE type::string(id) = $id AND audiobook != NONE",
        )
        .bind(("id", episode_full_id.to_string()))
        .await?
        .take(0)?;
    Ok(rows.drain(..).next())
}

/// Clear a chapter's failure state before re-running it.
pub async fn clear_episode_error(db: &Surreal<Any>, episode_full_id: &str) -> DbResult<()> {
    db.query(
        "UPDATE episode SET generation_error = NONE WHERE type::string(id) = $id RETURN NONE",
    )
    .bind(("id", episode_full_id.to_string()))
    .await?
    .check()?;
    Ok(())
}

/// Remap a host-absolute figure path into this process's DATA_FOLDER.
///
/// Ingest stores absolute host paths (e.g. /Users/x/noteBoogie/data/books/…);
/// inside a container the same file lives under /app/data/…. When the stored
/// path has a "/data/" segment, rebuild it against the local data folder.
pub fn remap_into_data_folder(path: &str, data_folder: &str) -> Option<String> {
    let idx = path.rfind("/data/")?;
    let suffix = &path[idx + "/data/".len()..];
    Some(format!("{}/{}", data_folder.trim_end_matches('/'), suffix))
}

/// Convert a sidecar-produced audio path to the DB storage form: relative to
/// PODCASTS_FOLDER (e.g. "episodes/<id>/audio/<file>.mp3"), mirroring the
/// Python side's `to_relative_audio_path` (#1030). The API treats absolute
/// paths as legacy-invalid and refuses to serve them.
pub fn relative_audio_path(path: &str) -> String {
    let path = path.strip_prefix("file://").unwrap_or(path);
    match path.rfind("/podcasts/") {
        Some(idx) => path[idx + "/podcasts/".len()..].to_string(),
        None => path.to_string(),
    }
}

/// Record a permanent generation failure so the UI can surface it
/// (otherwise a failed chapter looks "generating" forever).
pub async fn set_episode_error(
    db: &Surreal<Any>,
    episode_full_id: &str,
    message: &str,
) -> DbResult<()> {
    db.query(
        "UPDATE episode SET generation_error = $msg WHERE type::string(id) = $id RETURN NONE",
    )
    .bind(("id", episode_full_id.to_string()))
    .bind(("msg", message.to_string()))
    .await?
    .check()?;
    Ok(())
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
    let audio_file = relative_audio_path(audio_file);
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

#[cfg(test)]
mod tests {
    use super::{relative_audio_path, remap_into_data_folder};

    #[test]
    fn relativizes_sidecar_output_paths() {
        assert_eq!(
            relative_audio_path("/tmp/data/podcasts/episodes/abc/audio/abc.mp3"),
            "episodes/abc/audio/abc.mp3"
        );
        assert_eq!(
            relative_audio_path("file:///app/data/podcasts/episodes/x/y.mp3"),
            "episodes/x/y.mp3"
        );
        assert_eq!(
            relative_audio_path("./data/podcasts/episodes/x/y.mp3"),
            "episodes/x/y.mp3"
        );
        // No podcasts root — left as-is (legacy-invalid on the API side).
        assert_eq!(relative_audio_path("/somewhere/else/a.mp3"), "/somewhere/else/a.mp3");
    }

    #[test]
    fn remaps_host_paths_into_the_local_data_folder() {
        assert_eq!(
            remap_into_data_folder(
                "/Users/x/noteBoogie/data/books/b/images/f.png",
                "/app/data"
            )
            .as_deref(),
            Some("/app/data/books/b/images/f.png")
        );
        assert!(remap_into_data_folder("/srv/elsewhere/f.png", "/app/data").is_none());
    }

    #[test]
    fn relative_audio_path_edge_cases() {
        // Already-relative value without a podcasts segment passes through.
        assert_eq!(relative_audio_path("episodes/x/y.mp3"), "episodes/x/y.mp3");
        assert_eq!(relative_audio_path(""), "");
        // Multiple occurrences: rfind keeps only the innermost remainder, so a
        // data dir that itself contains "/podcasts/" cannot leak into the value.
        assert_eq!(
            relative_audio_path("/srv/podcasts/data/podcasts/episodes/a.mp3"),
            "episodes/a.mp3"
        );
        // A path ENDING in /podcasts/ yields empty (degenerate but not a panic).
        assert_eq!(relative_audio_path("/data/podcasts/"), "");
    }
}
