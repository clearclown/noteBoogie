//! API and persistence types. SurrealDB native `id`/`datetime` are projected to
//! strings in SurrealQL (see repo.rs) so these stay plain serde types.

use serde::{Deserialize, Serialize};

/// Audiobook summary row (list / detail header).
#[derive(Debug, Serialize, Deserialize)]
pub struct Audiobook {
    pub id: Option<String>,
    pub name: Option<String>,
    pub source_id: Option<String>,
    pub briefing: Option<String>,
    pub chapter_count: Option<i64>,
    #[serde(default)]
    pub created: Option<String>,
}

/// One chapter episode as exposed to clients.
#[derive(Debug, Serialize, Deserialize)]
pub struct ChapterEpisode {
    pub id: Option<String>,
    pub name: Option<String>,
    pub chapter_index: Option<i64>,
    pub chapter_title: Option<String>,
    pub audio_file: Option<String>,
    /// Permanent generation failure message (None while pending/succeeded).
    #[serde(default)]
    pub generation_error: Option<String>,
}

/// Audiobook + ordered chapters (GET /audiobooks/{id}).
#[derive(Debug, Serialize)]
pub struct AudiobookDetail {
    #[serde(flatten)]
    pub audiobook: Audiobook,
    pub chapters: Vec<ChapterEpisode>,
}

/// POST /audiobooks/generate request body.
#[derive(Debug, Deserialize)]
pub struct GenerateAudiobookRequest {
    pub audiobook_name: String,
    #[serde(default)]
    pub source_id: Option<String>,
    #[serde(default)]
    pub content: Option<String>,
    #[serde(default = "default_episode_profile")]
    pub episode_profile: String,
    #[serde(default = "default_speaker_profile")]
    pub speaker_profile: String,
    #[serde(default)]
    pub briefing_suffix: Option<String>,
    #[serde(default)]
    pub max_chapters: Option<usize>,
}

fn default_episode_profile() -> String {
    "book_navigator".to_string()
}
fn default_speaker_profile() -> String {
    "book_navigator_mentor".to_string()
}

/// POST /audiobooks/generate response.
#[derive(Debug, Serialize)]
pub struct GenerateAudiobookResponse {
    pub audiobook_id: String,
    pub audiobook_name: String,
    pub chapter_count: usize,
    pub status: String,
}

/// Minimal profile snapshot fields (scalars only — avoids RecordId/Datetime).
#[derive(Debug, Serialize, Deserialize)]
pub struct ProfileLite {
    pub name: String,
    #[serde(default)]
    pub num_segments: Option<i64>,
    #[serde(default)]
    pub default_briefing: Option<String>,
}

/// Source text used as audiobook input.
#[derive(Debug, Deserialize)]
pub struct SourceLite {
    pub full_text: Option<String>,
    pub title: Option<String>,
}

/// A separated book figure (migration 25) as exposed to the frontend gallery.
#[derive(Debug, Serialize, Deserialize)]
pub struct BookFigure {
    pub id: Option<String>,
    pub page: Option<i64>,
    pub chapter_index: Option<i64>,
    pub kind: Option<String>,
    pub caption: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generate_request_fills_profile_defaults() {
        let req: GenerateAudiobookRequest =
            serde_json::from_str(r#"{"audiobook_name":"B"}"#).unwrap();
        assert_eq!(req.episode_profile, "book_navigator");
        assert_eq!(req.speaker_profile, "book_navigator_mentor");
        assert!(req.source_id.is_none());
        assert!(req.content.is_none());
        assert!(req.briefing_suffix.is_none());
        assert!(req.max_chapters.is_none());
    }

    #[test]
    fn audiobook_detail_flattens_parent_fields() {
        let detail = AudiobookDetail {
            audiobook: Audiobook {
                id: Some("audiobook:a".into()),
                name: Some("N".into()),
                source_id: None,
                briefing: Some("b".into()),
                chapter_count: Some(1),
                created: None,
            },
            chapters: vec![ChapterEpisode {
                id: Some("episode:e".into()),
                name: Some("第1章".into()),
                chapter_index: Some(0),
                chapter_title: Some("序".into()),
                audio_file: None,
                generation_error: None,
            }],
        };
        let v = serde_json::to_value(&detail).unwrap();
        // #[serde(flatten)]: audiobook fields sit at the top level, no nesting.
        assert_eq!(v["id"], "audiobook:a");
        assert_eq!(v["name"], "N");
        assert!(v.get("audiobook").is_none());
        assert_eq!(v["chapters"][0]["chapter_title"], "序");
        assert!(v["chapters"][0]["audio_file"].is_null());
    }

    #[test]
    fn audiobook_created_roundtrips_from_projected_string() {
        // repo.rs projects type::string(created); the field must deserialize.
        let json = r#"{"id":"audiobook:a","name":"N","source_id":null,"briefing":null,"chapter_count":2,"created":"2026-07-23T12:00:00Z"}"#;
        let audiobook: Audiobook = serde_json::from_str(json).unwrap();
        assert_eq!(audiobook.created.as_deref(), Some("2026-07-23T12:00:00Z"));
        let back = serde_json::to_value(&audiobook).unwrap();
        assert_eq!(back["created"], "2026-07-23T12:00:00Z");
    }

    #[test]
    fn book_figure_roundtrips_with_nulls() {
        let json = r#"{"id":"book_figure:f","page":3,"chapter_index":null,"kind":"figure","caption":null}"#;
        let fig: BookFigure = serde_json::from_str(json).unwrap();
        assert_eq!(fig.page, Some(3));
        assert!(fig.chapter_index.is_none());
        let back = serde_json::to_value(&fig).unwrap();
        assert_eq!(back["kind"], "figure");
        assert!(back["caption"].is_null());
    }
}
