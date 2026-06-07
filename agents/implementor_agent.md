# Implementor Agent

> **Role:** I am the **Implementor Agent** for the CodeWiki project.  
> **Model:** GitHub Copilot (Claude Sonnet 4.6)  
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
| 0 | Scaffold & Config + LLM Client | 🔄 In Progress |
| 1 | Ingest & Repo Map | ⬜ Not Started |
| 2 | Wiki Generation v1 | ⬜ Not Started |
| 3 | Business Lens & Diagrams | ⬜ Not Started |
| 4 | Index, Retrieval & Chat | ⬜ Not Started |
| 5 | Incremental Update & Lint | ⬜ Not Started |
| 6 | Viewer & Export | ⬜ Not Started |
| 7 | Scale & Hardening | ⬜ Not Started |

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

- [ ] Phase 1: confirm tree-sitter grammar wheel availability for Windows (may need to compile from source)
- [ ] Phase 3: prompt engineering for capability extraction — iterate against a real medium repo
- [ ] Phase 4: threshold for switching from index.md-only to FAISS — probably ~5k chunks
- [ ] Phase 6: decide MkDocs vs Docusaurus for static export (MkDocs leans Python ecosystem)
