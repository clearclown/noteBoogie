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
- **ask**: 「Ask and Search」ページ。蔵書全体への横断質問（グローバル検索）。API/MCP からは `notebook_id` で特定ノートブックに限定可能
- **Self-RAG**: 検索の最大類似度が下限（`ASK_EVIDENCE_FLOOR`、既定0.4）未満のときは、LLM を呼ばず「蔵書に十分な根拠が見つかりませんでした」と**正直に断ります**（一般知識で隙間を埋めない）。メンターも同様（`MENTOR_EVIDENCE_FLOOR`）で、根拠不足時は「蔵書に直接の記述はありませんが」と明示した一般論になります

## 4. オーディオブック生成・視聴

http://localhost:3000/podcasts → **オーディオブック**タブ:

- **オーディオブック生成** ボタン → 本（Source）を選択 → **台本モデルと声のプロファイルを選択**（コスト/品質は生成ごとにユーザーが選ぶ。`--create-presets` で節約プリセットを追加可能）→ 生成開始
- 章は H1 見出しで分割され、柱の重複・目次行・200字未満の断片は自動でマージ（ハルシネーション対策）
- トラックリスト: 生成中=バッジ / 失敗=赤バッジ（ホバーでエラー内容）/ 完了=クリックで再生
- **連続再生** ON で章末→次章へ自動送り（設定は永続化）
- **図ギャラリー**: 再生中の章の図＋キャプションを表示（未選択時は全図）
- **👍/👎**: 完了章に感想を付けられる（もう一度押すと取り消し）。プロンプト最適化の報酬データになる

### 品質ゲート（既定ON）

台本は **TTS 前に自動採点**され（構成遵守・捏造リスク・敬体・長さ、追加LLMコストゼロ）、
閾値 0.6 未満なら**批評を添えて1回だけ再生成**、なお未達なら TTS せず棄却されます
（赤バッジ + generation_error に「品質ゲート未達」とスコア内訳）。判定は `quality_event`
テーブルに記録され、閾値の較正データになります。無効化は `SIDECAR_GATE=0`、
閾値変更は `SIDECAR_GATE_THRESHOLD`。

CLI から生成する場合:

```bash
curl -X POST http://localhost:8088/audiobooks/generate \
  -H 'content-type: application/json' \
  -d '{"audiobook_name":"本のタイトル","source_id":"source:xxxx"}'
# 任意: "max_chapters": 2（試し生成）, "briefing_suffix": "追加指示"
```

### mp3 の持ち出し（1フォルダに集約）

生成された章 mp3 は DB 管理のため `data/podcasts/episodes/<id>/` に散在します。
プレイヤーや他端末で聴くときは export でまとめてください:

```bash
make export-audiobook AUDIOBOOK=audiobook:xxxx   # → data/audiobooks/<タイトル>/
# 01_第1章….mp3 … 13_第3部….mp3 + playlist.m3u8（コピーの代わりに --link でハードリンク可）
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
| `ask_book(question, notebook_id?)` | 本文グラウンディング回答（`[source:…]` 引用付き。`notebook_id` でスコープ限定可） |
| `consult_mentor(message)` | 師匠AIとの壁打ち（記憶+蔵書RAG+傾斜） |
| `list_figures(source_id)` | 図のキャプション一覧 |

疎通確認: `uv run --env-file .env python scripts/book_mcp_server.py --selftest`

## 6. モデルの選択（GUI / CLI）

### GUI から

| 対象 | 場所 |
|---|---|
| 台本（outline/transcript）と声（voice） | http://localhost:3000/podcasts → **テンプレート**タブ → `book_navigator` / `book_navigator_mentor` を編集（モデルのドロップダウン） |
| chat / ask / 埋め込み / 変換のデフォルト | http://localhost:3000/settings 系のモデル設定ページ（DefaultModels） |
| モデルの登録・APIキー | Models（API Keys）ページ → クレデンシャル追加 → モデル発見・登録 |

### CLI から

```bash
make set-book-models                                            # 既定: sonnet-5 + gemini TTS
make set-book-models LLM=claude-haiku-4-5                       # 台本を低コスト化
make set-book-models PROVIDER=deepseek LLM=deepseek-v4-flash    # さらに低コスト（品質は eval で確認を）
make set-book-models TTS_PROVIDER=openai_compatible TTS=kokoro  # ローカルTTS（下記）
```

## 7. ローカルTTS（クラウド不要の音声合成）

TTS は esperanto の `openai_compatible` プロバイダ経由で、**OpenAI 互換 `/v1/audio/speech` を喋る任意のローカルサーバ**に差し替えられます。

例: [kokoro-fastapi](https://github.com/remsky/Kokoro-FastAPI)（日本語ボイスあり・Docker一発）

```bash
docker run -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:latest   # Apple SiliconはCPU版

