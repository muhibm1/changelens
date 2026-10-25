# OpenAI-compatible provider: live check

The `openai` provider speaks the OpenAI chat-completions API with `response_format: json_schema`, so it works with any endpoint that does (OpenAI, a local server such as Ollama or vLLM, or Anthropic's OpenAI-compatible endpoint).

Before this change, strict endpoints rejected Fathom's schemas: strict mode needs every object closed and every property listed as required. `strict_schema()` now rewrites each schema into that form, making optional properties nullable, so the meaning is unchanged.

Run: the review eval (61 cases, one pass, whole-file context and second reading on) with `--provider openai`, `base_url: https://api.anthropic.com/v1`, model `claude-haiku-5-5`. Full table: `review-haiku-5-5-openai-compat-x1.md`.

| metric | openai-compatible | native anthropic (`review-haiku-5-5-verify-x1`) |
| --- | --- | --- |
| recall | 1.0 | 0.976 |
| precision | 0.955 | 0.932 |
| false alarms at warn/block | 2 | 3 |
| correct changes flagged | 2/19 | 2/19 |
| provider errors | 0 | 0 |

The two paths give the same quality within one-run noise, with no schema or parse errors, so the provider interface holds for a second wire protocol.
