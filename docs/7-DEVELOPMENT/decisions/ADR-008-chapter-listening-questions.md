# ADR-008: Listening questions use the stored chapter text

- **Status**: Accepted
- **Date**: 2026-09
- **Related**: [Book Navigator PMVV](../../book-navigator/PMVV.md)

## Context

A listener needs to pause an audiobook, ask about the current chapter, and resume
without navigating away. Library-wide retrieval can select another book or chapter.
The gateway already stores the original chapter body in `episode.content`.

## Decision

Expose `POST /api/podcasts/episodes/{episode_id}/question` through the authenticated
FastAPI service. Load that episode's original chapter text on the server and use the
configured chat model. Pass at most three previous question/answer pairs as context.
Require short source excerpts and verify their presence in the chapter before
returning an answer. Missing text or missing/invalid excerpts produce an explicit
no-evidence result. Do not silently truncate the chapter.

Keep the question panel next to the existing audio element. Opening it pauses
playback; returning to listening resumes the same element. Persist the last chapter
ID and whole-second offset per audiobook in the browser, and restore only after
the user selects Resume. Chapter IDs keep bookmarks stable when lists reorder.

## Alternatives considered

- Library-wide mentor/ask: useful for broader advice, but does not constrain evidence
  to the chapter the listener selected.
- Send a chapter label or client-provided excerpt: cannot reliably identify or
  authenticate the source content.
- New server-side playback and conversation tables: unnecessary for this first
  single-device flow; introduce them if cross-device history becomes a requirement.

## Consequences

No migration or new provider dependency is required. Playback positions stay on this
browser; questions remain only while the panel is open. Navigation still stops audio.
Excerpt matching checks provenance, not whether the answer logically follows from
the excerpt. Answer quality still requires evaluation with real chapters. Voice input,
spoken answers, and offline question answering are outside this change.
