# Langfuse Observability Plugin

This plugin ships bundled with Vermes but is **opt-in** — it only loads when
you explicitly enable it.

## Enable

```bash
pip install langfuse
vermes plugins enable observability/langfuse
```

Or check the box in the interactive `vermes plugins` UI.

## Required credentials

Set these in `~/.vermes/.env`:

```bash
VERMES_LANGFUSE_PUBLIC_KEY=pk-lf-...
VERMES_LANGFUSE_SECRET_KEY=sk-lf-...
VERMES_LANGFUSE_BASE_URL=https://cloud.langfuse.com   # or your self-hosted URL
```

Without the SDK or credentials the hooks no-op silently — the plugin fails
open.

## Verify

```bash
vermes plugins list                 # observability/langfuse should show "enabled"
vermes chat -q "hello"              # then check Langfuse for a "Vermes turn" trace
```

## Optional tuning

```bash
VERMES_LANGFUSE_ENV=production       # environment tag
VERMES_LANGFUSE_RELEASE=v1.0.0       # release tag
VERMES_LANGFUSE_SAMPLE_RATE=0.5      # sample 50% of traces
VERMES_LANGFUSE_MAX_CHARS=12000      # max chars per field (default: 12000)
VERMES_LANGFUSE_DEBUG=true           # verbose plugin logging
```

## Disable

```bash
vermes plugins disable observability/langfuse
```
