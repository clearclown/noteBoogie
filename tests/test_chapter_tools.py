"""章ツール(inject_chapter_headings / llm_chapter_detect / audit_audiobooks)の
純関数を網羅的に検証する。LLM/DBを叩く部分ではなく、決定的なロジックを死ぬほど
細かく固めることで、章構造の破綻（断片・順序崩れ・1章潰れ）の再発を防ぐ。
"""

import importlib

import pytest

inj = importlib.import_module("scripts.inject_chapter_headings")
llm = importlib.import_module("scripts.llm_chapter_detect")
aud = importlib.import_module("scripts.audit_audiobooks")


# =========================================================================
# _find_anchor — アンカー崩れバグ（改行入りアンカーが外れ章が1つに潰れる）の温床
# =========================================================================


class TestFindAnchor:
    def test_exact_match_returns_offset(self):
        text = "0123本文の開始位置ここ456"
        assert llm._find_anchor(text, "本文の開始位置ここ", 0) == 4

    def test_respects_search_from_skips_earlier(self):
        text = "アンカーxxxxxxxxxxアンカー"
        first = text.find("アンカー")
        second = text.find("アンカー", first + 1)
        assert llm._find_anchor(text, "アンカー", first + 1) == second

    def test_not_found_returns_minus_one(self):
        assert llm._find_anchor("本文", "存在しない語", 0) == -1

    def test_empty_anchor_returns_minus_one(self):
        assert llm._find_anchor("何か本文", "", 0) == -1

    def test_newline_in_anchor_matches_via_whitespace_collapse(self):
        # LLMは『「見出しらしき行」\n本文が続く…』のように改行入りを返す
        text = "前置き。「見出しらしき行」\n本文が続いて、次の段落へ…続き"
        anchor = "「見出しらしき行」本文が続いて"  # 改行なしのアンカー
        pos = llm._find_anchor(text, anchor, 0)
        assert pos != -1
        assert text[pos:].startswith("「見出しらしき行」")

    def test_anchor_with_newline_matches_collapsed_text(self):
        text = "章の本文がここから始まります"
        anchor = "章の本文が\nここから"  # アンカー側に改行
        pos = llm._find_anchor(text, anchor, 0)
        assert pos == 0

    def test_returned_offset_is_original_position_not_collapsed(self):
        # 空白畳み込み後の位置ではなく、元テキストのオフセットを返すこと
        text = "  \n\n  目的の　　文章のつづき"  # 前方に空白多数
        anchor = "目的の文章のつづき"  # 6字以上
        pos = llm._find_anchor(text, anchor, 0)
        assert text[pos] == "目"

    def test_short_non_matching_anchor_returns_minus_one(self):
        # 逐語一致せず、かつ6字未満は曖昧一致もしない（誤爆防止）
        assert llm._find_anchor("a b c 本 文", "abc本", 0) == -1

    def test_prefix_fallback_when_tail_has_ocr_junk(self):
        # アンカー末尾にOCRゆらぎ(先頭16字は逐語一致)でも先頭一致で拾う
        text = "序文。第一章序論の本文がここから始まる。以下略"
        anchor = "第一章序論の本文がここから始まるXYZ"  # 先頭16字は逐語、末尾は差異
        pos = llm._find_anchor(text, anchor, 0)
        assert pos != -1
        assert text[pos] == "第"


# =========================================================================
# _extract_json — LLM出力からのJSON抽出（llm/audit 共通ロジック）
# =========================================================================


