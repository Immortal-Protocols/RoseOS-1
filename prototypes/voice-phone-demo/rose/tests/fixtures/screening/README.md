# Screening fixtures (docs/APP-BUILDER.md §3.7)

JSON lines of `{id, kind, text, priorText, expected, category, source}`, replayed offline by
`rose/tests/test_app_policy.py`. No test calls a model.

- `benign.jsonl`: ordinary requests, many of them near-misses for the phrase gate. Expected `allow` overall, with
  no objection from the phrase gate. They come from the review corpus of 82830ea0 and 1809f85b.
- `capability_notes.jsonl`: requests that name a power generated apps never get. Expected no objection, with a note.
- `refused.jsonl`: the §3.6 phrase-gate refusals already in the test suite. Expected `deny`.

New harm and other-platform content belongs to whoever owns the content policy (§3.7, decisions for Nathan).
App Core adds harness and failure-mode cases, not new harmful phrasings.
