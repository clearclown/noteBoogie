# メンターAI フロントエンド UI 設計書

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