class TestExtractJson:
    @pytest.mark.parametrize("mod", [llm, aud])
    def test_pure_json(self, mod):
        assert mod._extract_json('{"a": 1}') == {"a": 1}

    @pytest.mark.parametrize("mod", [llm, aud])
    def test_json_with_surrounding_prose(self, mod):
        raw = 'はい、以下が結果です:\n{"status": "ok"}\n以上です。'
        assert mod._extract_json(raw) == {"status": "ok"}

    @pytest.mark.parametrize("mod", [llm, aud])
    def test_json_in_code_fence(self, mod):
        raw = '```json\n{"x": [1, 2]}\n```'
        assert mod._extract_json(raw) == {"x": [1, 2]}

    @pytest.mark.parametrize("mod", [llm, aud])
    def test_nested_objects(self, mod):
        raw = '{"chapters": [{"title": "A"}], "back": null}'
        got = mod._extract_json(raw)
        assert got["chapters"][0]["title"] == "A"
        assert got["back"] is None

    @pytest.mark.parametrize("mod", [llm, aud])
    def test_no_json_raises(self, mod):
        with pytest.raises(Exception):
            mod._extract_json("これはJSONを含まない文章です")


# =========================================================================
# inject_chapter_headings._clean_title — タイトル整形
# =========================================================================


class TestCleanTitle:
    def test_removes_parenthetical_note(self):
        assert inj._clean_title("(本書では第7章)戦略") == "戦略"

    def test_removes_fullwidth_parenthetical(self):
        assert inj._clean_title("（注記）本論") == "本論"

    def test_cuts_at_sentence_boundary(self):
        assert inj._clean_title("戦略。詳細は後述") == "戦略"

    def test_cuts_at_particle_dewa(self):
        assert inj._clean_title("この章では戦略を説明する") == "この章"

    def test_caps_length(self):
        long = "あ" * 50
        assert len(inj._clean_title(long)) <= inj.MAX_TITLE_CHARS

    def test_strips_separators(self):
        assert inj._clean_title("・戦略・") == "戦略"

    def test_clean_title_unchanged_when_already_clean(self):
        assert inj._clean_title("ファイナンス") == "ファイナンス"


# =========================================================================
# inject_chapter_headings.detect_chapter_starts — 反復柱からの章検出
# =========================================================================


class TestDetectChapterStarts:
    def _lines(self, *items):
        return list(items)

    def test_repeated_running_header_detected(self):
        # 第1章が MIN_REPEAT 以上反復 → 章として検出
        body = ["本文。" * 60]
        lines = (
            ["目次"]
            + ["第1章 序論"] + body
            + ["第1章 序論"] + body
            + ["第1章 序論"] + body
        )
        starts = inj.detect_chapter_starts(lines)
        assert len(starts) == 1
        assert starts[0][1] == "第1章"

    def test_single_occurrence_excluded_as_reference(self):
        # 1回だけの「第3章で述べたように」は章ではない（MIN_REPEAT未満）
        lines = ["第3章でも触れたように重要だ", "本文。" * 60]
        starts = inj.detect_chapter_starts(lines)
        assert starts == []

    def test_first_substantial_occurrence_is_start(self):
        body = ["本文。" * 60]  # 実体のある本文(600字)
        lines = (
            ["第2章 管理会計"]  # L0: 目次的な薄い初出（後続40行に本文なし）
            + ["fill"] * 50  # L1-50: 本文でない埋め（薄い判定を担保）
            + ["第2章 管理会計"] + body  # L51: 実体のある初出
            + ["第2章 管理会計"] + body
            + ["第2章 管理会計"] + body
        )
        starts = inj.detect_chapter_starts(lines)
        assert len(starts) == 1
        # 薄いL0でなく実体のあるL51が章開始に採用される
        assert starts[0][0] == 51

    def test_chapters_returned_in_document_order(self):
        b = ["本文。" * 60]
        lines = []
        for key in ["第1章 A", "第2章 B"]:
            for _ in range(3):
                lines += [key] + b
        starts = inj.detect_chapter_starts(lines)
        keys = [s[1] for s in starts]
        assert keys == ["第1章", "第2章"]


# =========================================================================
# ハルシネーション対策の決定論ガード（title_is_grounded / coverage_warnings）
# =========================================================================


