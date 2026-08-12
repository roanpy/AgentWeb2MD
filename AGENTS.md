# AgentWeb2MD Agent Contract

You are responsible for the extraction decisions. The Python tools are deterministic helpers, not an autonomous crawler.

## Required inputs

Before writing a site profile, establish:

- target URL or explicit URL list
- content to include
- sections to exclude
- whether images and attachments should be downloaded
- sample size and acceptable quality threshold

If these are not provided, inspect what can be verified and ask the user only for decisions that materially change scope.

## Workflow

1. Probe the site with `agentweb2md-init --auto`; do not treat detected selectors as approved.
2. Create or edit `config/<site>/common.json` and page-type JSON files.
3. Keep the first `output_root` under `/tmp/agentweb2md/<site>`.
4. Cap discovery for the first run and narrow include/exclude patterns.
5. Run `validate_config()` before extraction.
6. Extract a representative sample.
7. Run `agentweb2md-quality`, then inspect representative Markdown and reported worst files.
8. Fix configuration or generic rules and repeat the sample.
9. Ask for approval before a full crawl, non-temporary destination, or publication workflow.

## Completion gate

- configuration has no validation errors
- at least one Markdown file was scored
- no broken local images or resources
- requested specs/resources are present when applicable
- representative output has been read, not only scored
- remaining warnings and coverage gaps are stated explicitly
- extraction and quality commands exit with status 0; a non-zero status blocks approval

## Boundaries

- Do not assume a successful HTTP response means useful content.
- Do not hand-edit generated Markdown; fix the profile or extraction rule.
- Do not expand crawl scope automatically after a sample passes.
- Do not bypass authentication, access controls, site terms, or rate limits.
- Do not run an untrusted site profile without reviewing its URLs and output paths.
- Keep LLM credentials in environment variables referenced by `llm_refine.api_key_env`, never in JSON.
- Keep `rate_limit.max_response_bytes` positive; the default per-response limit is 25 MiB.
- Do not publish, promote, or overwrite an existing library without explicit approval.
