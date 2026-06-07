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
| `codewiki ping` | Verify LLM endpoint & config | ✅ |
| `codewiki generate --source <path\|url>` | Ingest, map, detect signals, index, and generate wiki | ✅ |
| `codewiki generate --source . --dry-run` | Estimate file/symbol/signal/tokens without writing pages | ✅ |
| `codewiki update --source .` | Diff-aware refresh using manifest-based change detection | ✅ |
| `codewiki chat "<question>"` | Grounded Q&A over local index + wiki context (`--file-back` supported) | ✅ |
| `codewiki lint` | Wiki health report: broken links, missing citations, orphans | ✅ |
| `codewiki serve --port 8080` | Local FastAPI markdown + Mermaid viewer | ✅ |

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
| 0 | Scaffold + Config + LLM Client | ✅ Baseline complete |
| 1 | Ingest & Repo Map | ✅ Baseline complete |
| 2 | Wiki Generation v1 | ✅ Baseline complete |
| 3 | Business Lens & Diagrams | ✅ Baseline complete |
| 4 | Index, Retrieval & Chat | ✅ Baseline complete |
| 5 | Incremental Update & Lint | ✅ Baseline complete |
| 6 | Viewer & Export | ✅ Baseline complete |
| 7 | Scale & Hardening | ⏳ Next iteration |

See [`agents/implementor_agent.md`](agents/implementor_agent.md) for implementation status and decisions.

---

## Docs

- [`docs/PRD.md`](docs/PRD.md) — Product requirements
- [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) — Technical architecture & milestones
