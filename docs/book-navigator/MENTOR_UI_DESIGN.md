# メンター/コーチ UI 設計書 v2

**確定方針（2026-07-23 壁打ち）**: フロントは Next.js 続行（Vite 移行なし、upstream 同期を維持）。
コーチ機能は①会話壁打ち ②スライドレビュー ③蔵書傾斜設定 の3タブ構成の `/mentor` ページ。
会話UIは**チャットボット型で確定**。追加要件: **回答の音声再生**（師匠の回答をTTSで聴ける）と、
スライドの**UI/デザイン指導を構造的に表示**できること（§11の軸別スコア+指摘リスト表示が担う）。

対象: 既存の mentor グラフ（recall→respond→memorize、`open_notebook/graphs/mentor.py`）を
フロントエンド（Next.js :3000）から使えるようにする。現状は MCP（`consult_mentor`）のみ。

## 0. 設計方針

- **専用ページ `/mentor`**（既存 notebook chat の1モードにしない）。理由: メンターは
  「特定ノートブックの文脈」ではなく蔵書全体+長期記憶が主語であり、既存 chat の
  セッション/コンテキスト選択UIはむしろ邪魔になる
- 会話の永続化はメンター側の `mentor_memory`（要点）に加え、**表示用の生ログ**を
  新テーブルに保存（リロードで会話が消えないこと）
- 既存資産を最大限流用: AppShell / MarkdownRenderer（引用付き回答の表示）/
  sonner トースト / i18n / TanStack Query のフック形

## 1. 画面構成（ワイヤーフレーム）

```
┌─ AppSidebar ─┬────────────────────────────────────────────────┐
│  …           │  🧑‍🏫 師匠に相談                    [記憶を見る ▾] │
│  Create      │ ┌──────────────────────────────────────────────┐ │
│   Podcasts   │ │ （空状態: 「資料の壁打ち・仕事の進め方・     │ │
│  Mentor ←新  │ │   キャリア、何でも相談してください」＋       │ │
│  …           │ │   サンプル質問チップ 3つ）                   │ │
│              │ │                                              │ │
│              │ │  [弟子] 初回提案の構成を壁打ちしたい…       │ │
│              │ │  [師匠] 結論から言うと、その並びは自社視点   │ │
│              │ │         です。『コンサル頭のつくり方』では…  │ │
│              │ │         ┌ 参照した本 ───────────────┐        │ │
│              │ │         │ 📖 コンサル頭のつくり方    │        │ │
│              │ │         └──────────────(クリック→Source)──┘   │ │
│              │ │  [師匠] （応答中… ローディングドット）       │ │
│              │ └──────────────────────────────────────────────┘ │
│              │ ┌──────────────────────────────────────────────┐ │
│              │ │ 相談を入力…                     [送信 ⏎]     │ │
│              │ └──────────────────────────────────────────────┘ │
└──────────────┴────────────────────────────────────────────────┘

[記憶を見る] ドロップダウン/シート:
┌ 過去の相談（mentor_memory）────────────────┐
│ 7/23 提案資料の構成 → 課題認識から始める    │
│ 7/20 報告の悩み → 結論から話す              │
│  （クリックで該当の質問を入力欄へ再挿入）   │
└─────────────────────────────────────────────┘
```

## 2. コンポーネント分解

```
app/(dashboard)/mentor/page.tsx        … ページ（薄い。フック呼び出しとレイアウト）
components/mentor/
  MentorChat.tsx                       … 会話ストリーム（メッセージリスト+自動スクロール）
  MentorMessage.tsx                    … 1メッセージ（役割アイコン、MarkdownRenderer、
                                          師匠側は「参照した本」チップ列）
  MentorComposer.tsx                   … 入力欄（textarea 自動伸長、⌘Enter送信、送信中disable）
  MentorMemoryPanel.tsx                … 記憶一覧（シート/Popover、日付+質問+要点）
  MentorEmptyState.tsx                 … 空状態+サンプル質問チップ
lib/api/mentor.ts                      … apiClient 経由（:5055、認証は既存インターセプタ）
lib/hooks/use-mentor.ts                … 会話状態+送信ミューテーション+記憶クエリ
```

新規は7ファイル。プレイヤーのような複雑状態はないため Zustand は不要
（会話ログはサーバー永続 + TanStack Query キャッシュで足りる）。

## 3. バックエンド追加（FastAPI）

mentor グラフは in-process のため、フロント用に薄い REST を追加する:

| エンドポイント | 内容 |
|---|---|
| `POST /api/mentor/consult` `{message}` | mentor グラフを ainvoke。応答 `{answer, sources:[{id,title}]}`。モデルは DefaultModels.default_chat_model |
| `POST /api/mentor/speak` `{message_id}` | 師匠回答のTTS化（DefaultModels.default_text_to_speech_model、生成mp3は `data/podcasts/mentor/` にキャッシュ）→ audio/mpeg ストリーム。UI は各回答の🔊ボタンから再生 |
| `GET /api/mentor/messages?limit=50` | 表示用の会話ログ（新テーブル `mentor_message`、下記） |
| `GET /api/mentor/memories?limit=20` | `mentor_memory` の一覧（記憶パネル用） |
| `DELETE /api/mentor/memories/{id}` | 記憶の削除（誤学習の手動修正） |

**migration 28**: `mentor_message { role: "user"|"mentor", content: string, sources: option<array<string>>, created }`
— mentor グラフの `memorize` ノードは要点のみ保存する設計のため、**表示用の生ログは
API 層で保存**する（graph は変更しない。consult ハンドラが user/mentor の2行を書く）。

実装は既存パターン踏襲: `api/routers/mentor.py` + ルータ登録、typed exceptions、
`sources` は recall が返す `parent_id` を Source title に解決して返す。

## 4. データフロー

```
MentorComposer 送信
  → useMentor().send(message)
      1. 楽観的に user メッセージを表示（既存 chat と同じパターン）
      2. POST /api/mentor/consult（タイムアウトは既存の10分設定に従う）
      3. 応答を追加、参照本チップを sources から描画
      4. QUERY_KEYS.mentorMessages / mentorMemories を invalidate
  失敗時: 楽観メッセージにエラーバッジ+再送ボタン、sonner トースト
```

ストリーミング（SSE）は v1 では**やらない**。mentor 応答は1コールで数秒〜十数秒
（ask と同等）であり、既存 `useAsk` の SSE パターンは将来の改善として温存。
v1 は「応答中…」のタイピングインジケータで十分。

## 5. i18n（全14ロケール、`mentor.*` セクション新設）

`title / subtitle / placeholder / send / thinking / memoryButton / memoryTitle /
memoryEmpty / referencedBooks / retry / sampleQuestion1..3 / consultError`
（約13キー。パリティテストが強制するため一括投入）

## 6. サイドバー導線

`AppSidebar` の Create セクションに「Mentor」（アイコン: GraduationCap）を追加。
i18n: `nav.mentor`。

## 7. テスト計画

| レイヤ | 内容 |
|---|---|
| pytest | `api/routers/mentor.py`: consult 正常系（graphモック）/ グラフ例外→typed error / メッセージ・記憶の一覧・削除 / 生ログ2行書き込み |
| vitest | `use-mentor`（楽観更新→成功/失敗ロールバック）、`MentorChat`（空状態・参照本チップ・エラー再送）、`MentorComposer`（⌘Enter・送信中disable） |
| Playwright | `/mentor` 密閉モック: 相談→応答表示→参照本チップ→記憶パネルに反映、の1シナリオを既存 e2e スイートに追加 |
| migration | 28 のスキーマ検証を `test_book_navigator_migrations.py` に追加 |

## 8. 見積り

| 作業 | 規模 |
|---|---|
| migration 28 + api/routers/mentor.py + テスト | S〜M |
| lib/api + hooks + i18n | S |
| コンポーネント4+ページ+サイドバー | M |
| vitest + Playwright | S〜M |
| 合計 | **1実装ラウンド**（このリポの既存パターンの組合せのみ、新規技術なし） |

## 9. 将来拡張（v1 では対象外）

- SSE ストリーミング応答（`useAsk` パターン流用）
- 記憶の構造化（宿題/決定事項のタグ付け、`mentor_memory.topics`）
- 複数ペルソナ切替（職業別の師匠 — プロファイル選択UIは今回の生成ダイアログと同型）
- 資料ファイル添付レビュー（アップロード→Source化→相談に自動添付）


---

# v2 拡張: コーチ機能（スライドレビュー + 蔵書傾斜）

## 10. ページ構成の変更（3タブ）

```
/mentor
├─ 💬 相談        … v1 の会話UI（§1〜§4）
├─ 📊 スライド     … スライドレビュー（§11）
└─ ⚖️ 学習の傾斜   … 蔵書の重み付け（§12）
```

## 11. スライドレビュー

### 入力（両対応）
| 形式 | 経路 | 得られるもの |
|---|---|---|
| PNG/JPG/PDF | アップロード → PDFはページ毎に画像化（pymupdf） → vision | 見た目の全評価（デザイン・トンマナ・グラフ） |
| pptx | python-pptx で構造解析（テキスト・フォント種数・色パレット・図形/整列座標） | 定量lint（トンマナ検査が正確）+ テキストは論理評価へ + **修正の適用（§11b）** |

