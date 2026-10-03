# noteBoogie ドキュメント

noteBoogieは、蔵書の原本・本文・出典・読書記録を管理する個人用の蔵書基盤を目指します。mangagaがiPhoneの日常利用を担います。2026-10-03に役割と要件を再構成しました。要件文書は到達目標、USAGE・ARCHITECTUREは既存機能の説明として読み分けてください。

| ドキュメント | 内容 |
|---|---|
| [PMVV.md](PMVV.md) | 目的・役割分担・成功条件・初回と後続の範囲 |
| [MANGAGA_INTEGRATION.md](MANGAGA_INTEGRATION.md) | 提供側要件NB-01〜10の正本。取り込み・読書・質問・記録・運用 |
| [MANGAGA_CONTRACT_ADDENDUM.md](MANGAGA_CONTRACT_ADDENDUM.md) | 共通契約v0.1への追加案。位置・公開ID・履歴・認証・削除 |
| [MANGAGA_ACCEPTANCE.md](MANGAGA_ACCEPTANCE.md) | A〜Dの完成条件、品質・運用測定、未確定事項（試験は未実施） |
| [PDR-003](../7-DEVELOPMENT/decisions/PDR-003-personal-library-and-mangaga.md) | 蔵書基盤とmangagaの役割を定めた理由 |
| [SETUP.md](SETUP.md) | セットアップ詳細（前提ツール・姉妹リポ・venv・モデル設定・環境変数） |
| [USAGE.md](USAGE.md) | 使い方（変換→取り込み→質問→生成→視聴の全手順、スクリプト群、MCP、トラブルシュート） |
| [ARCHITECTURE.md](ARCHITECTURE.md) | アーキテクチャ（gateway/sidecar/データフロー/マイグレーション/既知の制約） |
| [QUALITY_PIPELINE.md](QUALITY_PIPELINE.md) | 章構造の品質保証（DNの限界・抽出LLM章検出・ハルシ対策・標本Vision QA・3台分散） |
| [ADVANCED_ROADMAP.md](ADVANCED_ROADMAP.md) | 過去の発展案（MCP拡張・メンターAI・RL段階2）。現在の優先順位はPMVVを参照 |
| [MENTOR_UI_DESIGN.md](MENTOR_UI_DESIGN.md) | メンターAI フロントエンドUIの設計書（/mentor ページ・API・テスト計画） |
| [RETROSPECTIVE.md](RETROSPECTIVE.md) | 開発振り返り（インシデント・学び・数値） |
| [COMPARISON.md](COMPARISON.md) | NotebookLM / Open Notebook / noteBoogie 3者比較（機能表 + ハルシネーション層別分析） |

クイックスタートはリポジトリ直下の [README.md](../../README.md) を参照してください。
