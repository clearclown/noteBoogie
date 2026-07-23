"""Unit tests for the transcript quality metrics (scripts/eval_transcript.py)."""

from scripts.eval_transcript import (
    ChapterEval,
    evaluate_chapter,
    extract_fact_terms,
    grounding_score,
    politeness_score,
    structure_score,
    transcript_text,
)

GOOD_TRANSCRIPT = (
    "この章は、報告が伝わらないという悩みに答えます。"
    "1つ目は、結論から話すこと。結論を先に述べることで聞き手の負担が減ります。"
    "2つ目は、根拠を3点に絞ることです。"
    "3つ目は、相手の関心から逆算することです。"
    "では、明日からのアクションプランです。まず、次の会議で結論から話してみましょう。"
)


class TestTranscriptText:
    def test_flattens_db_transcript_object(self):
        obj = {"transcript": [{"speaker": "M", "dialogue": "こんにちは。"}, {"dialogue": "続き。"}]}
        assert transcript_text(obj) == "こんにちは。\n続き。"

    def test_passthrough_and_empty(self):
        assert transcript_text("raw") == "raw"
        assert transcript_text(None) == ""


class TestStructureScore:
    def test_full_structure_scores_1(self):
        result = structure_score(GOOD_TRANSCRIPT)
        assert result["score"] == 1.0
        assert result["ordered"] is True

    def test_missing_points_lower_score(self):
        result = structure_score("この章は、悩みに答えます。以上です。")
        assert result["score"] < 0.5
        assert "point1" not in result["found"]

    def test_out_of_order_points_penalized(self):
        text = "この章は、悩みに答えます。3つ目は甲。1つ目は乙。2つ目は丙。アクションプランです。"
        result = structure_score(text)
        assert result["ordered"] is False
        assert result["score"] < 1.0


class TestGrounding:
    CONTENT = "戦略コンサルの内定率は約0.5%です。Prismというプログラムが存在します。イシューツリーで分解します。"

    def test_supported_terms_score_high(self):
        transcript = "Prismの内定率は0.5%と紹介されています。イシューツリーが鍵です。"
        result = grounding_score(self.CONTENT, transcript)
        assert result["score"] >= 0.8

    def test_fabricated_terms_are_flagged(self):
        transcript = "マッキンゼーのフレームワークではシナジーが年間200億円になります。"
        result = grounding_score(self.CONTENT, transcript)
        assert result["score"] < 0.5
        assert any("マッキンゼー" in t for t in result["unsupported"])
        assert any("200億" in t for t in result["unsupported"])

    def test_speech_stopwords_do_not_count_as_facts(self):
        terms = extract_fact_terms("皆さん、今日はアクションプランを説明します。")
        assert "皆さん" not in terms
        assert "アクションプラン" not in terms

    def test_no_fact_terms_scores_perfect(self):
        assert grounding_score("本文", "ええ、そうですね。")["score"] == 1.0


class TestPoliteness:
    def test_consistent_desu_masu(self):
        assert politeness_score("結論から話します。理由は3つあります。負担が減ります。") == 1.0

    def test_mixed_register_scores_lower(self):
        score = politeness_score("結論から話します。理由は3つだ。負担が減るぞ。")
        assert score < 0.5


class TestComposite:
    def test_evaluate_chapter_end_to_end(self):
        content = "報告の悩み。結論から話す。根拠を3点に絞る。相手の関心から逆算する。会議で実践。" * 3
        e = evaluate_chapter("ch0", content, GOOD_TRANSCRIPT)
        assert isinstance(e, ChapterEval)
        assert e.structure == 1.0
        assert 0.0 <= e.composite <= 1.0
        assert e.composite > 0.7, f"good transcript scores high: {e}"

    def test_thin_content_inflation_is_penalized_via_length_ratio(self):
        thin = "第7章"
        e = evaluate_chapter("ch7", thin, GOOD_TRANSCRIPT)
        assert e.length_ratio > 8.0
        # Composite still bounded and lower than the well-grounded case.
        assert e.composite < 0.9
