//! gRPC client for the Python sidecar (podcast-creator: LLM + TTS).

pub mod pb {
    tonic::include_proto!("noteboogie.podcast.v1");
}

use pb::podcast_sidecar_client::PodcastSidecarClient;
pub use pb::{CreatePodcastRequest, CreatePodcastResponse};

/// Call the sidecar to generate one episode's audio.
pub async fn create_podcast(
    addr: &str,
    req: CreatePodcastRequest,
) -> Result<CreatePodcastResponse, Box<dyn std::error::Error + Send + Sync>> {
    let mut client = PodcastSidecarClient::connect(addr.to_string()).await?;
    let resp = client.create_podcast(req).await?;
    Ok(resp.into_inner())
}

/// Liveness check against the sidecar.
pub async fn ping(addr: &str) -> Result<bool, Box<dyn std::error::Error + Send + Sync>> {
    let mut client = PodcastSidecarClient::connect(addr.to_string()).await?;
    let resp = client.ping(pb::PingRequest {}).await?;
    Ok(resp.into_inner().ok)
}
