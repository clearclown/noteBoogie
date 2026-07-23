//! HTTP handlers for `/audiobooks` and the generation orchestration.

use reinhardt::{delete, get, post, Json, Path, Response, ViewResult};
use serde_json::json;

use crate::chapters::split_into_chapters;
use crate::config::Config;
use crate::models::{AudiobookDetail, GenerateAudiobookRequest, GenerateAudiobookResponse};
use crate::sidecar::{self, CreatePodcastRequest};
use crate::{db, repo};

/// Build a 500 JSON response from any displayable error.
fn server_error(msg: impl std::fmt::Display) -> Response {
    eprintln!("gateway error: {msg}");
    Response::internal_server_error()
        .with_json(&json!({ "error": msg.to_string() }))
        .unwrap_or_else(|_| Response::internal_server_error())
}

fn require_db() -> Result<&'static surrealdb::Surreal<surrealdb::engine::any::Any>, Response> {
    db::get().ok_or_else(|| server_error("database not initialized"))
}

#[get("/health")]
pub async fn health() -> ViewResult<Response> {
    Response::ok()
        .with_json(&json!({"status": "ok", "service": "noteboogie-gateway"}))
        .map_err(Into::into)
}

#[get("/audiobooks")]
pub async fn list_audiobooks() -> ViewResult<Response> {
    let db = match require_db() {
        Ok(db) => db,
        Err(r) => return Ok(r),
    };
    match repo::list_audiobooks(db).await {
        Ok(rows) => Response::ok().with_json(&rows).map_err(Into::into),
        Err(e) => Ok(server_error(e)),
    }
}

#[get("/audiobooks/{id}")]
pub async fn get_audiobook(Path(id): Path<String>) -> ViewResult<Response> {
    let db = match require_db() {
        Ok(db) => db,
        Err(r) => return Ok(r),
    };
    let audiobook = match repo::get_audiobook(db, &id).await {
        Ok(Some(a)) => a,
        Ok(None) => {
            return Response::not_found()
                .with_json(&json!({"error": "audiobook not found"}))
                .map_err(Into::into)
        }
        Err(e) => return Ok(server_error(e)),
    };
    let chapters = match repo::get_chapters(db, &id).await {
        Ok(c) => c,
        Err(e) => return Ok(server_error(e)),
    };
    let detail = AudiobookDetail { audiobook, chapters };
    Response::ok().with_json(&detail).map_err(Into::into)
}

#[delete("/audiobooks/{id}")]
pub async fn delete_audiobook(Path(id): Path<String>) -> ViewResult<Response> {
    let db = match require_db() {
        Ok(db) => db,
        Err(r) => return Ok(r),
    };
    match repo::delete_audiobook(db, &id).await {
        Ok(audio_files) => {
            // Best-effort on-disk cleanup of generated mp3s. Stored paths are
            // relative to PODCASTS_FOLDER since #1030; tolerate legacy
            // absolute values from pre-migration rows.
            let podcasts_root = format!(
                "{}/podcasts",
                Config::from_env().data_folder.trim_end_matches('/')
            );
            for f in audio_files {
                let path = f.strip_prefix("file://").unwrap_or(&f);
                let resolved = if path.starts_with('/') {
                    path.to_string()
                } else {
                    format!("{podcasts_root}/{path}")
                };
                let _ = std::fs::remove_file(resolved);
            }
            Response::ok()
                .with_json(&json!({"message": "deleted", "id": id}))
                .map_err(Into::into)
        }
        Err(e) => Ok(server_error(e)),
    }
}

