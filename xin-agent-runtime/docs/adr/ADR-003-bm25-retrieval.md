# ADR-003: BM25 over Keyword Overlap for LLM-Wiki Retrieval

**Date**: 2026-06-25
**Status**: Accepted

## Context

The initial LLM-Wiki adapter used simple keyword overlap scoring
(count of shared terms / query length). This approach has poor
ranking quality: a document mentioning a term once scores the same
as one mentioning it ten times, and common terms dominate rare terms.

## Decision

Replace keyword overlap with **BM25** (Best Matching 25):

- Standard BM25 with k1=1.5, b=0.75.
- IDF weighting down-weights common terms.
- TF saturation prevents long documents from dominating.
- Document length normalization penalizes overly long chunks.

BM25 is computed over keyword tokens + title tokens extracted during
compilation. No external dependencies required.

## Consequences

- Retrieval ranking quality improves significantly.
- Production should upgrade to embedding-based semantic search (v1
  of the retrieval roadmap) for better recall on paraphrased queries.
- The BM25 implementation is pure Python with no external deps.
