# noteBoogie — Book Navigator

**紙の書籍（スキャンPDF・縦書き対応）を、章ごとの「メンター独話オーディオブック」と質問できるナレッジベースに変換する個人プロジェクト。** ビジネス書に限らず、技術書・専門書・実用書など任意の蔵書で機能します（師匠のペルソナも自由に設定可能）。

Google NotebookLM の「音声概要 + ソースに聞く」体験を、自分の蔵書・自分のモデル選択・自分のマシンで超えることを目標にした [Open Notebook](https://github.com/lfnovo/open-notebook) の個人フォークです。

> フォーク元（Open Notebook 本体）の機能・設定・デプロイは [docs/](docs/index.md)（英語）と [docs/UPSTREAM_README.md](docs/UPSTREAM_README.md) を参照してください。この README は本フォークの追加機能（Book Navigator）を扱います。

---

## なにができるか

1. **スキャン和書PDF → Markdown**: 姉妹リポ [Rust_DN_SuperBook_PDF_Converter](https://github.com/clearclown/Rust_DN_SuperBook_PDF_Converter) が YomiToku OCR（Apple Silicon MPS 加速）で変換。**縦書きの右→左段組**・**章見出し（第N章）**・**表（Markdown表として復元）**・**図の切り出し**・章/図マニフェスト出力に対応
2. **取り込み**: 本文を Open Notebook の Source として登録し、図は Claude vision で**日本語キャプション化**（音声用に本文へ【図: …】マーカーを埋め込み）、全文をチャンク埋め込み
3. **質問**: 既存の chat / ask（LangGraph）で「この本に質問」— 本文グラウンディング・引用付き回答
4. **オーディオブック生成**: Rust gateway が章分割（柱・目次ノイズのガードレール付き）→ 章ごとに 台本LLM → TTS → mp3。台本プロンプトは**強化学習ハーネスで自動チューニング済み**
5. **視聴**: フロントの「オーディオブック」タブでトラックリスト再生（連続再生・生成中/失敗の可視化）+ 章ごとの**図ギャラリー**
6. **外部連携**: MCP サーバーで蔵書を Claude Desktop / Claude Code から検索・質問可能
7. **モデルは差し替え自由**: GUI（テンプレート/モデル設定）と CLI（`make set-book-models`）で台本・TTS・チャットのモデルを選択。TTS は OpenAI 互換サーバ（kokoro 等）で**ローカル実行**も可能
8. **メンターAI（師匠）**: 蔵書全体 + 長期記憶（過去の相談）を持つ師匠と壁打ち。**ペルソナは自由設定**（既定シードはコンサルだが、外科医・編集者など蔵書に合わせて GUI から変更可）。本/章単位の**学習傾斜**（0〜2 + 自動傾斜）と回答の**TTS音声化**、スライドの5軸レビュー+pptx自動修正に対応。専用UI（/mentor）+ REST + MCP
9. **持ち出し**: `make export-audiobook` で章mp3を1フォルダ（`data/audiobooks/<タイトル>/NN_章名.mp3` + m3u8 プレイリスト）に集約。**Docker/Podman 一発起動**（`docker-compose.book.yml`）で Tailscale 経由のリモート視聴も可能

## アーキテクチャ

```
スキャンPDF（縦書き和書）
  └─ superbook-pdf markdown（Rust + YomiToku/MPS）─→ 本.md + images/ + book_manifest.json
       └─ scripts/ingest_book.py ─→ Notebook + Source(full_text) + book_figure(+visionキャプション) + 埋め込み
            ├─ chat / ask（LangGraph・FastAPI :5055）……「この本に質問」
            ├─ scripts/book_mcp_server.py（MCP stdio）……外部クライアントから検索/質問
            └─ Rust gateway（reinhardt-web :8088）
                 └─ 章分割 → Python sidecar（gRPC :50069, podcast-creator）→ 章別mp3
                      └─ フロント（Next.js :3000）… トラックリスト + 図ギャラリー
                           （音声は既存 API :5055 /api/podcasts/episodes/{id}/audio から配信）
```

- **Rust gateway**: `gateway/`（reinhardt-web 0.3.0-rc.5、SurrealDB Rust SDK 直結、CORS 有効）
- **Python sidecar**: `sidecar/`（LLM/TTS は Python 資産＝podcast-creator をそのまま利用）
- **DB**: SurrealDB。追加マイグレーションは 24（audiobook/章リンク+プロファイル）・25（book_figure+図ナレーション指示）・26（章の生成エラー可視化）・27（mentor_memory 長期記憶）・28（mentor_message 会話ログ + mentor_source_weight 学習傾斜）

## クイックスタート

### 前提

- macOS（Apple Silicon 推奨・MPS 加速）/ Rust 1.96（rustup）/ Python 3.11+（uv）/ Node 22 / Docker or Podman
- 姉妹リポを隣に配置: `../Rust_DN_SuperBook_PDF_Converter`（`superbook-pdf/ai_bridge/ai_venv` に yomitoku venv — 同リポの README 参照）
- `.env` に API キー（`.env.example` 参照）:
  - `ANTHROPIC_API_KEY` … 台本・図キャプション（claude-sonnet-5）
  - `GOOGLE_API_KEY` … TTS（gemini-3.1-flash-tts-preview）+ 埋め込み（gemini-embedding-001）
  - `DEEPSEEK_API_KEY` …（任意）低コスト台本の比較評価用

### 起動〜1冊目

```bash
# 1. 全サービス起動（SurrealDB + API + worker + sidecar + gateway）
make book-stack
# フロントは別ターミナルで
make run
# もしくはコンテナで全部（Tailscale 対応、SETUP.md §6）:
#   podman compose -f docker-compose.book.yml up -d --build

# 2. モデル登録 + デフォルト設定（初回のみ。LLM=/TTS= で差し替え可、ローカルTTSも可）
make set-book-models

# 3. PDF → Markdown（408頁で30分前後 / MPS）
make convert-book PDF=input/本.pdf

# 4. 取り込み（図キャプション + 埋め込み）
make ingest-book DIR=data/books/本 PDF=input/本.pdf TITLE=本のタイトル

# 5. 生成と視聴
#    http://localhost:3000/podcasts → 「オーディオブック」タブ → 「オーディオブック生成」
#    質問は http://localhost:3000/notebooks の該当ノートブックから（chat / ask）
```

### MCP で外部から使う（Claude Code の例）

```bash
claude mcp add book-navigator -- \
  uv run --env-file /path/to/noteBoogie/.env \
  python /path/to/noteBoogie/scripts/book_mcp_server.py
# ツール: list_books / search_books / ask_book / list_figures
```

## 品質・コストの道具箱

| スクリプト | 役割 |
|---|---|
| `scripts/eval_transcript.py` | 台本の自動採点（構成遵守・捏造リスク・敬体一貫性・長さ）+ モデル間比較（品質×コスト表） |
| `scripts/optimize_briefing.py` | **RLプロンプト最適化**（行動=編集案 / 状態=試行履歴 / 報酬=品質−λ·トークン / 方策=LLM最適化器のビーム登坂）。`--apply` でプロファイルへ反映 |

実測の目安（408頁・8章・2026-07時点）: 変換 $0（ローカル）/ 取り込み ~$0.7（vision 127図 + 埋め込み）/ オーディオブック ~$4.6（台本 sonnet-5 + TTS gemini）。詳細と削減オプションは [docs/book-navigator/](docs/book-navigator/README.md) を参照。

## テスト

```bash
uv run pytest tests/                 # Python（760+）
cd gateway && cargo test             # Rust gateway（モックsidecar統合テスト含む・40+）
cd frontend && npm test              # vitest（180+）
cd frontend && npm run test:e2e      # Playwright（ネットワークモックで密閉実行）
```

CI（GitHub Actions）は上記すべて + lint / typecheck / build を実行します。

## ドキュメント

- **[docs/book-navigator/](docs/book-navigator/README.md)** … 本フォークのセットアップ詳細・使い方・アーキテクチャ・発展ロードマップ（日本語）
- [docs/book-navigator/COMPARISON.md](docs/book-navigator/COMPARISON.md) … **NotebookLM / Open Notebook / noteBoogie 3者比較**（なぜハルシネーションが構造的に減るのか）
- [docs/](docs/index.md) … フォーク元 Open Notebook の公式ドキュメント（英語）
- `.kiro/specs/` … 開発時の要件・設計・タスク定義

## ライセンス

MIT（フォーク元 [lfnovo/open-notebook](https://github.com/lfnovo/open-notebook) に準拠）。PDF変換は姉妹リポ [Rust_DN_SuperBook_PDF_Converter](https://github.com/clearclown/Rust_DN_SuperBook_PDF_Converter) を利用しています。
