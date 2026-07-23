# Technical Steering — ハイブリッド（Rust 主体 + Python サイドカー）

## アーキテクチャ決定

バックエンドは **reinhardt-web（Rust）を主軸**とし、Rust に等価物が無い計算のみ **Python サイドカー**へ gRPC で委譲する（ユーザー決定 2026-06-27）。

```
Next.js (3000)
   │ REST
Rust Gateway (reinhardt-web, :8088)
   ├─ ルーティング / 認証 / シリアライズ / CRUD / オーケストレーション
   ├─ SurrealDB Rust SDK ─────────────→ SurrealDB (8000, v2)
   └─ gRPC ─→ Python Sidecar
                ├─ create_podcast (outline LLM → transcript LLM → TTS) = podcast-creator
                ├─ extract = content-core
                └─ (将来) langgraph ワークフロー / esperanto
既存 Python API (FastAPI, :5055) は移行完了まで並走
```

## フォールバック境界（Rust で作る / Python に委ねる）

| Rust（reinhardt-web） | Python サイドカー（Rust 等価物なし） |
|---|---|
| HTTP/ルーティング/認証/シリアライズ/DI | LLM 推論（outline・transcript）= esperanto/podcast-creator |
| CRUD（notebook/source/note/episode/profile/audiobook） | TTS 音声合成 = podcast-creator |
| SurrealDB アクセス（SDK 直叩き・グラフ・ベクトル） | 多形式抽出 = content-core |
| 生成オーケストレーション / 章パース（pulldown-cmark）/ ジョブ・進捗 | グラフワークフロー = langgraph |
| 音声ファイル配信 | （必要なら）ジョブワーカー = surreal-commands |

## バージョン / ツール（実機検証済み 2026-06-27）
- Rust toolchain: cargo/rustc 1.95.0（導入済み）。
- `reinhardt-web` = **v0.3.0-rc.5**（crates.io 公開、`package = "reinhardt-web"`、alpha/RC のため API 変動に注意）。雛形 CLI: `cargo install reinhardt-admin-cli` → `reinhardt-admin startproject`。
- `surrealdb` Rust SDK v3.x（本リポの SurrealDB v2 に対応）。
- gRPC: Rust=`tonic` 0.14.x、Python=`grpcio`/`grpcio-tools`。
- 開発支援（任意）: `reinhardt-agents-plugin`（Claude Code/Codex プラグイン。**ランタイム依存ではない**）。

## 既存技術スタック（不変）
- フロント: Next.js 16 / React 19 / TypeScript / Zustand / TanStack Query / Tailwind + shadcn。
- DB: SurrealDB（マイグレーションは `open_notebook/database/async_migrate.py` の up/down 明示リスト、`*.surrealql`）。
- AI: esperanto / langgraph / ai-prompter / content-core / podcast-creator / surreal-commands（Python、サイドカーへ集約）。

## 制約・注意
- マイグレーション追加時は `async_migrate.py` のリスト編集が必須（glob 探索ではない）。
- podcast-creator のテンプレートは CWD の `prompts/podcast/*.jinja` を使う。`configure()` はプロセスグローバル → サイドカー内で per-job の `prompts_dir` 切替は避ける。
- alpha FW（reinhardt-web）採用に伴う破壊的変更リスクは継続監視（Phase 0 判断ゲート）。
