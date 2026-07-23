# noteboogie-gateway (Rust / reinhardt-web)

Rust API gateway — the **main backend** for the Open Notebook personal fork (see
`.kiro/steering/tech.md`). Talks to SurrealDB directly via the Rust SDK and (in a
later increment) delegates LLM/TTS work to the Python sidecar over gRPC.

## Status: foundation increment

- `GET /health` — liveness.
- `GET /audiobooks` — lists audiobooks from SurrealDB (proves Rust ↔ SurrealDB).
- Sidecar gRPC wiring and the full `/audiobooks/{generate,get,delete}` come next
  (see `.kiro/specs/book-navigator/tasks.md`).

## Toolchain

reinhardt-web 0.3.0-rc.5 requires **rustc ≥ 1.96.0**. The repo's Homebrew rustc is
1.95.0, so `rust-toolchain.toml` pins the rustup-managed `1.96.0` toolchain. Build
through the rustup cargo shim:

```bash
export PATH="$HOME/.cargo/bin:$PATH"   # use rustup cargo, not Homebrew's 1.95.0
cargo build
```

## Run

Uses the same env vars as the Python backend (defaults shown):

```bash
SURREAL_URL=ws://localhost:8000/rpc \
SURREAL_USER=root SURREAL_PASSWORD=root \
SURREAL_NAMESPACE=open_notebook SURREAL_DATABASE=open_notebook \
  cargo run

curl http://127.0.0.1:8088/health
curl http://127.0.0.1:8088/audiobooks
```

If SurrealDB is down the gateway still starts; `/audiobooks` returns 500 until it's up.

## Tests

```bash
export PATH="$HOME/.cargo/bin:$PATH"
cargo test
```

- `src/chapters.rs` — unit tests for Markdown chapter splitting (ATX H1/H2, CRLF,
  inline markup, Japanese headings, fallback, deep-heading non-split).
- `tests/repo_it.rs` — repository tests against an **in-memory SurrealDB**
  (`kv-mem`), hermetic, no Docker: create/list/get, chapter ordering, result
  recording, cascade delete, profile/source lookups.
- `tests/handlers_it.rs` — drives the handlers through reinhardt-web's own
  `ServerRouter::handle` (routing, `Path`/`Json` extractors, `Request`/`Response`):
  health, unmatched-route error, 400 validation, and a generate → get → list →
  delete roundtrip backed by an in-memory DB.