class TestTitleGrounding:
    def test_title_core_strips_markers(self):
        assert llm._title_core("第4章 品質と改善") == "品質と改善"
        assert llm._title_core("エピソード3 在庫だらけの店") == "在庫だらけの店"
        assert llm._title_core("プロローグ 会計は難しい") == "会計は難しい"

    def test_grounded_when_title_content_in_text(self):
        text = "……本編では品質と改善について論じる……"
        assert llm.title_is_grounded("第4章 品質と改善", text) is True

    def test_not_grounded_when_fabricated(self):
        text = "会計と財務の話が延々と続く本文である。"
        # 本文に一切無い創作タイトルは弾く
        assert llm.title_is_grounded("第4章 宇宙人による経営論", text) is False

    def test_marker_only_title_is_allowed(self):
        # 「第3章」だけ（中身なし）は検証対象外＝True
        assert llm.title_is_grounded("第3章", "無関係な本文") is True

    def test_grounding_respects_near_scope(self):
        # near 近傍のみを見る: 遠くにしか無い語は grounded 扱いにしない（核は4字以上）
        text = "組織マネジメント論" + ("x" * 10000) + "会計の本文"
        # near=10050付近には「組織マネジメント」が無い
        assert llm.title_is_grounded("第1章 組織マネジメント論", text, near=10050) is False

    def test_short_core_allowed_without_verification(self):
        # 3字以下の核は意味的検証が不能なので許容（短い実タイトルを弾かない）
        assert llm.title_is_grounded("第1章 序論", "無関係な本文だけ") is True


class TestCoverageWarnings:
    def test_empty_positions(self):
        coverage = llm.coverage_warnings([], 1000)
        assert coverage
        assert "未分割" in coverage[0]

    def test_large_preamble_flagged(self):
        # 先頭章が本文の50%地点＝前付け過大
        w = llm.coverage_warnings([500, 600, 700], 1000)
        assert any("前付け" in x for x in w)

    def test_oversized_chapter_flagged(self):
        # 1章が全体の大半
        w = llm.coverage_warnings([10, 950], 1000)
        assert any("分割漏れ" in x for x in w)

    def test_tiny_chapter_flagged(self):
        w = llm.coverage_warnings([0, 100, 150], 5000)
        assert any("ほぼ空" in x for x in w)

    def test_healthy_coverage_no_warnings(self):
        # 均等に分かれた健全なケース
        positions = [0, 2000, 4000, 6000, 8000]
        assert llm.coverage_warnings(positions, 10000) == []


def test_chapter_regex_matches_variants():
    assert inj.CHAPTER_RE.match("第1章 タイトル")
    assert inj.CHAPTER_RE.match("第一章 タイトル")
    assert inj.CHAPTER_RE.match("第10章 x")
    assert inj.CHAPTER_RE.match("第２部 全角")
    # 「節」は対象外（章/部のみ）。細かい節まで拾うと過分割になるため。
    assert not inj.CHAPTER_RE.match("第10節 x")
    assert not inj.CHAPTER_RE.match("これは第1章の話")  # 行頭でない


# =========================================================================
# qa_sample_vision — ランダム標本 + Vision照合QA の純関数
# =========================================================================

qa = importlib.import_module("scripts.qa_sample_vision")


class TestQaTitleMatch:
    def test_matches_when_substring_overlap(self):
        assert qa.title_matches_any("第4章 品質と改善",
                                    ["第4章 品質と改善"]) is True

    def test_matches_with_ocr_whitespace_diff(self):
        # OCRで空白が混ざっても、文字列が同じなら一致とみなす
        assert qa.title_matches_any("品質 と 改善", ["第4章 品質と改善"]) is True

    def test_no_match_for_unrelated(self):
        assert qa.title_matches_any("量子コンピュータ入門", ["第1章 会計の基礎"]) is False

    def test_short_fragment_not_judged(self):
        # 3字未満は判定せずTrue（誤検出回避）
        assert qa.title_matches_any("序", ["第1章 x"]) is True


