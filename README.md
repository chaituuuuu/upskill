# CodeWiki

> **Business-oriented knowledge base generator for source code.**  
> Point it at a large codebase and it produces — and keeps current — a structured, interlinked wiki that explains *what the system does for the business*, not just how the code works.

---

## Quick Start

```bash
# 1. Install
pip install -e ".[dev]"

# 2. Configure (copy sample and fill in your LLM endpoint)
cp codewiki.yaml codewiki.local.yaml   # or edit codewiki.yaml directly
cp .env.example .env                   # set OPENAI_API_KEY (or leave blank for local models)

# 3. Verify the endpoint
codewiki ping

# 4. Generate wiki for a repo
codewiki generate --source /path/to/your/repo

# (or estimate cost first)
codewiki generate --source /path/to/your/repo --dry-run
```

---

## CLI Reference

| Command | Description | Phase |
|---------|-------------|-------|
| `codewiki ping` | Verify LLM endpoint & config | 0 ✅ |
| `codewiki generate --source <path\|url>` | Full wiki build | 1–3 |
| `codewiki generate --source . --dry-run` | Estimate cost only | 1–3 |
| `codewiki update --source .` | Diff-aware incremental refresh | 5 |
| `codewiki chat "<question>"` | Grounded Q&A (+ file-back) | 4 |
| `codewiki lint` | Health report: stale/orphan/citations | 5 |
| `codewiki serve --port 8080` | Local web viewer | 6 |

---

## Configuration

Precedence: `codewiki.yaml` < `.env` < env vars < CLI flags.

See [`codewiki.yaml`](codewiki.yaml) for the annotated sample and [`.env.example`](.env.example) for env var names.

### Works with any OpenAI-compatible endpoint

| Provider | `base_url` |
|----------|-----------|
| OpenAI | `https://api.openai.com/v1` (default) |
| Azure OpenAI | `https://<resource>.openai.azure.com/openai/deployments/<deployment>` |
| Ollama (local) | `http://localhost:11434/v1` |
| vLLM | `http://localhost:8000/v1` |
| LM Studio | `http://localhost:1234/v1` |
| OpenRouter | `https://openrouter.ai/api/v1` |

---

## Development

```bash
pip install -e ".[dev]"
ruff check .      # lint
```

---

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 0 | Scaffold + Config + LLM Client | ✅ Done |
| 1 | Ingest & Repo Map | 🔜 |
| 2 | Wiki Generation v1 | 🔜 |
| 3 | Business Lens & Diagrams | 🔜 |
| 4 | Index, Retrieval & Chat | 🔜 |
| 5 | Incremental Update & Lint | 🔜 |
| 6 | Viewer & Export | 🔜 |
| 7 | Scale & Hardening | 🔜 |

See [`agents/implementor_agent.md`](agents/implementor_agent.md) for implementation status and decisions.

---

## Docs

- [`docs/PRD.md`](docs/PRD.md) — Product requirements
- [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) — Technical architecture & milestones
