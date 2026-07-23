# アーキテクチャ

## 全体像

```
スキャンPDF（縦書き和書）
  │  superbook-pdf markdown（別リポ・Rust）
  │    PDFラスタライズ → 傾き/回転補正 → YomiToku OCR（MPS）
  │    章見出し検出（第N章 + フォントサイズ）/ 図切り出し / 表復元 / 読み順ソート
  ▼
本.md + images/ + book_manifest.json
  │  scripts/ingest_book.py（in-process、生テキストのHTTP APIは存在しないため）
  │    図→Claude visionキャプション → mdへ【図:…】埋め込み
  ▼
SurrealDB
  ├─ notebook / source(full_text) / reference
  ├─ book_figure（migration 25: 図＋キャプション＋章対応）
  ├─ source_embedding（workerが embed_source ジョブで生成）
  └─ audiobook / episode（migration 24/26: 章リンク・生成エラー）
  │
  ├─ FastAPI :5055 …… chat / ask（LangGraph）・章音声の配信
  ├─ MCP stdio …… scripts/book_mcp_server.py（search/ask/figures）
  └─ Rust gateway :8088（reinhardt-web）
        │  POST /audiobooks/generate
        │    章分割（chapters.rs）→ 章episode作成 → tokio::spawn ループ
        ▼
     Python sidecar :50069（gRPC / protos/podcast.proto）
        podcast-creator: outline LLM → transcript LLM → TTS → mp3
        │  結果は gateway が episode に永続化（audio_file は PODCASTS_FOLDER 相対）
        ▼
     フロント :3000（Next.js）
        オーディオブックタブ: gateway からメタ/図、API :5055 から音声blob（認証付き）
```

## コンポーネントの責務

| コンポーネント | 責務 | 主要ファイル |
|---|---|---|
| gateway（Rust） | オーディオブックのCRUD・章分割・生成オーケストレーション・図API | `gateway/src/{handlers,repo,chapters,sidecar,models}.rs` |
| sidecar（Python） | LLM/TTS 実行（podcast-creator をラップ、Rust に等価物が無い部分だけ） | `sidecar/{podcast_sidecar,podcast_runner}.py` |
| ingest | 変換出力の取り込み・キャプション・埋め込み投入 | `scripts/ingest_book.py` |
| MCP | 蔵書ナレッジの外部公開 | `scripts/book_mcp_server.py` |
| 評価/最適化 | 台本採点・モデル比較・RLプロンプト改善 | `scripts/{eval_transcript,optimize_briefing}.py` |

## 章分割のガードレール（gateway/src/chapters.rs）

OCR本文の「ほぼ空の章」から LLM が尤もらしい台本を捏造する事故を防ぐ:

1. H1 が2つ以上あれば **H1のみ**で分割（H2は章内の節）
2. **連続する同名章をマージ**（柱＝ランニングヘッダの再検出対策）
3. **本文200字未満の章は次章へ折り込み**（目次ページ・前付け断片対策）

## 音声パスの契約（#1030 準拠）

- sidecar は絶対パスを返す → gateway が `episodes/<id>/audio/<id>.mp3` の**相対形**に変換して保存（`repo::relative_audio_path`）
- API は `PODCASTS_FOLDER`（`./data/podcasts` 固定）配下で解決・封じ込め検証して配信
- したがって **gateway はリポジトリルートから起動**する（`make book-stack` 参照）

## マイグレーション（本フォーク追加分）

| # | 内容 |
|---|---|
| 24 | `audiobook` テーブル、`episode` に audiobook/chapter_index/chapter_title、`book_navigator`/`book_navigator_mentor` プロファイル seed（speaker を先に seed し record link で参照） |
| 25 | `book_figure` テーブル、briefing への図ナレーション指示の追記（冪等ガード付き） |
| 26 | `episode.generation_error`（失敗の可視化） |

登録は `open_notebook/database/async_migrate.py`（ハードコード必須）。upstream が番号を消費するため、マージ時は**改番**が必要になることがある（16/17→24/25 の前例）。`tests/test_book_navigator_migrations.py` が番号ギャップを監視。

## テスト戦略

| レイヤ | 手段 |
|---|---|
| gateway 単体/統合 | `cargo test`。in-memory SurrealDB（kv-mem）+ reinhardt ServerRouter 直駆動 + **モック gRPC sidecar** で生成ループまで検証 |
| Python | pytest（repo_query/anthropic/LLM をモック）。ingest/sidecar/migrations/eval/optimizer/MCP/setup |
| フロント単体 | vitest + Testing Library（fetch/API モック、`e2e/` は除外） |
| フロント統合 | Playwright。**ネットワーク層で両オリジンをモックした密閉実行**（実バックエンド不要、CI は production ビルドで起動） |
| 変換（別リポ） | cargo test（1500+）+ CLI 統合テスト + bridge の stdlib unittest |

## 既知の制約・設計上の逸脱

- `<audio>` 要素はタブ内ローカル（ページ遷移で再生停止）。連続再生設定と視聴位置のみ zustand persist で永続化
- `book_figure.path` は絶対パス保存 → gateway のコンテナ化時は共有ボリューム or 相対化が必要
- ask は蔵書グローバル検索（notebook スコープ不可）。メンターAI 設計（ADVANCED_ROADMAP）ではむしろ利点として扱う
- 全量変換の manifest 章リストは柱の再検出でノイズを含む（オーディオブック側はガードレールで吸収。図の章対応の精度に影響）
- converter の `--include-page-numbers` / `--validate` / `--api-provider` は未配線（help に明示）
