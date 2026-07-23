//! Markdown chapter splitting for Book Navigator audiobooks.
//!
//! Splits a Markdown document into chapters, keeping each chapter's body whole
//! (no token-based secondary chunking — that would fragment a chapter).
//!
//! Guardrails for OCR'd books (SuperBook output):
//! - When the document has 2+ H1 headings, split on H1 only — H2s are section
//!   headings within a chapter. Otherwise fall back to H1+H2.
//! - Consecutive chapters with the same title are merged (running headers make
//!   OCR re-emit the chapter title on every page).
//! - Chapters whose body is under [`MIN_CHAPTER_BODY_CHARS`] are merged into
//!   the next chapter (TOC pages and front-matter fragments). Without this the
//!   LLM receives a near-empty chapter and fabricates a plausible monologue.
//! - If the source has no heading at all, the whole text becomes a single
//!   chapter titled after the provided fallback (the source title).

use pulldown_cmark::{Event, HeadingLevel, Parser, Tag, TagEnd};

/// Minimum body size (chars, heading line excluded) for a standalone chapter.
const MIN_CHAPTER_BODY_CHARS: usize = 200;

/// One parsed chapter: a title and its body text (including the heading line).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Chapter {
    pub title: String,
    pub body: String,
}

/// Collect (byte offset, title, level) of every H1/H2 heading.
fn collect_headings(markdown: &str) -> Vec<(usize, String, HeadingLevel)> {
    let mut headings: Vec<(usize, String, HeadingLevel)> = Vec::new();
    let parser = Parser::new(markdown).into_offset_iter();

    let mut capturing: Option<(usize, HeadingLevel)> = None;
    let mut current_title = String::new();

    for (event, range) in parser {
        match event {
            Event::Start(Tag::Heading { level, .. })
                if level == HeadingLevel::H1 || level == HeadingLevel::H2 =>
            {
                capturing = Some((range.start, level));
                current_title.clear();
            }
            Event::Text(text) | Event::Code(text) if capturing.is_some() => {
                current_title.push_str(&text);
            }
            Event::End(TagEnd::Heading(level))
                if (level == HeadingLevel::H1 || level == HeadingLevel::H2)
                    && capturing.is_some() =>
            {
                let (start, _) = capturing.take().unwrap();
                headings.push((start, current_title.trim().to_string(), level));
            }
            _ => {}
        }
    }
    headings
}

/// Chars in the body once the leading heading line is excluded.
fn body_content_chars(body: &str) -> usize {
    let rest = match body.strip_prefix('#') {
        Some(_) => body.split_once('\n').map(|(_, r)| r).unwrap_or(""),
        None => body,
    };
    rest.chars().filter(|c| !c.is_whitespace()).count()
}