# .env に追記
OPENAI_COMPATIBLE_BASE_URL=http://localhost:8880/v1
OPENAI_COMPATIBLE_API_KEY=not-needed

make set-book-models TTS_PROVIDER=openai_compatible TTS=kokoro
```

以後のオーディオブック生成は TTS 費用ゼロ（品質・読み仮名精度はクラウド比で低下します。日本語特化なら VOICEVOX / AivisSpeech + OpenAI互換ブリッジも同じ方式で接続可能）。台本 LLM も `PROVIDER=ollama LLM=モデル名` でローカル化でき、**完全オフライン生成**構成になります。

## 8. メンターAI（師匠との壁打ち）

蔵書を読み込んだ「師匠」に、資料作成・仕事の進め方・キャリアを相談できます。
過去の相談を記憶（`mentor_memory`）しており、蔵書に根拠がある助言は『本のタイトル』付きで返します。

**ペルソナは切り替え可能**: 既定はコンサルタントの師匠（`default`）ですが、/mentor の
「ペルソナ」ボタンから **generalist / engineer / editor / researcher** のプリセットや
自作プロファイル（`PUT /api/mentor/personas/{name}` → `POST …/activate`）へワンクリックで
切り替えられます。各プロファイルは本文を自由に編集でき、切替は相談とスライドレビューの
両方に即時反映されます。

### 専用ページ /mentor

http://localhost:3000/mentor に3タブ: **💬相談**（チャット・回答の🔊読み上げ・記憶パネル）/
**📊スライド**（アップロード→5軸レビュー→レーダー+バー表示。pptx は指摘を選んで
`_coached.pptx` をダウンロード。「相談で深掘り」で会話タブへ引き継ぎ）/
**⚖️学習の傾斜**（本・章スライダー + 自動傾斜バッジ）。

### REST API

| エンドポイント | 内容 |
|---|---|
| `POST /api/mentor/consult {message}` | 師匠に相談（回答 + 参照した本 + message_id） |
| `POST /api/mentor/speak/{message_id}` | 回答をTTSでmp3化（既定ボイス kore、`data/podcasts/mentor/` にキャッシュ） |
| `GET /api/mentor/messages` / `GET /api/mentor/memories` | 会話ログ / 長期記憶の一覧 |
| `DELETE /api/mentor/memories/{id}` | 誤学習した記憶の削除 |
| `GET /api/mentor/weights` / `PUT /api/mentor/weights/{source_id}` | **学習の傾斜**: 本単位 0.0〜2.0（0=除外）+ 章単位の重み。自動傾斜（よく参照する本を緩やかに加点）と掛け算合成 |

MCP 経由（Claude Code / Desktop）:

```
consult_mentor("クライアント初回提案の構成を壁打ちしたい。今の案は会社紹介→実績→提案→価格")
```

スクリプト直呼びも可能:

```python
from open_notebook.graphs.mentor import graph
result = await graph.ainvoke({"message": "相談内容"},
    config={"configurable": {"mentor_model": "model:xxxx"}})
```

構成: recall（記憶 + 蔵書横断ベクトル検索）→ respond（師匠ペルソナ）→ memorize（要点を長期記憶へ）。
発展（複数ペルソナ・記憶の構造化・フロントUI）は ADVANCED_ROADMAP.md 参照。

## 9. 品質評価とプロンプト最適化

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
| フロントから gateway に繋がらない | 接続先はアクセス元ホスト名から実行時導出（`:8088`）。gateway の bind アドレスと、上書きしている場合は `NEXT_PUBLIC_GATEWAY_URL` を確認（CORS は許可済み） |
| DeepSeek 比較が Insufficient Balance | DeepSeek プラットフォームで残高チャージが必要 |
