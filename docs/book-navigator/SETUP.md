# セットアップ

## 1. 前提ツール

| ツール | 用途 | 備考 |
|---|---|---|
| macOS + Apple Silicon | OCR の MPS 加速 | Intel/Linux でも CPU で動作（遅い） |
| Rust 1.96（rustup） | gateway / PDF変換 | Homebrew の rustc 1.95 では不可。`gateway/rust-toolchain.toml` が自動選択 |
| Python 3.11+ / uv | API・sidecar・スクリプト | `uv sync --group sidecar` |
| Node 22 / npm | フロント | `cd frontend && npm ci` |
| Docker または Podman | SurrealDB | `examples/docker-compose-dev.yml` を使用 |
| protoc | gateway のビルド（gRPC codegen） | `brew install protobuf` |
| poppler または ImageMagick | PDF ラスタライズ | `brew install poppler` |

## 2. 姉妹リポ（PDF変換）

```bash
cd ..   # noteBoogie の親ディレクトリ
git clone https://github.com/clearclown/Rust_DN_SuperBook_PDF_Converter
cd Rust_DN_SuperBook_PDF_Converter/superbook-pdf

# YomiToku 用 venv は ai_bridge/ai_venv に置く（この場所は規約。他の場所では検出されない）
uv venv ai_bridge/ai_venv
ai_bridge/ai_venv/bin/pip install yomitoku torch

# 動作確認（MPS が True なら加速有効）
ai_bridge/ai_venv/bin/python -c "import torch; print(torch.backends.mps.is_available())"
```

`Makefile` の `SUPERBOOK` 変数が `../Rust_DN_SuperBook_PDF_Converter/superbook-pdf` を指しています。別の場所に置く場合は `make convert-book SUPERBOOK=/path/to/superbook-pdf ...` で上書きしてください。

## 3. 環境変数（.env）

`.env.example` をコピーして作成します。Book Navigator が使うもの:

| 変数 | 用途 |
|---|---|
| `ANTHROPIC_API_KEY` | 台本生成・図の vision キャプション（claude-sonnet-5） |
| `GOOGLE_API_KEY` | TTS（gemini-3.1-flash-tts-preview）と埋め込み（gemini-embedding-001） |
| `DEEPSEEK_API_KEY` | （任意）eval_transcript の低コスト比較。**キー名は DEEPSEEK_API_KEY**（DEEP_SEEK_… ではない） |
| `SURREAL_URL` ほか | DB 接続（既定で compose の SurrealDB に一致） |
| `GATEWAY_BIND_ADDR` | gateway の bind（既定 127.0.0.1:8088） |
| `SIDECAR_GRPC_ADDR` | gateway→sidecar（既定 http://127.0.0.1:50069） |
| `NEXT_PUBLIC_GATEWAY_URL` | フロント→gateway（既定 http://localhost:8088、frontend 側の env） |

`DATA_FOLDER` は Open Notebook 本体が `./data` に固定しているため、**gateway もリポジトリルートから起動する**必要があります（`make book-stack` はそうなっています）。

## 4. サービス起動

```bash
make book-stack   # SurrealDB + API(:5055) + worker + sidecar(:50069) + gateway(:8088)
make run          # フロント(:3000)（別ターミナル）
```

- API 起動時にマイグレーション（24/25/26 含む）が自動適用されます
- **worker（surreal-commands）が埋め込みジョブを処理**します。worker 無しだと取り込み後の埋め込みが進みません
- 停止は `make stop-all`

## 5. モデル登録（初回のみ）

```bash
uv run --env-file .env python scripts/setup_book_navigator_models.py \
  --provider anthropic --language-model claude-sonnet-5 \
  --tts-provider google --tts-model gemini-3.1-flash-tts-preview \
  --set-defaults
```

- `book_navigator`（episode profile）と `book_navigator_mentor`（speaker profile）にモデルをリンク
- `--set-defaults` で chat / transformation / tools / large_context / embedding / TTS の DefaultModels も一括設定（chat・ask・埋め込みに必須）
- モデルはクレデンシャル未リンクで登録され、実行時に環境変数のキーへフォールバックします
