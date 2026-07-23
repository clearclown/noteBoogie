//! noteboogie-gateway binary — thin entrypoint over the `gateway` library.
//!
//! Main backend for the Open Notebook personal fork. Talks to SurrealDB directly
//! (the reinhardt ORM has no SurrealDB support) and delegates LLM/TTS work to the
//! Python sidecar (podcast-creator) over gRPC.

use gateway::config::Config;
use gateway::{db, handlers, sidecar};
use reinhardt::server::HttpServer;
use reinhardt::ServerRouter;
use std::net::SocketAddr;

#[tokio::main]
async fn main() {
    let cfg = Config::from_env();

    match db::init(&cfg).await {
        Ok(()) => println!("connected to SurrealDB at {}", cfg.surreal_url),
        // Don't crash if the DB is down; data endpoints will 500 until it's up.
        Err(e) => eprintln!("WARNING: SurrealDB connection failed: {e}"),
    }

    // Optional liveness check against the sidecar (non-fatal).
    match sidecar::ping(&cfg.sidecar_addr).await {
        Ok(ok) => println!("sidecar reachable at {} (ok={ok})", cfg.sidecar_addr),
        Err(e) => eprintln!(
            "note: sidecar not reachable at {} ({e}); generation will be deferred",
            cfg.sidecar_addr
        ),
    }

    let mut router = ServerRouter::new()
        .endpoint(handlers::health)
        .endpoint(handlers::list_audiobooks)
        .endpoint(handlers::get_audiobook)
        .endpoint(handlers::get_audiobook_figures)
        .endpoint(handlers::get_figure_image)
        .endpoint(handlers::delete_audiobook)
        .endpoint(handlers::generate_audiobook);
    let errors = router.register_all_routes();
    if !errors.is_empty() {
        eprintln!("route registration warnings: {errors:?}");
    }

    let addr: SocketAddr = cfg.bind_addr.parse().expect("valid bind address");
    println!("noteboogie-gateway listening on http://{addr}");
    HttpServer::new(router)
        .listen(addr)
        .await
        .expect("server failed");
}
