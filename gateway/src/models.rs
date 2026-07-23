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
}

/// One chapter episode as exposed to clients.
#[derive(Debug, Serialize, Deserialize)]
pub struct ChapterEpisode {
    pub id: Option<String>,
    pub name: Option<String>,
    pub chapter_index: Option<i64>,
    pub chapter_title: Option<String>,
    pub audio_file: Option<String>,
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

/// A separated book figure (migration 17) as exposed to the frontend gallery.
#[derive(Debug, Serialize, Deserialize)]
pub struct BookFigure {
    pub id: Option<String>,
    pub page: Option<i64>,
    pub chapter_index: Option<i64>,
    pub kind: Option<String>,
    pub caption: Option<String>,
}
