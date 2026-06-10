# Product Requirements Document (PRD)
## CodeWiki — Business-Oriented Knowledge Base Generator for Source Code

> **One-liner:** Point it at a large codebase and it produces — and keeps current — a structured, interlinked wiki that explains *what the system does for the business*, not just how the code works.

---

## 0. Implementation Status (as of 2026-06-09)

> This section reconciles the PRD vision with what the code in [`codewiki/`](../codewiki/) actually does today. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for how it works, [`WORKFLOWS.md`](WORKFLOWS.md) for command flows, and [`IMPROVEMENTS.md`](IMPROVEMENTS.md) for gaps and next steps.

**Maturity: working end-to-end prototype.** The LLM is wired into generation (map→reduce summarization), the code graph grounds both diagrams and prompts, retrieval is hybrid (keyword + optional vectors + graph), and incremental update is page-targeted. The main gaps are **test coverage, scale hardening, and deployment surfaces** — not core capability.

| Capability | Status | Where |
|---|---|---|
| Configurable LLM (`base_url`/`model`/`api_key`, OpenAI-compatible) + real token usage | ✅ Done | [llm/client.py](../codewiki/llm/client.py), [llm/budget.py](../codewiki/llm/budget.py) |
| LLM map-reduce synthesis (file→module→system), hash-cached, JSON-repair, offline fallback | ✅ Done | [wiki/summarizer.py](../codewiki/wiki/summarizer.py) |
| Code graph (NetworkX): files/symbols/external, `imports`/`calls`/`defines`, impact, cycles | ✅ Done | [graph/code_graph.py](../codewiki/graph/code_graph.py), [graph/backend.py](../codewiki/graph/backend.py) |
| Graph-grounded prompts (neighbor context injected into file summaries) | ✅ Done | [wiki/summarizer.py](../codewiki/wiki/summarizer.py) |
| Hybrid retrieval (BM25 + optional vectors via RRF + graph neighborhood boost) | ✅ Done | [index/retriever.py](../codewiki/index/retriever.py), [index/vector_store.py](../codewiki/index/vector_store.py) |
| Multi-language parsing (Python AST; tree-sitter Java/Go/C# if installed; JS/TS regex) | ✅ Done¹ | [ingest/parser.py](../codewiki/ingest/parser.py) |
| Framework signal packs (Spring, FastAPI, Flask, Express) + registry + auto-detect | ✅ Done | [signals/packs/](../codewiki/signals/packs/) |
| Pluggable analysis lenses (business, onboarding, compliance, security, ai_opportunity) | ✅ Done | [lenses/](../codewiki/lenses/) |
| Confidence frontmatter + citation **resolution** lint (placeholder/unresolved/stale) | ✅ Done | [wiki/pages.py](../codewiki/wiki/pages.py), [lint/health.py](../codewiki/lint/health.py) |
| Incremental update (pagemap-targeted regen, contradiction flags, locked/`.proposed.md`) | ✅ Done | [wiki/updater.py](../codewiki/wiki/updater.py), [wiki/pagemap.py](../codewiki/wiki/pagemap.py) |
| `impact` command (graph ancestors → affected files/pages) | ✅ Done | [query/impact.py](../codewiki/query/impact.py) |
| Local web viewer (FastAPI + markdown-it, nav, chat) | ✅ Done | [viewer/app.py](../codewiki/viewer/app.py) |
| Automated tests / golden fixtures | ❌ Missing | — |
| Graph scale backend (Kùzu), multi-repo federation | ❌ Missing | — |
| Deployment surfaces (CI job, daemon/`watch`, Pages export) | ❌ Missing | — |
| Streaming/bounded-memory ingest for very large repos | ⚠️ Partial | walker loads full text in memory |

¹ tree-sitter grammar wheels are optional; without them, non-Python/JS files fall back to blind chunking.

**CLI surface today:** `ping · generate (--lens, --dry-run) · update · chat (--source, --file-back) · lint · impact · serve · version`.

---

## 1. Background & Motivation

### 1.1 The problem
Large codebases are opaque to almost everyone:
- **New engineers** spend weeks reverse-engineering architecture and tribal knowledge.
- **Product managers, analysts, and leadership** can't see what capabilities actually exist in code vs. what's documented.
- **Documentation rots.** Humans abandon wikis because the *maintenance burden grows faster than the value* (cross-references, stale claims, consistency across dozens of pages).

### 1.2 The insight (from the "LLM Wiki" pattern)
Most code+LLM tooling today is **RAG**: retrieve chunks at query time, synthesize an answer, throw it away. The LLM rediscovers knowledge *from scratch on every question*. Nothing accumulates.

This project takes the opposite stance: the LLM **incrementally builds and maintains a persistent wiki** — a compounding artifact that sits between a human and the raw source code. The synthesis is **compiled once and kept current**, not re-derived per query. Cross-references already exist. Contradictions are already flagged.

> Borrowing the analogy: **the codebase is the raw source, the LLM is the programmer, the wiki is the codebase, and Markdown/Obsidian/a web viewer is the IDE.**

### 1.3 The business twist
Generic code-doc tools (DeepWiki, etc.) produce *technical* docs. **CodeWiki's differentiator is the business lens:** it translates code into capabilities, domain concepts, workflows, owners, and risks that a non-engineer can act on.

---

## 2. Goals & Non-Goals

### 2.1 Goals
1. Ingest a **large** codebase (local folder or Git URL, multi-language) and produce a navigable, interlinked Markdown wiki.
2. Lead with a **business-oriented view**: capabilities, domain glossary, workflows, stakeholders, data assets, risk/compliance touchpoints.
3. Make the wiki a **persistent, compounding artifact** — re-running on a changed codebase *updates* pages rather than regenerating blindly.
4. **Ground every claim in code** with file/line citations to fight hallucination.
5. Be **LLM-agnostic**: configurable `base_url`, `model`, and `api_key` (works with OpenAI, Azure OpenAI, Ollama, vLLM, OpenRouter, LM Studio, Together, etc.).
6. Auto-generate **architecture & flow diagrams** (Mermaid) embedded in pages.
7. Provide a **"chat with the repo"** Q&A grounded in the wiki + code, where good answers can be *filed back* as new wiki pages.

### 2.2 Non-Goals (v1)
- Not a real-time IDE plugin or inline code-completion tool.
- Not a replacement for hand-written API reference (we *complement* it).
- Not auto-committing to the user's source repo (wiki lives in its own output dir / git repo).
- No fine-tuning of models; we only use inference via the configured endpoint.
- No multi-tenant SaaS/auth in v1 (single-user / single-workspace CLI + local web viewer).

---

## 3. Target Users & Personas

| Persona | Need | What CodeWiki gives them |
|---|---|---|
| **New Engineer** (onboarding) | "How is this system built and why?" | Architecture overview, module map, dependency graph, setup guide — all linked. |
| **Product Manager / BA** | "What can this system actually do?" | Capability catalog, domain glossary, business workflows in plain language. |
| **Tech Lead / Architect** | "Where are the risks and coupling hotspots?" | Component graph, hotspot/complexity flags, contradiction & stale-doc lint. |
| **Leadership / Due-diligence** | "What's the shape and health of this asset?" | Executive summary, capability map, tech-stack inventory, risk register. |

**Primary audience: business stakeholders.** Secondary: engineers. The wiki supports a **toggle/lens** between *Business view* and *Technical view* where useful.

---

## 4. Core Concepts & Architecture (Three Layers)

Following the LLM Wiki pattern, there are three layers plus a schema:

1. **Raw sources (immutable):** the codebase. The LLM reads, never modifies it. Source of truth.
2. **The wiki (LLM-owned):** a directory of generated, interlinked Markdown pages — overview, capabilities, components, concepts, glossary, workflows, ADRs, diagrams. The LLM creates and maintains this entirely.
3. **The schema (config):** an `AGENTS.md`-style document + structured config that tells the engine how the wiki is organized, naming/linking conventions, page templates, and ingest/query/lint workflows. This is co-evolved per project.

Plus two navigation files:
- **`index.md`** — content-oriented catalog of every page (link + one-line summary + metadata), organized by category. Read first when answering a query.
- **`log.md`** — chronological, append-only record of ingests/queries/lints with a parseable prefix (e.g. `## [2026-06-08] ingest | payments-service`).

---

## 5. Functional Requirements

### 5.1 Source Ingestion
- **FR-1** Accept a **local directory path** or **Git URL** (public; private via token).
- **FR-2** Respect `.gitignore` + a configurable include/exclude glob list; skip binaries, vendored deps, build artifacts, lockfiles (configurable).
- **FR-3** **Multi-language parsing** via tree-sitter (Python, JS/TS, Java, Go, C#, etc.) to extract symbols (files, classes, functions, modules, routes, configs).
- **FR-4** Build a **repo map**: file tree, language stats, dependency/import graph, entry points, and detected frameworks.
- **FR-5** Detect **business signals**: API endpoints/routes, DB schemas/models, queue topics, feature flags, env/config, scheduled jobs, external integrations.

### 5.2 Indexing & Retrieval
- **FR-6** Chunk code by symbol boundaries (not blind line windows) and store with metadata (path, language, symbol, start/end line).
- **FR-7** **Hybrid retrieval**: keyword/BM25 + optional embeddings (configurable embedding endpoint). At small scale, `index.md` alone may suffice (no vector DB required); embeddings turn on for large repos.
- **FR-8** Every retrieved snippet carries a **citation** (`path:Lstart-Lend`).

### 5.3 Wiki Generation
- **FR-9** Generate a **page hierarchy** (see §7) from templates, populated by the LLM with grounded content.
- **FR-10** Maintain **cross-references / wikilinks** between pages automatically (e.g. a capability links to the components that implement it).
- **FR-11** Embed **Mermaid diagrams**: system context, component/module graph, key sequence/flow diagrams, ER diagram for data models.
- **FR-12** Add **YAML frontmatter** to every page (tags, source files, last-updated, confidence, audience: business|technical) so downstream tools (e.g. Dataview) can query it.
- **FR-13** Produce a **business glossary** mapping domain terms → where they live in code.

### 5.4 Incremental Update (compounding)
- **FR-14** On re-run, **diff** the codebase (git diff or content hashes) and update only affected pages; preserve human edits where marked.
- **FR-15** When new code contradicts an existing page, **flag the contradiction** rather than silently overwriting.
- **FR-16** Append every operation to `log.md`; keep `index.md` current on every run.

### 5.5 Query / Chat
- **FR-17** **Chat with the repo**: answer questions grounded in wiki pages + code, with citations.
- **FR-18** **File-back**: offer to save a good answer (comparison, analysis, new diagram) as a new wiki page so explorations compound.
- **FR-19** Support multiple answer formats: Markdown page, comparison table, Mermaid diagram.

### 5.6 Lint / Health
- **FR-20** **Lint pass**: detect contradictions, stale claims (code changed but page didn't), orphan pages (no inbound links), missing pages for important concepts, and broken citations.
- **FR-21** Suggest follow-up questions and gaps to investigate.

### 5.7 Output & Delivery
- **FR-22** Emit a **self-contained Markdown wiki** (git-friendly, Obsidian-compatible).
- **FR-23** Optional **local web viewer** (renders Markdown + Mermaid, sidebar nav from `index.md`, search, chat panel).
- **FR-24** Optional **static-site export** (MkDocs/Docusaurus-style) for sharing.

### 5.8 Configuration (explicit requirement)
- **FR-25** **LLM endpoint is fully configurable** — `base_url`, `model`, `api_key`, plus `temperature`, `max_tokens`, `timeout`, and a separate optional `embedding_model` / `embedding_base_url`. Configurable via file (`codewiki.yaml`/`.env`), env vars, and CLI flags, with documented precedence.
- **FR-26** Configurable **concurrency / rate limits / retry-with-backoff** to handle large repos without hammering the endpoint.
- **FR-27** **Cost & token controls**: per-run token budget, max files, and a `--dry-run` that reports estimated cost before generating.

---

## 6. Non-Functional Requirements

| Category | Requirement |
|---|---|
| **Scale** | Handle repos up to ~1M LOC / tens of thousands of files via map-reduce summarization (file → module → system). |
| **Determinism** | Same inputs + config → stable structure; pin model/temperature; record run manifest. |
| **Resilience** | Resume interrupted runs (checkpointing); never lose partial progress on a large repo. |
| **Cost transparency** | Token + request accounting per run, surfaced in `log.md` and a run summary. |
| **Privacy/Security** | No code leaves the configured endpoint; secrets never logged; support fully local models (Ollama/vLLM) for sensitive code. |
| **Grounding/Trust** | Every non-trivial claim cites code; configurable "strict grounding" mode that refuses unsupported claims. |
| **Portability** | Runs cross-platform (Windows/macOS/Linux); wiki output is plain files. |
| **Extensibility** | Pluggable parsers, page templates, output renderers, and LLM providers. |

---

## 7. Information Architecture (Generated Wiki Structure)

```
wiki/
├─ AGENTS.md                 # schema: conventions + workflows (the "config" layer)
├─ index.md                  # catalog of all pages, by category
├─ log.md                    # append-only chronological history
├─ 00-overview/
│  ├─ executive-summary.md   # business: what is this system, in 1 page
│  ├─ system-context.md      # Mermaid C4-ish context diagram
│  └─ tech-stack.md          # languages, frameworks, infra inventory
├─ capabilities/             # BUSINESS LENS — one page per capability/feature
│  └─ <capability>.md        # what it does, who uses it, code that implements it
├─ workflows/                # business/process flows (Mermaid sequence)
├─ components/               # TECHNICAL LENS — services, modules, packages
│  └─ <component>.md         # responsibility, interfaces, deps, complexity
├─ domain/
│  ├─ glossary.md            # domain term → meaning → where in code
│  └─ data-model.md          # entities + ER diagram
├─ integrations/             # external systems, APIs, queues, 3rd parties
├─ operations/
│  ├─ setup.md               # how to run/build/test
│  └─ risk-register.md       # hotspots, complexity, security/compliance flags
└─ decisions/                # inferred ADRs (why things might be the way they are)
```

Each page = frontmatter + body + "Sources" section (citations) + "Related" links.

---

## 8. Key User Journeys

1. **Generate from scratch:** `codewiki generate --source <repo>` → progress → `wiki/` produced → open web viewer → land on Executive Summary.
2. **Onboard:** PM reads Executive Summary → Capabilities → clicks a capability → sees plain-language description + the components/files that implement it.
3. **Ask:** "How does refunds work?" → grounded answer with citations → "Save as wiki page?" → filed under `workflows/refunds.md`.
4. **Keep current:** code changes → `codewiki update` → diff-aware refresh → contradictions flagged in `log.md`.
5. **Health check:** `codewiki lint` → report of stale/orphan/contradiction issues + suggested gaps.

---

## 9. Success Metrics

- **Time-to-understanding:** new engineer can answer 5 architecture questions in <30 min using only the wiki.
- **Grounding rate:** ≥95% of factual claims carry a valid citation (auto-checked by lint).
- **Coverage:** ≥90% of top-level modules and public endpoints have a page.
- **Freshness:** after `update`, 0 pages reference deleted symbols.
- **Cost predictability:** actual run cost within ±15% of `--dry-run` estimate.
- **Provider portability:** identical run succeeds against ≥3 endpoints (e.g. OpenAI, Azure, Ollama) with only config changes.

---

## 10. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| **Hallucinated architecture** | Strict grounding mode + mandatory citations + lint that verifies citations resolve. |
| **Context-window limits on huge repos** | Map-reduce/hierarchical summarization; symbol-level chunking; retrieval-augmented page generation. |
| **Runaway cost** | Token budgets, `--dry-run` estimate, caching, incremental updates. |
| **Stale wiki** | Diff-aware `update` + `lint` + `log.md` timeline. |
| **Sensitive code leaving network** | First-class local-model support; no telemetry; secret redaction. |
| **Inconsistent output across runs** | Pinned model/temp, page templates, run manifest, deterministic ordering. |
| **Business framing wrong / too generic** | Glossary + capability extraction prompts grounded in code signals (routes, models); human-in-the-loop review and file-back. |

---

## 11. Configuration Surface (illustrative)

```yaml
# codewiki.yaml
llm:
  base_url: "http://localhost:11434/v1"   # OpenAI-compatible; swap for any provider
  model: "qwen2.5-coder:32b"
  api_key_env: "CODEWIKI_API_KEY"          # read from env, never inline secrets
  temperature: 0.2
  max_tokens: 4096
  timeout_s: 120
embedding:
  enabled: true
  base_url: "http://localhost:11434/v1"
  model: "nomic-embed-text"
source:
  path_or_url: "./"
  include: ["**/*.py", "**/*.ts", "**/*.go"]
  exclude: ["**/node_modules/**", "**/dist/**", "**/*.lock"]
generation:
  audience: "business"        # business | technical | both
  diagrams: true
  strict_grounding: true
limits:
  max_files: 5000
  token_budget: 2_000_000
  concurrency: 4
output:
  dir: "./wiki"
  viewer: true
```

---

## 12. Open Questions (to confirm during build)
1. Default primary language/stack for the engine — **assumed Python** (best tree-sitter + LLM ecosystem). OK?
2. Web viewer: lightweight custom (FastAPI + Mermaid) vs. MkDocs Material export — or both?
3. How aggressive should "inferred ADRs / business intent" be vs. strictly-grounded facts only?
4. Private repo auth scope for v1 (GitHub token only, or also GitLab/Bitbucket)?

---

*See [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) for the phased build plan, architecture, and milestones.*
