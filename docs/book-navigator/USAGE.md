# 使い方

前提: [SETUP.md](SETUP.md) 完了、`make book-stack` + `make run` 起動済み。

## 1. PDF → Markdown 変換

```bash
make convert-book PDF=input/本.pdf            # 出力: data/books/本/
# 部分検証なら: make convert-book PDF=input/本.pdf PAGES="--max-pages 30"
```

出力: `本.md`（結合Markdown）+ `pages/page_NNN.md` + `images/`（cover / page_NNN_full / page_NNN_fig_MMM）+ `book_manifest.json`（章・図・縦横判定）。

便利フラグ（superbook-pdf 直叩き時）:
- `--text-direction vertical` … 縦書き強制（ページ単位の縦横自動判定が割れる本に）
- `--no-detect-tables` … Markdown表の復元を無効化
- `--resume` … 中断からの再開（pages/ 単位）

目安: 408頁 ≈ 30分（M4 Max / MPS）。初回は YomiToku モデルのダウンロードが入ります。

## 2. 取り込み

```bash
make ingest-book DIR=data/books/本 PDF=input/本.pdf TITLE=本のタイトル
```

行われること:
1. 図（figure / full_page）を claude-sonnet-5 vision で日本語キャプション化
2. 本文中の画像リンクを `【図: キャプション】` に置換（音声台本がここで図を語る）
3. Notebook + Source（full_text）+ `book_figure` レコードを作成
4. 埋め込みジョブを投入（worker が処理 → `source_embedding` にチャンク）

オプション: `--no-captions`（キャプション省略）/ `--caption-model claude-haiku-4-5`（低コスト化）。

## 3. 質問（chat / ask）

- **chat**: http://localhost:3000/notebooks → 本のノートブック → チャット。ソースを「full content」でコンテキストに含めると本文グラウンディングされた引用付き回答になります
- **ask**: 「Ask and Search」ページ。蔵書全体への横断質問（グローバル検索）

## 4. オーディオブック生成・視聴

http://localhost:3000/podcasts → **オーディオブック**タブ:

- **オーディオブック生成** ボタン → 本（Source）を選択 → 生成開始
- 章は H1 見出しで分割され、柱の重複・目次行・200字未満の断片は自動でマージ（ハルシネーション対策）
- トラックリスト: 生成中=バッジ / 失敗=赤バッジ（ホバーでエラー内容）/ 完了=クリックで再生
- **連続再生** ON で章末→次章へ自動送り（設定は永続化）
- **図ギャラリー**: 再生中の章の図＋キャプションを表示（未選択時は全図）

CLI から生成する場合:

```bash
curl -X POST http://localhost:8088/audiobooks/generate \
  -H 'content-type: application/json' \
  -d '{"audiobook_name":"本のタイトル","source_id":"source:xxxx"}'
# 任意: "max_chapters": 2（試し生成）, "briefing_suffix": "追加指示"
```

## 5. MCP（Claude Desktop / Claude Code から蔵書を使う）

```bash
claude mcp add book-navigator -- \
  uv run --env-file /abs/path/noteBoogie/.env \
  python /abs/path/noteBoogie/scripts/book_mcp_server.py
```

| ツール | 内容 |
|---|---|
| `list_books()` | 蔵書一覧 |
| `search_books(query, limit)` | 意味検索（チャンク+出典） |
| `ask_book(question)` | 本文グラウンディング回答（`[source:…]` 引用付き） |
| `list_figures(source_id)` | 図のキャプション一覧 |

疎通確認: `uv run --env-file .env python scripts/book_mcp_server.py --selftest`

## 6. 品質評価とプロンプト最適化

```bash
# 台本の自動採点（構成遵守 / 捏造リスク / 敬体一貫性 / 長さ）
uv run --env-file .env python scripts/eval_transcript.py --audiobook audiobook:xxxx

# モデル比較（同一章で再生成し品質×コストを並べる）
uv run --env-file .env python scripts/eval_transcript.py --audiobook audiobook:xxxx \
  --chapters 3 --compare anthropic:claude-sonnet-5 anthropic:claude-haiku-4-5 deepseek:deepseek-v4-flash

# RL プロンプト最適化（briefing を自動改善、--apply でプロファイル反映）
uv run --env-file .env python scripts/optimize_briefing.py \
  --audiobook audiobook:xxxx --generations 3 --beam 3 \
  --gen-model anthropic:claude-haiku-4-5 --apply
```

## トラブルシュート

| 症状 | 原因と対処 |
|---|---|
| 変換結果が「全ページ画像扱い・章0」 | YomiToku venv が `ai_bridge/ai_venv` に無い（SETUP.md §2）。警告ログ「YomiToku利用不可」を確認 |
| 取り込み後、チャットが本文を知らない | worker 未起動で埋め込み未処理。`make worker-start` 後にジョブが流れる |
| 章の音声がフロントで再生できない | gateway をリポジトリルート以外から起動すると mp3 が `./data/podcasts` に入らない。`make book-stack` を使う |
| 生成が「生成中」のまま | sidecar 未起動（gateway 起動ログの `sidecar not reachable` を確認）。失敗時は赤バッジ+エラーが出る |
| フロントから gateway に繋がらない | `NEXT_PUBLIC_GATEWAY_URL` と gateway の bind アドレスを確認（CORS は許可済み） |
| DeepSeek 比較が Insufficient Balance | DeepSeek プラットフォームで残高チャージが必要 |