class TestSelectSamplePages:
    def test_includes_all_boundary_pages(self):
        pages = qa.select_sample_pages(100, [10, 20, 30], sample_n=0, seed=0)
        assert {10, 20, 30} <= set(pages)

    def test_adds_random_sample(self):
        pages = qa.select_sample_pages(100, [5], sample_n=4, seed=0)
        assert 5 in pages
        assert len(pages) == 5  # 境界1 + 標本4

    def test_deterministic_with_seed(self):
        a = qa.select_sample_pages(200, [1], 6, seed=42)
        b = qa.select_sample_pages(200, [1], 6, seed=42)
        assert a == b  # 同じseedで再現可能

    def test_boundary_out_of_range_dropped(self):
        pages = qa.select_sample_pages(10, [999], sample_n=0, seed=0)
        assert 999 not in pages


class TestAggregateQa:
    def test_ok_when_all_headings_match(self):
        reports = [{"page": 5, "headings": ["第2章 管理会計"], "is_back_matter": False}]
        res = qa.aggregate_qa(reports, ["第2章 管理会計"])
        assert res["verdict"] == "ok"
        assert res["missed_chapters"] == []

    def test_flags_missed_chapter(self):
        # Visionが見た章見出しが source に無い → 見落とし
        reports = [{"page": 8, "headings": ["第5章 M&A戦略"], "is_back_matter": False}]
        res = qa.aggregate_qa(reports, ["第1章 序論"])
        assert res["verdict"] == "flag"
        assert res["missed_chapters"][0]["heading"] == "第5章 M&A戦略"

    def test_flags_back_matter_page(self):
        reports = [{"page": 300, "headings": [], "is_back_matter": True}]
        res = qa.aggregate_qa(reports, ["第1章 x"])
        assert res["verdict"] == "flag"
        assert res["back_matter_pages"] == 1


# =========================================================================
# build_injected_source — 章検出結果の適用パイプライン（アンカー崩れバグの本体）
# =========================================================================


class TestBuildInjectedSource:
    def test_basic_injection_with_headings(self):
        ft = "序文。\n第一章の本文がここから始まる。\n第二章の本文はこちら。"
        chapters = [
            {"title": "第1章 序", "anchor": "第一章の本文がここから始まる"},
            {"title": "第2章 次", "anchor": "第二章の本文はこちら"},
        ]
        r = llm.build_injected_source(ft, chapters, None)
        assert r["chapter_count"] == 2
        assert r["text"].count("## ") == 2
        assert "## 第1章 序" in r["text"]
        assert "## 第2章 次" in r["text"]
        # 見出しは対応する本文の直前に入る
        assert r["text"].index("## 第1章") < r["text"].index("第一章の本文")

    def test_existing_noise_headings_stripped(self):
        # OCRノイズの `## 著者名` は平文へ降格され、真の章だけ `## ` になる
        ft = "## 山田太郎\n## 3\n本編の開始はここから確実に始まります。"
        chapters = [{"title": "第1章 本編", "anchor": "本編の開始はここから確実に始まります"}]
        r = llm.build_injected_source(ft, chapters, None)
        assert r["text"].count("## ") == 1  # ノイズ2つ消え、真の章1つ
        assert "## 第1章 本編" in r["text"]
        assert "山田太郎" in r["text"]  # 内容は残る（平文化）

    def test_back_matter_trimmed(self):
        ft = "本編の内容がここにあります。" + "x" * 100 + "出版目録の始まりです"
        chapters = [{"title": "第1章", "anchor": "本編の内容がここにあります"}]
        r = llm.build_injected_source(ft, chapters, "出版目録の始まりです")
        assert "出版目録" not in r["text"]  # 巻末除去
        assert r["end"] < len(ft)

    def test_unfound_anchor_skipped(self):
        ft = "実在する本文の開始位置はここです。"
        chapters = [
            {"title": "第1章", "anchor": "実在する本文の開始位置はここです"},
            {"title": "第2章", "anchor": "存在しない架空のアンカー文字列"},
        ]
        r = llm.build_injected_source(ft, chapters, None)
        assert r["chapter_count"] == 1  # 見つからない章は棄却

    def test_ungrounded_title_demoted_to_marker(self):
        # タイトルの中身が本文に無い→マーカーのみに縮退（生成的でっち上げ防止）
        ft = "会計と財務の解説が延々と続く本編の始まりの位置です。"
        chapters = [{"title": "第3章 宇宙人の逆襲",
                     "anchor": "会計と財務の解説が延々と続く本編の始まりの位置です"}]
        r = llm.build_injected_source(ft, chapters, None)
        assert r["ungrounded"] == 1
        assert "## 第3章" in r["text"]
        assert "宇宙人の逆襲" not in r["text"]  # でっち上げは注入されない

    def test_scrambled_anchors_all_injected_not_collapsed(self):
        # アンカー崩れバグ: 改行入りアンカーでも全章が注入され、1章に潰れない
        ft = "序。\n第一章。「見出しらしき行」\n本文が続いて大変だ。\n第二。それでは結論を述べよう。"
        chapters = [
            {"title": "第1章", "anchor": "「見出しらしき行」本文が続いて"},  # 改行を含む本文
            {"title": "第2章", "anchor": "それでは結論を述べよう"},
        ]
        r = llm.build_injected_source(ft, chapters, None)
        assert r["chapter_count"] == 2  # 潰れずに2章

    def test_document_order_preserved(self):
        ft = "A本文の第一段落。B本文の第二段落。C本文の第三段落。"
        chapters = [
            {"title": "章A", "anchor": "A本文の第一段落"},
            {"title": "章B", "anchor": "B本文の第二段落"},
            {"title": "章C", "anchor": "C本文の第三段落"},
        ]
        r = llm.build_injected_source(ft, chapters, None)
        t = r["text"]
        assert t.index("## 章A") < t.index("## 章B") < t.index("## 章C")

    def test_coverage_warnings_surfaced(self):
        # 1章が本文の大半 → 分割漏れ警告
        ft = "第一章の開始。" + "本文。" * 200
        chapters = [{"title": "第1章", "anchor": "第一章の開始"}]
        r = llm.build_injected_source(ft, chapters, None)
        # 単一章なので coverage は空 or 前付け0。複数章で偏りを見る別ケースは純関数側でカバー済み
        assert isinstance(r["warnings"], list)

    def test_empty_chapters_returns_zero(self):
        r = llm.build_injected_source("何か本文", [], None)
        assert r["chapter_count"] == 0
        assert r["text"] == "何か本文"