/// Split Markdown into chapters (see module docs for the guardrails).
///
/// - Text before the first heading becomes its own chapter when non-trivial.
/// - `fallback_title` is used when the document has no H1/H2 heading.
pub fn split_into_chapters(markdown: &str, fallback_title: &str) -> Vec<Chapter> {
    let all = collect_headings(markdown);

    // With 2+ H1s the H1s are the chapters; H2s are sections inside them.
    let h1_count = all
        .iter()
        .filter(|(_, _, l)| *l == HeadingLevel::H1)
        .count();
    let boundaries: Vec<(usize, String)> = all
        .into_iter()
        .filter(|(_, _, l)| h1_count < 2 || *l == HeadingLevel::H1)
        .map(|(s, t, _)| (s, t))
        .collect();

    // No H1/H2 headings -> single chapter with the whole text.
    if boundaries.is_empty() {
        let body = markdown.trim().to_string();
        return vec![Chapter {
            title: fallback_title.trim().to_string(),
            body,
        }];
    }

    let mut chapters: Vec<Chapter> = Vec::with_capacity(boundaries.len());

    // Preamble before the first heading, if any non-empty, becomes its own chapter.
    let first_start = boundaries[0].0;
    let preamble = markdown[..first_start].trim();
    if !preamble.is_empty() {
        chapters.push(Chapter {
            title: fallback_title.trim().to_string(),
            body: preamble.to_string(),
        });
    }

    for i in 0..boundaries.len() {
        let start = boundaries[i].0;
        let end = boundaries
            .get(i + 1)
            .map(|(s, _)| *s)
            .unwrap_or(markdown.len());
        let body = markdown[start..end].trim().to_string();
        let title = if boundaries[i].1.is_empty() {
            fallback_title.trim().to_string()
        } else {
            boundaries[i].1.clone()
        };
        chapters.push(Chapter { title, body });
    }

    // Running headers (柱) re-emit chapter AND part titles on alternating
    // pages, so consecutive-dedupe alone still splits a book into hundreds of
    // fragments. Rule: a heading occurrence starts a chapter only if it is the
    // FIRST SUBSTANTIAL occurrence of that title (TOC lines are tiny and thus
    // never anchor). Every other occurrence — repeats and tiny firsts — is a
    // continuation of whatever chapter is currently open, preserving document
    // order.
    let mut first_substantial: std::collections::HashMap<&str, usize> =
        std::collections::HashMap::new();
    for (i, ch) in chapters.iter().enumerate() {
        if body_content_chars(&ch.body) >= MIN_CHAPTER_BODY_CHARS
            && !first_substantial.contains_key(ch.title.as_str())
        {
            first_substantial.insert(ch.title.as_str(), i);
        }
    }
    let anchors: Vec<bool> = chapters
        .iter()
        .enumerate()
        .map(|(i, ch)| first_substantial.get(ch.title.as_str()) == Some(&i))
        .collect();

    let mut merged: Vec<Chapter> = Vec::with_capacity(chapters.len());
    for (ch, is_anchor) in chapters.into_iter().zip(anchors) {
        match merged.last_mut() {
            Some(prev) if !is_anchor => {
                prev.body.push_str("\n\n");
                prev.body.push_str(&ch.body);
            }
            _ => merged.push(ch),
        }
    }

    // Fold tiny chapters (TOC lines, front-matter fragments) into the NEXT
    // chapter so the LLM never receives a near-empty chapter to embellish.
    // The last chapter, if tiny, folds backwards instead.
    let mut folded: Vec<Chapter> = Vec::with_capacity(merged.len());
    let mut pending_prefix = String::new();
    let mut pending_title: Option<String> = None;
    for ch in merged {
        if body_content_chars(&ch.body) < MIN_CHAPTER_BODY_CHARS {
            pending_title.get_or_insert(ch.title);
            pending_prefix.push_str(&ch.body);
            pending_prefix.push_str("\n\n");
            continue;
        }
        pending_title = None;
        let body = if pending_prefix.is_empty() {
            ch.body
        } else {
            format!("{}{}", std::mem::take(&mut pending_prefix), ch.body)
        };
        folded.push(Chapter {
            title: ch.title,
            body,
        });
    }
    if !pending_prefix.is_empty() {
        match folded.last_mut() {
            Some(last) => {
                last.body.push_str("\n\n");
                last.body.push_str(pending_prefix.trim_end());
            }
            // Everything was tiny — better one thin chapter than none.
            None => folded.push(Chapter {
                title: pending_title
                    .filter(|t| !t.is_empty())
                    .unwrap_or_else(|| fallback_title.trim().to_string()),
                body: pending_prefix.trim_end().to_string(),
            }),
        }
    }

    folded
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A chapter-sized body (clears MIN_CHAPTER_BODY_CHARS) with a marker.
    fn body(marker: &str) -> String {
        format!("{marker} {}", "本文。".repeat(100))
    }

    #[test]
    fn splits_on_h1_and_h2_when_single_h1() {
        let md = format!(
            "# Chapter One\n{}\n\n## Chapter Two\n{}",
            body("Intro text."),
            body("More text.")
        );
        let chapters = split_into_chapters(&md, "Fallback");
        assert_eq!(chapters.len(), 2);
        assert_eq!(chapters[0].title, "Chapter One");
        assert!(chapters[0].body.contains("Intro text."));
        assert_eq!(chapters[1].title, "Chapter Two");
        assert!(chapters[1].body.contains("More text."));
    }

    #[test]
    fn multiple_h1_split_on_h1_only() {
        // With 2+ H1s the H2s are sections, not chapters.
        let md = format!(
            "# 第1章 全体像\n{}\n\n## 節タイトル\n{}\n\n# 第2章 思考\n{}",
            body("a"),
            body("b"),
            body("c")
        );
        let chapters = split_into_chapters(&md, "本");
        assert_eq!(chapters.len(), 2);
        assert_eq!(chapters[0].title, "第1章 全体像");
        assert!(chapters[0].body.contains("節タイトル"));
        assert_eq!(chapters[1].title, "第2章 思考");
    }

    #[test]
    fn no_headings_falls_back_to_single_chapter() {
        let md = "Just a paragraph of plain text with no headings at all.";
        let chapters = split_into_chapters(md, "My Book");
        assert_eq!(chapters.len(), 1);
        assert_eq!(chapters[0].title, "My Book");
        assert!(chapters[0].body.contains("plain text"));
    }

    #[test]
    fn large_preamble_becomes_its_own_chapter() {
        let md = format!("{}\n\n# Real Chapter\n{}", body("Foreword."), body("Body."));
        let chapters = split_into_chapters(&md, "Front Matter");
        assert_eq!(chapters.len(), 2);
        assert_eq!(chapters[0].title, "Front Matter");
        assert!(chapters[0].body.contains("Foreword."));
        assert_eq!(chapters[1].title, "Real Chapter");
    }

    #[test]
    fn tiny_preamble_folds_into_first_chapter() {
        let md = format!("Foreword paragraph.\n\n# Real Chapter\n{}", body("Body."));
        let chapters = split_into_chapters(&md, "Front Matter");
        assert_eq!(chapters.len(), 1);
        assert_eq!(chapters[0].title, "Real Chapter");
        assert!(chapters[0].body.contains("Foreword paragraph."));
        assert!(chapters[0].body.contains("Body."));
    }

    #[test]
    fn running_header_duplicates_merge() {
        // OCR re-emits the chapter title on every page (running headers).
        let md = format!(
            "# はじめに\n{}\n\n# はじめに\n{}\n\n# 第1章 入門\n{}",
            body("p1"),
            body("p2"),
            body("p3")
        );
        let chapters = split_into_chapters(&md, "本");
        assert_eq!(chapters.len(), 2);
        assert_eq!(chapters[0].title, "はじめに");
        assert!(chapters[0].body.contains("p1"));
        assert!(chapters[0].body.contains("p2"));
        assert_eq!(chapters[1].title, "第1章 入門");
    }

    #[test]
    fn alternating_running_headers_do_not_fragment() {
        // Real vertical books alternate PART and CHAPTER running headers on
        // odd/even pages: 第1部, 第1章, 第1部, 第1章, … Consecutive dedupe
        // alone exploded a 408-page book into 123 "chapters". Only the first
        // substantial occurrence of a title may open a chapter.
        let md = format!(
            "# 第1部 入門編\n{}\n\n# 第1章 全体像\n{}\n\n# 第1部 入門編\n{}\n\n# 第1章 全体像\n{}\n\n# 第2章 思考\n{}\n\n# 第1部 入門編\n{}",
            body("p30"),
            body("p31"),
            body("p32"),
            body("p33"),
            body("p36"),
            body("p37")
        );
        let chapters = split_into_chapters(&md, "本");
        let titles: Vec<&str> = chapters.iter().map(|c| c.title.as_str()).collect();
        assert_eq!(titles, vec!["第1部 入門編", "第1章 全体像", "第2章 思考"]);
        // Continuation pages stay in DOCUMENT order inside the open chapter.
        assert!(chapters[1].body.contains("p31"));
        assert!(chapters[1].body.contains("p32"), "柱 page folds into current");
        assert!(chapters[1].body.contains("p33"));
        assert!(chapters[2].body.contains("p36"));
        assert!(chapters[2].body.contains("p37"));
    }

    #[test]
    fn toc_line_chapters_fold_forward() {
        // A table-of-contents page emits one tiny "chapter" per listed title;
        // they must not reach the LLM as standalone near-empty chapters.
        let md = format!(
            "# 第1章 全体像\n\n# 第2章 思考\n\n# 第1章 全体像\n{}\n\n# 第2章 思考\n{}",
            body("real1"),
            body("real2")
        );
        let chapters = split_into_chapters(&md, "本");
        assert_eq!(chapters.len(), 2);
        assert_eq!(chapters[0].title, "第1章 全体像");
        assert!(chapters[0].body.contains("real1"));
        assert_eq!(chapters[1].title, "第2章 思考");
        assert!(chapters[1].body.contains("real2"));
    }

    #[test]
    fn all_tiny_collapses_to_single_titled_chapter() {
        let md = "# Chapter A\nText.\n\n### Subsection\nNested text.";
        let chapters = split_into_chapters(md, "Fallback");
        assert_eq!(chapters.len(), 1);
        assert_eq!(chapters[0].title, "Chapter A");
        assert!(chapters[0].body.contains("Nested text."));
    }

    #[test]
    fn empty_input_yields_single_fallback_chapter() {
        let chapters = split_into_chapters("   ", "Empty Book");
        assert_eq!(chapters.len(), 1);
        assert_eq!(chapters[0].title, "Empty Book");
        assert_eq!(chapters[0].body, "");
    }

    #[test]
    fn handles_crlf_line_endings() {
        let md = format!("# Ch A\r\n{}\r\n\r\n# Ch B\r\n{}", body("a"), body("b"));
        let chapters = split_into_chapters(&md, "Fallback");
        assert_eq!(chapters.len(), 2);
        assert_eq!(chapters[0].title, "Ch A");
        assert_eq!(chapters[1].title, "Ch B");
    }

    #[test]
    fn heading_title_strips_inline_markup() {
        let md = format!("# **Bold** and `code` title\n{}", body("b"));
        let chapters = split_into_chapters(&md, "Fallback");
        assert_eq!(chapters.len(), 1);
        // Inline emphasis/code markers are not part of the extracted title text.
        assert_eq!(chapters[0].title, "Bold and code title");
    }

    #[test]
    fn multiple_h2_each_become_chapters() {
        let md = format!(
            "## One\n{}\n\n## Two\n{}\n\n## Three\n{}",
            body("a"),
            body("b"),
            body("c")
        );
        let chapters = split_into_chapters(&md, "Fallback");
        assert_eq!(chapters.len(), 3);
        assert_eq!(
            chapters.iter().map(|c| c.title.as_str()).collect::<Vec<_>>(),
            vec!["One", "Two", "Three"]
        );
    }

    #[test]
    fn japanese_headings_split_and_keep_body() {
        let md = format!(
            "# 第一章 はじめに\n内容A。{}\n\n# 第二章 戦略\n内容B。{}",
            body(""),
            body("")
        );
        let chapters = split_into_chapters(&md, "本");
        assert_eq!(chapters.len(), 2);
        assert_eq!(chapters[0].title, "第一章 はじめに");
        assert!(chapters[0].body.contains("内容A。"));
        assert!(!chapters[0].body.contains("内容B。"));
        assert_eq!(chapters[1].title, "第二章 戦略");
    }

    #[test]
    fn empty_heading_uses_fallback_title() {
        let md = "#\nbody under an empty heading";
        let chapters = split_into_chapters(md, "Untitled");
        assert_eq!(chapters.len(), 1);
        assert_eq!(chapters[0].title, "Untitled");
    }
}
