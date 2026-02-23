Never put API keys, tokens, or secrets in tracked files. `.octopoid/config.yaml` is tracked by git — do NOT add `api_key` there.

The API key is stored in `.octopoid/.api_key` (gitignored). The SDK reads it automatically. To persist a key:
- Use `save_api_key()` from `orchestrator.config` — writes to `.octopoid/.api_key`
- Or set `OCTOPOID_API_KEY` env var (overrides the file when using `OCTOPOID_SERVER_URL`)
- The scheduler auto-captures the key on first registration — no manual step needed
