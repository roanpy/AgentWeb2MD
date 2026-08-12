# Security Policy

Security fixes are applied to the latest version on the default branch.

To report a vulnerability, contact the maintainer through the method listed on the GitHub profile and ask for a private channel without including vulnerability details in the initial message.

Do not include credentials, authenticated site content, private URLs, local paths, downloaded documents, or extraction output in a public issue. Use synthetic inputs and redact logs before sharing them.

Treat site profiles as executable input: they control network requests and local output paths. Review profiles before running them. Store optional LLM credentials in an environment variable referenced by `llm_refine.api_key_env`; never put credentials in JSON. Enabling LLM refinement sends extracted Markdown to the configured endpoint, so do not enable it for private or authenticated content unless that disclosure is approved.
