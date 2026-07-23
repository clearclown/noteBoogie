# Book Navigator 発展ロードマップ（壁打ち用）

3つの発展課題について、実装方針の候補と「決めるべき論点」を整理する。
次の対話でここの論点に答えると、そのまま実装計画になる。

現状の資産（前提）: SuperBook変換（縦書き・表・図分離）→ ingest（Notebook/Source/
book_figure + vision キャプション + 埋め込み）→ ask/chat（LangGraph、引用付き）→
章別音声（gateway+sidecar）→ フロント（トラックリスト+図ギャラリー）。
品質評価ハーネス（`scripts/eval_transcript.py`）と RL 最適化
（`scripts/optimize_briefing.py`、実測 0.688→0.732）が稼働済み。

---

## 1. MCP サーバーで書籍ナレッジを外部公開

NotebookLM が Gemini から呼べるように、取り込んだ書籍を Claude Desktop /
Claude Code / 任意の MCP クライアントから呼べるようにする。

**推奨アーキテクチャ**: Python FastMCP サーバー（`mcp` パッケージ、stdio）。
既存ドメイン層を in-process で import するだけなので、ask グラフ・
`vector_search`・`book_figure` がそのまま使える。Rust gateway に足すより薄い。

**ツール案（最小4つ）**:
| ツール | 実装 | 説明 |
|---|---|---|
| `search_books(query, limit)` | `vector_search()` | 意味検索。チャンク+出典を返す |
| `ask_book(question)` | ask グラフ | 検索→統合回答（引用付き） |
| `list_books()` / `get_chapter(source_id, index)` | repo_query | 蔵書一覧・章本文 |
| `list_figures(source_id)` | book_figure | 図キャプション一覧（画像はパス返却） |

**決めるべき論点**:
- [ ] 接続形態: stdio（ローカル、Claude Desktop/Code 向け・推奨）か HTTP+SSE（リモート）か
- [ ] DB/worker の起動前提: MCPサーバーが SurrealDB 起動を要求してよいか、自動起動するか
- [ ] 認証: ローカル stdio なら不要。HTTP にするなら要トークン
- [ ] ask のモデル指定: 固定（sonnet-5）か MCP 側パラメータか

## 2. メンター/コーチ機能（書籍ナレッジ搭載の師匠AI）

蔵書を完全理解した「コンサルの師匠」。資料作成相談・壁打ち相手。

**推奨アーキテクチャ**: 既存 chat グラフの拡張ではなく**専用グラフ** `mentor.py`:
1. ユーザー発話 → 戦略ノード（何を参照すべきか判断、ask の Strategy を流用）
2. **蔵書横断 vector_search**（現状の ask がグローバル検索なのはここでは利点）
3. ペルソナ付き応答（システムプロンプト: 師匠の人格・「本書の第N章では…」と
   出典を会話に織り込む話法）
4. **長期記憶**: 相談履歴の要約を `mentor_memory` テーブルに保存し、
   次回セッションの文脈に注入（「先週の資料の件はどうなった?」）

**決めるべき論点**:
- [ ] ペルソナ定義: 単一の師匠か、職業別（コンサル/PM/…）に複数プロファイルか
- [ ] 記憶の粒度: セッション要約のみか、決定事項・宿題のような構造化記憶か
- [ ] UI: 既存 notebook chat の1モードか、専用ページ（/mentor）か
- [ ] 資料レビュー機能: ファイルアップロード→講評まで含めるか（Source化して参照）
- [ ] MCP（課題1）経由で Claude Desktop から師匠を呼ぶ形に統合するか

## 3. RL プロンプト最適化の発展形

Phase O で LLM-as-optimizer（OPRO系）は稼働済み。真のRLへの発展径路:

**段階1（済）**: 行動=自由編集、方策=optimizer LLM、報酬=品質−λ·トークン
**段階2**: 行動の離散化 — 編集オペレータ集合を定義
  （例: `add_rule(text)` / `remove_sentence(i)` / `rephrase(i, style)` /
  `reorder(i,j)` / `set_length_hint(n)`）。これで (状態, 行動, 報酬) の
  ログが蓄積可能になり、bandit（Thompson sampling でオペレータ選択）→
  方策勾配（小型LMをLoRAで微調整）へ進める