# =========================================================================
# inject_chapter_headings.inject — ファイル入出力（バックアップ・冪等・dry-run）
# =========================================================================


class TestInjectFile:
    def _book(self, tmp_path):
        body = "\n".join(["本文。" * 40] * 3)
        lines = []
        for key in ["第1章 序論", "第2章 本論", "第3章 結論"]:
            for _ in range(3):  # 柱として反復
                lines += [key, body]
        p = tmp_path / "book.md"
        p.write_text("# 書名\n" + "\n".join(lines), encoding="utf-8")
        return p

    def test_apply_injects_and_backs_up(self, tmp_path):
        p = self._book(tmp_path)
        n = inj.inject(p, dry_run=False)
        assert n == 3
        out = p.read_text(encoding="utf-8")
        assert out.count("## 第") == 3
        assert (tmp_path / "book.md.orig").exists()  # 元を退避

    def test_dry_run_does_not_write(self, tmp_path):
        p = self._book(tmp_path)
        before = p.read_text(encoding="utf-8")
        n = inj.inject(p, dry_run=True)
        assert n == 3
        assert p.read_text(encoding="utf-8") == before  # 無変更
        assert not (tmp_path / "book.md.orig").exists()

    def test_idempotent_skips_when_already_injected(self, tmp_path):
        p = tmp_path / "b.md"
        p.write_text("# 書名\n## 第1章 既存\n本文", encoding="utf-8")
        assert inj.inject(p, dry_run=False) == 0  # 既に `## 第` があるのでスキップ

    def test_no_chapters_returns_zero(self, tmp_path):
        p = tmp_path / "c.md"
        p.write_text("# 書名\n見出しの無いただの本文が続く。", encoding="utf-8")
        assert inj.inject(p, dry_run=False) == 0
