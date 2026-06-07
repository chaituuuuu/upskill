# Implementor Agent

> **Role:** I am the **Implementor Agent** for the CodeWiki project.  
> **Model:** GitHub Copilot (GPT-5.3-Codex)  
> **Session start:** 2026-06-08  

---

## What I am

I am an autonomous coding agent responsible for implementing the **CodeWiki** project end-to-end — from scaffold through all phases described in [`docs/IMPLEMENTATION_PLAN.md`](../docs/IMPLEMENTATION_PLAN.md), guided by the requirements in [`docs/PRD.md`](../docs/PRD.md).

I write code, create files, track progress, make decisions about implementation details, and update this file as the project evolves.

---

## My Responsibilities

- Implement all phases (0 → 7) of the CodeWiki pipeline
- Follow the module layout, data contracts, and tech-stack decisions in `IMPLEMENTATION_PLAN.md`
- Make pragmatic implementation decisions within the bounds of the PRD; document them here
- Keep this file current as a living record of status, decisions, and deviations

---

## Project Summary

**CodeWiki** is a business-oriented knowledge base generator for source code.  
Point it at a large codebase → it produces and maintains a structured, interlinked Markdown wiki that explains *what the system does for the business*, not just how the code works.

**Key differentiators:**
- Business lens (capabilities, domain glossary, workflows) vs. pure technical docs
- Persistent, compounding artifact — updates pages rather than regenerating blindly
- Provider-agnostic LLM client (`base_url`/`model`/`api_key` via config)
- Every claim grounded in code with `path:Lstart-Lend` citations

---

## Phase Status

| Phase | Name | Status |
|-------|------|--------|
| 0 | Scaffold & Config + LLM Client | ✅ Baseline Complete |
| 1 | Ingest & Repo Map | ✅ Baseline Complete |
| 2 | Wiki Generation v1 | ✅ Baseline Complete |
| 3 | Business Lens & Diagrams | ✅ Baseline Complete |
| 4 | Index, Retrieval & Chat | ✅ Baseline Complete |
| 5 | Incremental Update & Lint | ✅ Baseline Complete |
| 6 | Viewer & Export | ✅ Baseline Complete |
| 7 | Scale & Hardening | ⏳ Next Iteration |

---

## Implementation Decisions & Deviations

### 2026-06-08 — Phase 0 kickoff

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Keyword search backend | **Whoosh** (pure Python) as default, **tantivy-py** as optional fast backend | Whoosh has zero native dependencies; easier setup for first-time users |
| Vector store | **FAISS** (optional, behind `USE_EMBEDDINGS=true`) | Avoids forcing a server dependency; consistent with "index-first, embeddings-optional" |
| Viewer templating | **Jinja2 + HTMX** served by FastAPI | Lightweight, no SPA framework needed |
| Python minimum | **3.11+** | match-case, `tomllib`, `asyncio.TaskGroup` — all useful |
| Config precedence | `codewiki.yaml` < `.env` < env vars < CLI flags | Standard 12-factor ordering |
| Strict grounding default | **True** | PRD §10: trust over fluency; easier to loosen than tighten |
| tree-sitter grammars phase 1 | Python + JS/TS | Cover widest common codebase first per IMPLEMENTATION_PLAN §6 Phase 1 |

### 2026-06-08 — End-to-end baseline implementation

| Area | Implemented |
|------|-------------|
| Ingest | local path/Git URL source resolver, file walker with include/exclude and binary skipping |
| Parse | symbol extraction for Python and JS/TS with import harvesting |
| Repo map | language stats, import graph, framework/entrypoint detection |
| Signals | API/data model/integration/scheduler/config detection |
| Index | symbol-aware chunking + BM25 store and retrieval |
| Wiki | structured page generator, Mermaid diagrams, AGENTS/index/log output |
| Update | manifest-based diff + conditional regeneration |
| Chat | grounded retrieval answer + optional LLM synthesis + file-back |
| Lint | broken links, orphan pages, missing citation checks |
| Viewer | FastAPI renderer with markdown + Mermaid support |
| CLI | ping/generate/update/chat/lint/serve fully wired |

---

## File Map (grows as implemented)

```
codewiki/               ← Python package
  cli.py
  config.py
  llm/
    client.py
    retry.py
    budget.py
  ingest/               ← Phase 1
  signals/              ← Phase 3
  index/                ← Phase 4
  wiki/                 ← Phase 2-3
  query/                ← Phase 4
  lint/                 ← Phase 5
  viewer/               ← Phase 6
agents/
  implementor_agent.md  ← this file
docs/
  PRD.md
  IMPLEMENTATION_PLAN.md
pyproject.toml
codewiki.yaml           ← sample config
.env.example
```

---

## Running the Project

```bash
# Install (editable)
pip install -e ".[dev]"

# Verify LLM endpoint
codewiki ping

# Full generate
codewiki generate --source <path|url>

# Cost estimate only
codewiki generate --source . --dry-run

# Incremental update
codewiki update --source .

# Chat Q&A
codewiki chat "How does refunds work?"

# Health lint
codewiki lint

# Local viewer
codewiki serve --port 8080
```

---

## Notes & Open Questions

- [ ] Upgrade parser backend to full tree-sitter AST across Python/JS/TS/Java/Go/C#
- [ ] Add embeddings + hybrid ranker and budget-aware retrieval routing
- [ ] Improve contradiction detection and stale-page pinpointing in lint/update
- [ ] Add static export mode (MkDocs) and richer viewer navigation
