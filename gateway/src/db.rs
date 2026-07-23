//! SurrealDB connection (Rust SDK, direct — reinhardt's ORM has no SurrealDB support).

use std::sync::OnceLock;
use surrealdb::engine::any::{connect, Any};
use surrealdb::opt::auth::Root;
use surrealdb::Surreal;

use crate::config::Config;

static DB: OnceLock<Surreal<Any>> = OnceLock::new();

/// Connect using the configured credentials and store the handle globally.
pub async fn init(cfg: &Config) -> Result<(), Box<dyn std::error::Error>> {
    let db = connect(cfg.surreal_url.clone()).await?;
    db.signin(Root {
        username: &cfg.surreal_user,
        password: &cfg.surreal_pass,
    })
    .await?;
    db.use_ns(cfg.surreal_ns.clone())
        .use_db(cfg.surreal_db.clone())
        .await?;
    let _ = DB.set(db);
    Ok(())
}

/// Get the global connection. Returns None if `init` has not succeeded.
pub fn get() -> Option<&'static Surreal<Any>> {
    DB.get()
}

/// Connect to an in-memory SurrealDB instance (for tests). Each call is an
/// isolated database. Requires the `kv-mem` feature.
pub async fn connect_mem() -> Surreal<Any> {
    let db = connect("mem://").await.expect("connect in-memory surreal");
    db.use_ns("test")
        .use_db("test")
        .await
        .expect("select test ns/db");
    db
}

/// Install a connection into the global slot (for handler tests). No-op if a
/// connection was already set (the global can only be set once per process).
#[doc(hidden)]
pub fn set_for_test(db: Surreal<Any>) {
    let _ = DB.set(db);
}
