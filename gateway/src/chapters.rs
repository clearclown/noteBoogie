//! Markdown chapter splitting for Book Navigator audiobooks.
//!
//! Splits a Markdown document into chapters on `#`/`##` headings, keeping each
//! chapter's body whole (no token-based secondary chunking — that would fragment
//! a chapter). If the source has no `#`/`##` heading, the whole text becomes a
//! single chapter titled after the provided fallback (the source title).

use pulldown_cmark::{Event, HeadingLevel, Parser, Tag, TagEnd};

/// One parsed chapter: a title and its body text (including the heading line).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Chapter {
    pub title: String,
    pub body: String,
}

/// Split Markdown into chapters on H1/H2 headings.
///
/// - Text before the first heading is attached to the first chapter (or becomes
///   the sole chapter if there are no headings).
/// - `fallback_title` is used when the document has no H1/H2 heading.
pub fn split_into_chapters(markdown: &str, fallback_title: &str) -> Vec<Chapter> {
    // Byte offsets where each chapter (H1/H2 heading) starts.
    let mut boundaries: Vec<(usize, String)> = Vec::new();
    let parser = Parser::new(markdown).into_offset_iter();

    let mut capturing_title: Option<usize> = None; // start offset of the heading being read
    let mut current_title = String::new();

    for (event, range) in parser {
        match event {
            Event::Start(Tag::Heading { level, .. })
                if level == HeadingLevel::H1 || level == HeadingLevel::H2 =>
            {
                capturing_title = Some(range.start);
                current_title.clear();
            }
            Event::Text(text) | Event::Code(text) if capturing_title.is_some() => {
                current_title.push_str(&text);
            }
            Event::End(TagEnd::Heading(level))
                if (level == HeadingLevel::H1 || level == HeadingLevel::H2)
                    && capturing_title.is_some() =>
            {
                let start = capturing_title.take().unwrap();
                let title = current_title.trim().to_string();
                boundaries.push((start, title));
            }
            _ => {}
        }
    }

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

    chapters
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_on_h1_and_h2() {
        let md = "# Chapter One\nIntro text.\n\n## Chapter Two\nMore text.";
        let chapters = split_into_chapters(md, "Fallback");
        assert_eq!(chapters.len(), 2);
        assert_eq!(chapters[0].title, "Chapter One");
        assert!(chapters[0].body.contains("Intro text."));
        assert_eq!(chapters[1].title, "Chapter Two");
        assert!(chapters[1].body.contains("More text."));
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
    fn preamble_before_first_heading_becomes_a_chapter() {
        let md = "Foreword paragraph.\n\n# Real Chapter\nBody.";
        let chapters = split_into_chapters(md, "Front Matter");
        assert_eq!(chapters.len(), 2);
        assert_eq!(chapters[0].title, "Front Matter");
        assert!(chapters[0].body.contains("Foreword"));
        assert_eq!(chapters[1].title, "Real Chapter");
    }

    #[test]
    fn deeper_headings_do_not_split() {
        // ### should NOT start a new chapter; it stays inside Chapter A's body.
        let md = "# Chapter A\nText.\n\n### Subsection\nNested text.";
        let chapters = split_into_chapters(md, "Fallback");
        assert_eq!(chapters.len(), 1);
        assert_eq!(chapters[0].title, "Chapter A");
        assert!(chapters[0].body.contains("Subsection"));
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
        let md = "# Ch A\r\nbody a\r\n\r\n# Ch B\r\nbody b";
        let chapters = split_into_chapters(md, "Fallback");
        assert_eq!(chapters.len(), 2);
        assert_eq!(chapters[0].title, "Ch A");
        assert_eq!(chapters[1].title, "Ch B");
    }

    #[test]
    fn heading_title_strips_inline_markup() {
        let md = "# **Bold** and `code` title\nbody";
        let chapters = split_into_chapters(md, "Fallback");
        assert_eq!(chapters.len(), 1);
        // Inline emphasis/code markers are not part of the extracted title text.
        assert_eq!(chapters[0].title, "Bold and code title");
    }

    #[test]
    fn multiple_h2_each_become_chapters() {
        let md = "## One\na\n\n## Two\nb\n\n## Three\nc";
        let chapters = split_into_chapters(md, "Fallback");
        assert_eq!(chapters.len(), 3);
        assert_eq!(
            chapters.iter().map(|c| c.title.as_str()).collect::<Vec<_>>(),
            vec!["One", "Two", "Three"]
        );
    }

    #[test]
    fn japanese_headings_split_and_keep_body() {
        let md = "# 第一章 はじめに\n内容A。\n\n# 第二章 戦略\n内容B。";
        let chapters = split_into_chapters(md, "本");
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
