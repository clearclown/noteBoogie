# Structure Steering — ディレクトリ規約

## トップレベル構成

```
noteBoogie/
├── api/                    既存 Python FastAPI（移行完了まで並走）
├── open_notebook/          既存 Python コアロジック（domain/graphs/ai/database/podcasts/utils）
│   └── database/migrations/*.surrealql   DBマイグレーション（async_migrate.py で明示登録）
├── commands/               surreal-commands 非同期ジョブ（podcast_commands.py 等）
├── prompts/podcast/*.jinja podcast-creator が CWD 解決で使用
├── frontend/               Next.js（不変。Book Navigator UI を追加）
│
│  ── 本フォークで追加 ──
├── gateway/                ★ Rust ゲートウェイ（reinhardt-web）。新アーキの主軸
│   ├── Cargo.toml
│   └── src/                main.rs / ルータ / SurrealDB SDK / サイドカー gRPC クライアント
├── sidecar/                ★ Python gRPC サイドカー（create_podcast 等のラッパ）
│   ├── podcast_sidecar.py
│   └── gen/                protoc 生成スタブ
├── protos/                 ★ gRPC 契約（podcast.proto）
└── .kiro/                  ★ steering / specs
    ├── steering/{product,tech,structure}.md
    └── specs/{book-navigator,rust-migration}/
```

## 配置ルール
- **Rust 実装**は `gateway/` 配下。HTTP・DB アクセス・オーケストレーションはここ。
- **Python サイドカー**は `sidecar/` 配下。リポジトリ内に置き `open_notebook`/`commands` を import して既存ロジックを再利用する。
- **gRPC 契約**は `protos/` に単一の真実を置き、Rust（tonic build）と Python（grpc_tools.protoc）双方が生成元とする。
- **DB マイグレーション**は既存の `open_notebook/database/migrations/` に番号順で追加し、`async_migrate.py` の up/down 両リストへ登録。
- **フロントエンド**は `frontend/` 規約に従う（型は `lib/types`、API は `lib/api`、フックは `lib/hooks`、状態は `lib/stores`、i18n は `lib/locales/*`）。

## 命名
- マイグレーション: `N.surrealql` / `N_down.surrealql`（連番）。
- proto: `service PodcastSidecar`、メッセージは `XxxRequest`/`XxxResponse`。
- Rust エンドポイント: 既存 REST 契約に一致（`/audiobooks`, `/podcasts/...`）。