/// Figures of the book behind an audiobook, ordered by page — powers the
/// frontend figure gallery (grouped client-side by chapter_index).
#[get("/audiobooks/{id}/figures")]
pub async fn get_audiobook_figures(Path(id): Path<String>) -> ViewResult<Response> {
    let db = match require_db() {
        Ok(db) => db,
        Err(r) => return Ok(r),
    };
    let audiobook = match repo::get_audiobook(db, &id).await {
        Ok(Some(a)) => a,
        Ok(None) => {
            return Response::not_found()
                .with_json(&json!({"error": "audiobook not found"}))
                .map_err(Into::into)
        }
        Err(e) => return Ok(server_error(e)),
    };
    let Some(source_id) = audiobook.source_id else {
        return Response::ok().with_json(&Vec::<crate::models::BookFigure>::new()).map_err(Into::into);
    };
    match repo::get_figures_for_source(db, &source_id).await {
        Ok(figures) => Response::ok().with_json(&figures).map_err(Into::into),
        Err(e) => Ok(server_error(e)),
    }
}

/// Serve one figure's image bytes. The path comes from the DB record (written
/// by the ingest pipeline), never from the request — no traversal surface.
#[get("/figures/{id}/image")]
pub async fn get_figure_image(Path(id): Path<String>) -> ViewResult<Response> {
    let db = match require_db() {
        Ok(db) => db,
        Err(r) => return Ok(r),
    };
    let path = match repo::get_figure_path(db, &id).await {
        Ok(Some(p)) => p,
        Ok(None) => {
            return Response::not_found()
                .with_json(&json!({"error": "figure not found"}))
                .map_err(Into::into)
        }
        Err(e) => return Ok(server_error(e)),
    };
    match std::fs::read(&path) {
        Ok(bytes) => {
            let content_type = if path.ends_with(".jpg") || path.ends_with(".jpeg") {
                "image/jpeg"
            } else {
                "image/png"
            };
            Response::ok()
                .with_body(bytes)
                .try_with_header("content-type", content_type)
                .map_err(Into::into)
        }
        Err(e) => {
            eprintln!("figure image read failed {path}: {e}");
            Response::not_found()
                .with_json(&json!({"error": "image file missing"}))
                .map_err(Into::into)
        }
    }
}

