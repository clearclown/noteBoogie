# モデル選定ポリシー — 構造的原理と役割別の適性

モデルの序列は数ヶ月で入れ替わるため、**「今どれが最強か」ではなく「どの役割に
どんな特性が必要か」**を固定し、具体的なモデル名は実測で差し替える。本書は
その不変の選定原理（2026-07 の研究知見に基づく）と、現在の割り当てを記録する。

## 1. 構造的原理（モデルが変わっても変わらない）

1. **能力 ≠ 信頼性（pass@1 ≠ pass^k）** — τ-bench の実測では、単発成功率 61% の
   フロンティアモデルでも同一タスク8回連続成功は 25% に落ちる。エージェントの
   役割には「一度できる」ではなく「毎回できる」が必要で、両者はベンチマークで
   乖離する。→ 反復実行される役割ほど、賢さより一貫性で選ぶ
2. **長ホライズンはエラーが複利で伝播する** — per-step の小さな誤りは加算でなく
   複利で悪化し、しかも誤りは正の相関を持つ（混乱したエージェントは混乱し続ける）。
   → モデル選定より先に**ホライズンを短く切る設計**が効く。本プロジェクトが
   章単位生成・ノード単位グラフ・決定的ガードレールを採るのはこのため
3. **較正（calibration）は訓練目標と逆行する** — 標準ベンチは「自信のある誤答」に
   報酬を与える選択圧を持ち、LLM は系統的に過信する。「知らないと言え」という
   プロンプト指示は強制力がない。→ 不確実性の扱いは**プロンプトでなく決定的な
   分岐で実装する**（本プロジェクトの Self-RAG 分岐・品質ゲートが該当）
4. **指示遵守と構造化出力の忠実性が、エージェント適性の一級特性** — 生の推論力
   （数学・コーディングのベンチ）はエージェント適性の代理変数として不適。
   台本のような「厳格なフォーマット遵守 + 文章品質」の役割は、
   writing / instruction-following 系の評価で選ぶ
5. **役割ごとに選び、頻度×難易度で層別する** — ルーティング研究の帰結:
   高頻度・短ホライズン・構造化の呼び出しに最上位モデルを使うのはコストだけでなく
   レイテンシ・タイムアウトの面でも不利。低頻度・高難度（計画・統合・最適化）に
   フロンティアを充てる。パイプラインでは役割間の割当が結合するため、
   変更時は**組み合わせで**実測する
6. **専用モデルは専用アリーナで選ぶ** — TTS は人間のブラインド選好（Elo）と
   対象言語のサポート、埋め込みは多言語検索ベンチ + 自コーパスでの実測。
   汎用LLMの序列とは独立に評価する

## 2. 役割別の要求特性と現在の割り当て（2026-07）

| 役割 | 頻度/ホライズン | 支配的な要求特性 | 現在の割り当て | 根拠 |
|---|---|---|---|---|
| 台本生成（章→独話） | 高頻度・中 | **指示遵守・文章品質・長文一貫性**（原理4） | claude-sonnet-5 | 独立評価で writing/instruction-following 部門を Opus 4.8 より上位で獲得。自前ハーネス実測でも構成遵守 0.8-1.0 |
| 検索分解（ask strategy/answer） | 高頻度・短 | 構造化出力の忠実性・低レイテンシ（原理1,5） | claude-sonnet-5 | 短ホライズンの構造化タスクにフロンティア不要 |
| 統合回答・メンター応答・スライドレビュー | 低頻度・単発 | 推論深度・知識統合（原理5） | claude-opus-4-8 | GDPval 系の知識労働で最上位。単発なので一貫性の乖離が効きにくい |
| プロンプト最適化器・オペレータ具体化 | 稀・高難度 | 因果推測・編集設計（原理5） | claude-opus-4-8 | 世代あたり1回、質が支配的 |
| 採点・ゲート・Self-RAG判定 | 毎回 | **決定性**（原理1,3） | **LLM不使用**（正規表現指標+閾値） | 較正問題を構造的に回避。判定はテスト可能・再現可能 |
| 図キャプション（vision） | 取り込み時のみ | 知覚の正確さ・記述の簡潔さ | claude-sonnet-5（`--caption-model` で変更可） | 知覚タスクは指示遵守系で十分。図表の推論が要る本は opus に切替 |
| TTS | 高頻度 | 対象言語（日本語）の自然性 = 人間選好Elo | gemini-3.1-flash-tts-preview | TTSアリーナ Elo 1211・日本語は高品質評価対象言語 |
| 埋め込み | 取り込み時 | 多言語検索性能 + 自コーパス実測 | gemini-embedding-001 | MTEB(Multilingual) 首位。JMTEB 系（Ruri/Sarashina）は自コーパス比較の候補 |

## 3. 情報の陳腐化への構造的な備え（このプロジェクトの答え）

新モデルが出たら順位表を信じずに、**自分のタスクで測って差し替える**。その装置は
すでに常設されている:

1. `scripts/eval_transcript.py --compare provider:model …` — 同一章で台本モデルを
   品質×コストで実測比較（新モデルの追加は PRICES に1行）
2. `make set-book-models LLM=… TTS=…` / GUI — 差し替えは設定であってコードでない
3. 品質ゲート + `quality_event` — 差し替え後の劣化は生成のたびに自動検出・記録される
4. RL最適化器 — モデルを替えたらプロンプトも最適で無くなる前提で、報酬付きで再調整できる

つまり「どのモデルが良いか」は**このリポジトリでは検証可能な問い**であり、
本書の割り当て表は実測で上書きされることを想定している。

## 出典（2026-07 時点の調査）

- [τ-bench (Yao et al.)](https://arxiv.org/abs/2406.12045) / [Sierra の解説](https://sierra.ai/blog/benchmarking-ai-agents) — pass^k と能力/信頼性の乖離
- [Beyond pass@1: Reliability Science Framework](https://arxiv.org/abs/2603.29231) / [Capable but Unreliable](https://arxiv.org/pdf/2602.19008) — 長ホライズンの複利的エラー伝播
- [Where LLM Agents Fail](https://arxiv.org/pdf/2509.25370) / [Towards a Science of AI Agent Reliability](https://arxiv.org/pdf/2602.16666)
- [LLM Calibration in Production Agents](https://zylos.ai/research/2026-04-18-llm-calibration-uncertainty-production-agents) / [Reducing LLM Hallucinations](https://www.getzep.com/ai-agents/reducing-llm-hallucinations/) — 較正と「知らない」の構造的困難
- [Model routing 実務論](https://workos.com/blog/model-routing-vs-tool-routing-ai-agents) / [AgentGate](https://arxiv.org/pdf/2604.06696) — 役割×モデル層別
- [Sonnet 5 vs Opus 4.8 独立評価](https://llm-stats.com/blog/research/claude-sonnet-5-vs-claude-opus-4-8) / [CodingFleet比較](https://codingfleet.com/blog/claude-sonnet-5-vs-claude-opus-4-8/) — writing/instruction-following 部門の序列
- [Gemini 3.1 Flash TTS レビュー](https://texttolab.com/blog/gemini-tts-review) / [GIGAZINE 日本語ハンズオン](https://gigazine.net/gsc_news/en/20260416-gemini-3-1-flash-tts/) — TTSアリーナと日本語品質
- [MTEB 多言語リーダーボード解説](https://milvus.io/blog/choose-embedding-model-rag-2026.md) / [Embedding比較](https://aimultiple.com/embedding-models) — gemini-embedding-001 の位置
