"""Link the Book Navigator profiles to AI models so real audio can be generated.

Creates (if missing) a language Model and a text_to_speech Model, then points
`episode_profile:book_navigator` (outline_llm + transcript_llm) and
`speaker_profile:book_navigator_mentor` (voice_model) at them.

Models are created WITHOUT a linked credential, so resolution falls back to
environment variables (e.g. OPENAI_API_KEY) — see open_notebook/ai/key_provider.py.

Usage (cloud):
    uv run python scripts/setup_book_navigator_models.py \
        --provider openai --language-model gpt-4o-mini --tts-model gpt-4o-mini-tts

Usage (fully local LLM via Ollama; defer/skip TTS):
    uv run python scripts/setup_book_navigator_models.py \
        --provider ollama --language-model gpt-oss:20b --tts-model ""

Pass an empty --tts-model to skip linking the voice model (TTS added later).
Env (SurrealDB connection) is read the same way as the rest of the app.
"""

import argparse
import asyncio

from open_notebook.database.repository import repo_query


async def _upsert_model(name: str, provider: str, mtype: str) -> str:
    existing = await repo_query(
        "SELECT type::string(id) AS id FROM model WHERE name = $n AND type = $t",
        {"n": name, "t": mtype},
    )
    if existing:
        return existing[0]["id"]
    created = await repo_query(
        "CREATE model SET name = $n, provider = $p, type = $t "
        "RETURN type::string(id) AS id",
        {"n": name, "p": provider, "t": mtype},
    )
    return created[0]["id"]


async def main(
    provider: str, language_model: str, tts_model: str, tts_provider: str
) -> None:
    lang_id = await _upsert_model(language_model, provider, "language")
    print(f"language model:  {lang_id} ({provider}/{language_model})")

    await repo_query(
        "UPDATE episode_profile SET outline_llm = type::thing('model', $lid), "
        "transcript_llm = type::thing('model', $lid) WHERE name = 'book_navigator'",
        {"lid": lang_id.split(":", 1)[1]},
    )
    print("linked book_navigator -> outline_llm/transcript_llm")

    # TTS is optional: Ollama has no TTS, so a local OpenAI-compatible TTS server
    # (or a cloud provider) is linked separately. Empty --tts-model skips it.
    tts_id = None
    if tts_model:
        tts_id = await _upsert_model(tts_model, tts_provider, "text_to_speech")
        await repo_query(
            "UPDATE speaker_profile SET voice_model = type::thing('model', $tid) "
            "WHERE name = 'book_navigator_mentor'",
            {"tid": tts_id.split(":", 1)[1]},
        )
        print(f"tts model:       {tts_id} ({tts_provider}/{tts_model})")
        print("linked book_navigator_mentor -> voice_model")
    else:
        print("TTS skipped (no --tts-model); voice_model left unset.")


async def set_defaults(
    provider: str,
    language_model: str,
    embedding_provider: str,
    embedding_model: str,
    tts_model_id: "str | None",
) -> None:
    """Set Open Notebook's DefaultModels so chat/ask/embedding/transformation
    work natively (the podcast profiles above only cover audiobook generation).
    """
    lang_id = await _upsert_model(language_model, provider, "language")
    embed_id = await _upsert_model(embedding_model, embedding_provider, "embedding")
    parts = [
        "default_chat_model = type::thing('model', $lid)",
        "default_transformation_model = type::thing('model', $lid)",
        "default_tools_model = type::thing('model', $lid)",
        "large_context_model = type::thing('model', $lid)",
        "default_embedding_model = type::thing('model', $eid)",
    ]
    binds = {
        "lid": lang_id.split(":", 1)[1],
        "eid": embed_id.split(":", 1)[1],
    }
    if tts_model_id:
        parts.append("default_text_to_speech_model = type::thing('model', $tid)")
        binds["tid"] = tts_model_id.split(":", 1)[1]
    await repo_query(
        "UPSERT open_notebook:default_models SET " + ", ".join(parts),
        binds,
    )
    print(
        f"defaults set: chat/transformation/tools/large_context={language_model}, "
        f"embedding={embedding_provider}/{embedding_model}"
        + (", tts linked" if tts_model_id else "")
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="openai", help="LLM provider (e.g. openai, anthropic, ollama)")
    ap.add_argument("--language-model", default="gpt-4o-mini")
    ap.add_argument("--tts-model", default="gpt-4o-mini-tts", help="empty string to skip TTS")
    ap.add_argument(
        "--tts-provider",
        default=None,
        help="TTS provider (defaults to --provider; use 'openai-compatible' for local TTS)",
    )
    ap.add_argument(
        "--set-defaults",
        action="store_true",
        help="Also set Open Notebook DefaultModels (chat/transformation/tools/embedding)",
    )
    ap.add_argument("--embedding-provider", default="google")
    ap.add_argument("--embedding-model", default="gemini-embedding-001")
    args = ap.parse_args()

    async def run_all() -> None:
        await main(
            args.provider,
            args.language_model,
            args.tts_model,
            args.tts_provider or args.provider,
        )
        if args.set_defaults:
            tts_id = None
            if args.tts_model:
                tts_id = await _upsert_model(
                    args.tts_model, args.tts_provider or args.provider, "text_to_speech"
                )
            await set_defaults(
                args.provider,
                args.language_model,
                args.embedding_provider,
                args.embedding_model,
                tts_id,
            )

    asyncio.run(run_all())
