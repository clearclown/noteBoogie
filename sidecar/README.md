# Python Podcast Sidecar (gRPC)

Exposes Python-only computation that has no Rust equivalent to the Rust gateway:
podcast-creator (outline/transcript LLM + TTS). Wraps the existing logic in
`commands/podcast_commands.py` without surreal-commands or DB persistence (the
gateway owns persistence).

## Contract

`protos/podcast.proto` → `noteboogie.podcast.v1.PodcastSidecar`:
- `Ping` — liveness.
- `CreatePodcast` — one content chunk → one outline → N segments → one mp3.

Single source of truth is the `.proto`; both sides generate from it
(Rust: tonic-build; Python: `grpc_tools.protoc`).

## Run

```bash
make sidecar          # uv run --group sidecar python -m sidecar.podcast_sidecar
# or with a custom bind address:
SIDECAR_ADDRESS="[::]:50069" make sidecar
```

## Regenerate stubs after editing the proto

```bash
make sidecar-proto    # writes sidecar/gen/podcast_pb2*.py
```

Requires the `sidecar` dependency group: `uv sync --group sidecar`.

## Credentials for real audio generation

`create_podcast` makes real LLM (outline/transcript) and TTS calls, so the sidecar
process needs a valid provider key. Two options:

1. **Env / `.env`** — put the key where the sidecar can see it. `make sidecar` loads
   `.env` (`--env-file .env`):
   ```
   # .env
   OPENAI_API_KEY=sk-...
   ```
2. **DB credential** — create a `credential` record for the provider; models link to it.

Then link the Book Navigator profiles to models (falls back to the env key when a
model has no linked credential):

```bash
uv run python scripts/setup_book_navigator_models.py \
    --provider openai --language-model gpt-4o-mini --tts-model gpt-4o-mini-tts
```

End-to-end run: start SurrealDB → apply migrations → run the link script →
`make sidecar` → run the gateway → `POST /audiobooks/generate`. The gateway needs no
key itself; only the sidecar does.

