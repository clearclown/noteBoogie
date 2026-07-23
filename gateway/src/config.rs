//! Runtime configuration from environment variables (shared with the Python backend).

use std::env;

#[derive(Debug, Clone)]
pub struct Config {
    pub surreal_url: String,
    pub surreal_user: String,
    pub surreal_pass: String,
    pub surreal_ns: String,
    pub surreal_db: String,
    /// gRPC endpoint of the Python sidecar, e.g. http://127.0.0.1:50069
    pub sidecar_addr: String,
    /// Root data folder for generated audio (mirrors Python DATA_FOLDER).
    pub data_folder: String,
    /// Bind address for the gateway HTTP server.
    pub bind_addr: String,
}

impl Config {
    pub fn from_env() -> Self {
        let surreal_url = env::var("SURREAL_URL")
            .unwrap_or_else(|_| "ws://localhost:8000".into())
            .trim_end_matches("/rpc")
            .to_string();
        Config {
            surreal_url,
            surreal_user: env::var("SURREAL_USER").unwrap_or_else(|_| "root".into()),
            surreal_pass: env::var("SURREAL_PASSWORD").unwrap_or_else(|_| "root".into()),
            surreal_ns: env::var("SURREAL_NAMESPACE").unwrap_or_else(|_| "open_notebook".into()),
            surreal_db: env::var("SURREAL_DATABASE").unwrap_or_else(|_| "open_notebook".into()),
            sidecar_addr: env::var("SIDECAR_GRPC_ADDR")
                .unwrap_or_else(|_| "http://127.0.0.1:50069".into()),
            data_folder: env::var("DATA_FOLDER").unwrap_or_else(|_| "./data".into()),
            bind_addr: env::var("GATEWAY_BIND_ADDR").unwrap_or_else(|_| "127.0.0.1:8088".into()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const VARS: &[&str] = &[
        "SURREAL_URL",
        "SURREAL_USER",
        "SURREAL_PASSWORD",
        "SURREAL_NAMESPACE",
        "SURREAL_DATABASE",
        "SIDECAR_GRPC_ADDR",
        "DATA_FOLDER",
        "GATEWAY_BIND_ADDR",
    ];

    // Env vars are process-global, so defaults + overrides live in ONE test to
    // avoid races with parallel test threads mutating the same variables.
    #[test]
    fn from_env_defaults_and_overrides() {
        unsafe {
            for v in VARS {
                env::remove_var(v);
            }
        }
        let cfg = Config::from_env();
        assert_eq!(cfg.surreal_url, "ws://localhost:8000");
        assert_eq!(cfg.surreal_user, "root");
        assert_eq!(cfg.surreal_ns, "open_notebook");
        assert_eq!(cfg.sidecar_addr, "http://127.0.0.1:50069");
        assert_eq!(cfg.data_folder, "./data");
        assert_eq!(cfg.bind_addr, "127.0.0.1:8088");

        unsafe {
            // The Python stack's URL form includes /rpc; the Rust SDK must not.
            env::set_var("SURREAL_URL", "ws://db:9000/rpc");
            env::set_var("DATA_FOLDER", "/srv/data/");
            env::set_var("GATEWAY_BIND_ADDR", "0.0.0.0:9999");
        }
        let cfg = Config::from_env();
        assert_eq!(cfg.surreal_url, "ws://db:9000", "trailing /rpc stripped");
        assert_eq!(cfg.data_folder, "/srv/data/");
        assert_eq!(cfg.bind_addr, "0.0.0.0:9999");

        unsafe {
            for v in VARS {
                env::remove_var(v);
            }
        }
    }
}