#[post("/audiobooks/generate")]
pub async fn generate_audiobook(
    Json(req): Json<GenerateAudiobookRequest>,
) -> ViewResult<Response> {
    let db = match require_db() {
        Ok(db) => db,
        Err(r) => return Ok(r),
    };
    let cfg = Config::from_env();

    // 1. Resolve content + fallback chapter title.
    let (content, fallback_title) = match (&req.source_id, &req.content) {
        (Some(sid), _) => match repo::get_source_lite(db, sid).await {
            Ok(Some(s)) => (
                s.full_text.unwrap_or_default(),
                s.title.unwrap_or_else(|| req.audiobook_name.clone()),
            ),
            Ok(None) => {
                return Response::not_found()
                    .with_json(&json!({"error": "source not found"}))
                    .map_err(Into::into)
            }
            Err(e) => return Ok(server_error(e)),
        },
        (None, Some(c)) => (c.clone(), req.audiobook_name.clone()),
        (None, None) => {
            return Response::bad_request()
                .with_json(&json!({"error": "source_id or content is required"}))
                .map_err(Into::into)
        }
    };

    // 2. Validate + snapshot profiles.
    let ep_profile = match repo::get_episode_profile_lite(db, &req.episode_profile).await {
        Ok(Some(p)) => p,
        Ok(None) => {
            return Response::bad_request()
                .with_json(&json!({"error": format!("episode profile '{}' not found", req.episode_profile)}))
                .map_err(Into::into)
        }
        Err(e) => return Ok(server_error(e)),
    };
    match repo::speaker_profile_exists(db, &req.speaker_profile).await {
        Ok(true) => {}
        Ok(false) => {
            return Response::bad_request()
                .with_json(&json!({"error": format!("speaker profile '{}' not found", req.speaker_profile)}))
                .map_err(Into::into)
        }
        Err(e) => return Ok(server_error(e)),
    }

    let base_briefing = {
        let mut b = ep_profile.default_briefing.clone().unwrap_or_default();
        if let Some(suffix) = &req.briefing_suffix {
            b.push_str("\n\n");
            b.push_str(suffix);
        }
        b
    };
    let ep_snapshot = serde_json::to_value(&ep_profile).unwrap_or(json!({"name": req.episode_profile}));
    let sp_snapshot = json!({ "name": req.speaker_profile });

    // 3. Split into chapters (capped).
    let mut chapters = split_into_chapters(&content, &fallback_title);
    if let Some(max) = req.max_chapters {
        chapters.truncate(max.max(1));
    }
    let chapter_count = chapters.len();

    // 4. Create parent audiobook.
    let ab_id_part = uuid::Uuid::new_v4().simple().to_string();
    let audiobook_full_id = match repo::create_audiobook(
        db,
        &ab_id_part,
        &req.audiobook_name,
        req.source_id.as_deref(),
        &base_briefing,
        chapter_count as i64,
    )
    .await
    {
        Ok(id) => id,
        Err(e) => return Ok(server_error(e)),
    };

    // 5. Create chapter episodes (pending) and collect generation jobs.
    struct Job {
        episode_full_id: String,
        content: String,
        briefing: String,
        output_dir: String,
    }
    let mut jobs: Vec<Job> = Vec::with_capacity(chapter_count);
    for (idx, ch) in chapters.iter().enumerate() {
        let ep_id_part = uuid::Uuid::new_v4().simple().to_string();
        let ep_name = format!("第{}章：{}", idx + 1, ch.title);
        let chapter_briefing = format!("この章のタイトル：{}\n\n{}", ch.title, base_briefing);
        let episode_full_id = match repo::create_chapter_episode(
            db,
            &ep_id_part,
            &ab_id_part,
            &ep_name,
            &ep_snapshot,
            &sp_snapshot,
            &chapter_briefing,
            &ch.body,
            idx as i64,
            &ch.title,
        )
        .await
        {
            Ok(id) => id,
            Err(e) => return Ok(server_error(e)),
        };
        let output_dir = format!(
            "{}/podcasts/episodes/{}",
            cfg.data_folder.trim_end_matches('/'),
            ep_id_part
        );
        jobs.push(Job {
            episode_full_id,
            content: ch.body.clone(),
            briefing: chapter_briefing,
            output_dir,
        });
    }

    // 6. Spawn best-effort background generation (calls the Python sidecar per chapter).
    let sidecar_addr = cfg.sidecar_addr.clone();
    let speaker_config = req.speaker_profile.clone();
    let episode_profile = req.episode_profile.clone();
    tokio::spawn(async move {
        let Some(db) = db::get() else { return };
        for job in jobs {
            let request = CreatePodcastRequest {
                content: job.content,
                briefing: job.briefing,
                episode_name: job
                    .episode_full_id
                    .strip_prefix("episode:")
                    .unwrap_or(&job.episode_full_id)
                    .to_string(),
                output_dir: job.output_dir,
                speaker_config: speaker_config.clone(),
                episode_profile: episode_profile.clone(),
            };
            match sidecar::create_podcast(&sidecar_addr, request).await {
                Ok(resp) => {
                    if let Err(e) = repo::set_episode_result(
                        db,
                        &job.episode_full_id,
                        &resp.final_output_file_path,
                        &resp.transcript_json,
                        &resp.outline_json,
                    )
                    .await
                    {
                        eprintln!("failed to save chapter result {}: {e}", job.episode_full_id);
                    }
                }
                Err(e) => {
                    eprintln!("chapter generation failed {}: {e}", job.episode_full_id);
                    if let Err(persist_err) =
                        repo::set_episode_error(db, &job.episode_full_id, &e.to_string()).await
                    {
                        eprintln!(
                            "failed to record chapter error {}: {persist_err}",
                            job.episode_full_id
                        );
                    }
                }
            }
        }
    });

    let body = GenerateAudiobookResponse {
        audiobook_id: audiobook_full_id,
        audiobook_name: req.audiobook_name,
        chapter_count,
        status: "processing".to_string(),
    };
    Response::created().with_json(&body).map_err(Into::into)
}