### レビュー観点（5軸ルーブリック、各0〜5点 + 指摘 + 書き直し例）
1. **論理整理・論点整理** — メッセージライン（So What?）、ピラミッド構造、スライド間の流れ
2. **メッセージ×ボディ整合** — タイトルの主張をボディが証明しているか
3. **表・グラフの整理** — チャート選択の適否、データインク比、軸・単位・強調
4. **トンマナ** — フォント種数（pptx: >2で警告）、色数、表記揺れ、余白の一貫性
5. **デザイン指導** — 整列・近接・強調、視線誘導

- **蔵書グラウンディング**: メッセージ内容で蔵書を検索し「『本』では〜」と原則を引用（傾斜§12が効く）
- **最低品質ゲート**: 各軸のしきい値（既定3.0）未満は「未達」バッジ + 最優先の直し1点を提示

### API
| エンドポイント | 内容 |
|---|---|
| `POST /api/mentor/slide-review`（multipart） | ファイル受領 → 形式判別 → 画像化/構造解析 → vision+LLM レビュー → `{overall, axes:[{name,score,issues[],fix}], citations[], gate:{passed,threshold}}` |
| `GET /api/mentor/slide-reviews` | 過去レビュー一覧（再訪・改善差分の確認） |

migration 29: `slide_review { filename, axes: object, overall: float, passed: bool, created }`

### 11b. 修正の適用（pptx編集 — Claude のドキュメントスキル相当を目指す）

レビューで終わらず**直させる**。pptx 入力時は指摘ごとに「適用」ボタンを出し、
python-pptx で編集した**修正版 pptx をダウンロード**できるようにする:

| 自動適用できる修正 | 実装 |
|---|---|
| トンマナ統一（フォントを1〜2種に正規化、色をパレットへ丸め） | run/shape のスタイル一括置換 |
| タイトルのメッセージライン化（So What? 書き換え案の反映） | タイトルプレースホルダのテキスト差し替え |
| 表の整理（桁区切り・単位の統一、ヘッダ強調） | table cell の走査・書式設定 |
| 整列スナップ（左端・上端の座標を揃える） | shape.left/top の量子化 |

適用は**非破壊**（元ファイル保持、`<name>_coached.pptx` を新規生成）。レイアウト崩れリスクの
ある修正（要素の移動・削除）は適用対象にせず指摘に留める。
API: `POST /api/mentor/slide-review/{id}/apply {issue_ids[]}` → 修正版のダウンロードURL。

### UI
ドロップゾーン → ページサムネイル列 → 選択ページのレビュー結果（軸スコアのレーダー/バー + 指摘リスト〔pptx時は「適用」ボタン付き〕 + 引用チップ）。レビュー結果から「💬相談で深掘り」ボタンで会話タブへ引き継ぎ。修正適用後は「修正版をダウンロード」。

## 12. 学習の傾斜（蔵書20冊+の重み付け）

### データモデル（migration 28 に同居）
```
mentor_source_weight {
  source: record<source>,          -- 本
  weight: float (0.0〜2.0, 既定1.0), -- 本単位の手動傾斜
  chapter_weights: option<object>, -- {"0": 1.5, "3": 0.5, ...} 章単位の微調整
  updated
}
```

### 効き方（recall ノードの再ランク）
```
effective(source) = manual_weight(source) × auto_factor(source)
auto_factor = 1 + α·log(1 + 直近の相談で参照された回数)   # α=0.15, 上限1.5
score' = similarity × effective(source)
→ 再ソートして上位N件をプロンプトへ（weight 0 の本は除外）
```
- **手動**（本単位+章単位）: UI のスライダーで設定。章単位は章⇔チャンク対応が付くまでは
  「章インサイト（#46）へのヒット」と「章タイトルを含むチャンク」に適用する近似から開始
- **自動傾斜**: mentor_memory.sources の出現頻度から算出（実装は関数1つ、追加コストゼロ）。
  UI に「自動傾斜の現在値」を読み取り専用で表示し、手動と掛け算合成

### UI（⚖️タブ）
本のリスト（タイトル・チャンク数・手動スライダー0〜2・自動係数バッジ）→ 行を展開すると
章リスト（audiobook の章タイトルを流用）+ 章スライダー。保存は行単位の PATCH。

| エンドポイント | 内容 |
|---|---|
| `GET /api/mentor/weights` | 全本の {source, title, weight, chapter_weights, auto_factor} |
| `PUT /api/mentor/weights/{source_id}` | 手動傾斜の更新 |

## 13. 実装フェーズ（改訂）

| フェーズ | 内容 | 規模 |
|---|---|---|
| C1 | migration 28（mentor_message + mentor_source_weight）+ recall 傾斜（手動×自動）+ テスト | S〜M |
| C2 | v1 会話UI（§1〜§7のまま）+ ⚖️傾斜タブ + API | M |
| C3 | スライドレビュー: 画像/PDF 経路（vision）+ migration 29 + 📊タブ | M |
| C4 | pptx 構造解析 lint（python-pptx）を C3 に合流 | S〜M |