**段階3**: 報酬モデル蒸留 — 人手評価（聴いた感想）を少量集めて
  自動指標との回帰を学習、judge を報酬モデル化

**決めるべき論点**:
- [ ] 最適化対象の拡張: briefing 以外（図キャプションプロンプト、ask/chat の
      システムプロンプト、メンターペルソナ）もハーネスに載せるか
- [x] 人手フィードバックの収集UI: ✅ 章の👍/👎 実装済み（`episode.feedback`、2026-07）。
      報酬への混ぜ込み（段階3）はデータが溜まってから
- [ ] 予算方針: 1回の最適化に使ってよいトークン上限（現在 300k デフォルト）
- [ ] 段階2に進む判断基準: 段階1の改善が飽和したら（報酬の伸びが2世代連続 <1%）

---

## 4. 品質ゲートの生成ループ組み込み + Self-RAG（✅ 実装済み 2026-07）

設計思想の対比（合意済みの整理）: NotebookLM は「優秀なLLMを信頼する」設計で、
検索と生成の間・生成の後に検証が無く、失敗しても観測できない。noteBoogie は
「LLMを信頼しない」設計 — LLM に任せるのは台本の文体と対話だけに絞り、構造化は
決定的処理（章分割・表復元・読み順）に、危険な入力はルールで排除（章ガードレール）、
出力は独立採点する。**ゼロにはできないが、測れて・直せて・テストできる**。

正直な限界（自己申告）:
- 捏造リスク採点は LLM-as-judge であり、それ自体が確率的
- ask は蔵書グローバル検索で notebook スコープ指定ができない
- 全量変換の manifest 章リストには柱の再検出ノイズが残る（図の章対応精度に影響）
- 「検索が外れたときに LLM が埋める」という RAG の根本問題は完全には消えていない

**実装結果（2026-07）**:

1. ✅ **採点のゲート化**: `sidecar/podcast_runner.py` — transcript 生成直後（**TTS前**）に
   eval_transcript で採点し、`SIDECAR_GATE_THRESHOLD`(0.6) 未満は批評（未達指標→
   日本語の改善指示）を briefing に付けて1回だけ再生成、なお未達なら ValueError →
   generation_error で棄却。判定は決定的（正規表現指標のみ、judge はゲートに含めない）
2. ✅ **Self-RAG 分岐**: `ask.py::provide_answer` は `ASK_EVIDENCE_FLOOR`(0.4) 未満で
   LLM を呼ばず（根拠不足）を返し、全滅時は統合LLMも呼ばず定型で断る。
   `mentor.py::recall_node` は `MENTOR_EVIDENCE_FLOOR` 未満で low_evidence フラグ +
   ヒット破棄 + 「引用を捏造しない」決定的指示。chat は検索を持たないため対象外
   （コンテキストはユーザー選択＝根拠はユーザーが保証する設計）
3. ✅ **判定の永続化**: `quality_event` テーブル（migration 30、kind =
   transcript_gate / ask_refusal / mentor_low_evidence）
4. 残: **下限値の較正**（実トラフィックの quality_event で ROC を取り 0.4/0.6 を調整）

## 短期の残タスク（発展課題の前）

- ~~全量E2E（408ページ）~~ ✅ 完了（13/13章・282分・実測 ~$10.9、RETROSPECTIVE 参照）
- コスト切替の判断: ハーネス実測で **haiku-4.5 が composite 同点(0.69)・コスト-68%**。
  TTS は gemini-3.5-flash-tts（$20→$6/M）が有力。DeepSeek は**残高チャージ後に再計測**
  （キー名は `DEEPSEEK_API_KEY` が正。現在 .env は `DEEP_SEEK_API_KEY`）
- 薄い章のグラウンディング対策: 全量E2Eでは実章本文で自然改善する見込みだが、
  briefing 最適化（済: 捏造禁止ルール追加）+ 長さ比の監視を継続
