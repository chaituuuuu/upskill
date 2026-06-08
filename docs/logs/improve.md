

## 1. Executive Summary

**The skeleton is genuinely good — clean module boundaries, a provider-agnostic async LLM client, sane config precedence, and a sensible pipeline. But the single most important finding is this: the wiki generator does not call the LLM at all.** Every "business-oriented" sentence in the generated wiki is a **static template string + regex signal counting** (see generator.py). The LLM is only wired into `ping` and `chat`. So the actual thesis of the project — *an LLM that reads code and synthesizes business meaning into a compounding wiki* — is **not implemented yet**. What exists is a high-quality harness with the brain not plugged in.

That reframes "Phases 0–6 complete": the *plumbing* for those phases exists, but the *intelligence* (LLM synthesis), *vectors*, *incremental update*, and *tree-sitter multi-language* are stubs or partial. **My top recommendation: Phase 7 should not be "add a knowledge graph." It should be "turn on the brain" — wire the LLM into generation with a map-reduce summarizer — and build a lightweight code graph as the structure the LLM reasons over.** Graph-first retrieval is the right long-term call for CodeWiki (code *is* a graph), but it's only valuable once the LLM is actually generating grounded content. Details below.

---

## Section A — Architectural Improvements & Strategic Decisions (prioritized)

### A1. **CRITICAL: Generation bypasses the LLM entirely**
generator.py imports no LLM client. `business_summary` and `technical_summary` are hardcoded f-strings; capability/glossary pages are regex counts from detectors.py. 
- **Impact:** Output is indistinguishable from a non-AI static analyzer. The "business lens" differentiator doesn't exist yet.
- **Fix:** Introduce a `wiki/summarizer.py` that runs **file → module → system** map-reduce LLM summarization (the plan already describes this in IMPLEMENTATION_PLAN.md). Cache per file hash in `cfg.run.cache_dir` (which today is created but never used for summaries).

### A2. **Token accounting is cosmetic → cost controls don't actually work**
client.py returns only `choices[0].message.content` and **discards the `usage` field**. `Budget.record_from_response()` in budget.py therefore never receives real numbers; `ping` records a hardcoded `prompt_tokens=20, completion_tokens=10` (cli.py). `--dry-run` estimates `words * 1.35` for text that generation never actually sends.
- **Fix:** Return a richer result object (`text`, `usage`, `model`) from `chat()`; feed `usage` into `Budget`; enforce `token_budget` between map-reduce stages. Without this, NFR "cost predictability ±15%" is untestable.

### A3. **`update` is a full regenerate, not incremental**
updater.py computes a real diff (`added/removed/changed`) and then **throws it away**, calling `generate_wiki(...)` over everything if `changed_count > 0`. No per-page targeting, no contradiction flagging (FR-15), no preservation of human edits.
- **Fix:** Map changed files → affected pages via a stored `page → source-symbols` reverse index; regenerate only those; append contradiction notes to `log.md`.

### A4. **The dependency graph is broken at the foundation**
repo_map.py builds `import_graph` as `file_path → raw module names` (e.g. `os`, `codewiki.config`). These are **never resolved to in-repo file nodes**, so the Mermaid component graph (diagrams.py) mixes external libraries with internal files and draws edges to nodes that don't exist. Also, `Symbol.calls` is declared in models.py but **never populated** — there is no call graph at all.
- **Fix:** Resolve imports to internal paths; separate internal vs external nodes; populate `calls`. This is the prerequisite for both better diagrams *and* the knowledge-graph direction in Section D.

### A5. **"Multi-language via tree-sitter" is actually `ast` + regex (Python/JS only)**
parser.py handles only `python` (via `ast`) and `javascript/typescript` (via regex). **Java, Go, C#, etc. produce zero symbols** — they fall through to 120-line blind chunks (chunker.py). Given your banking **Java/Spring Boot** target from the last discussion, this is a blocking gap: Spring `@RestController`/`@GetMapping` are invisible to the FastAPI-style route regex in detectors.py.
- **Fix:** Adopt real tree-sitter grammars (or language servers) and add a Spring signal pack. This is mandatory for the AI-Opportunity-Discovery banking use case.

### A6. **"Optional vectors" are configured but unwired**
Config exposes `embedding_model`/`embedding_base_url` and the client has `embed()`, but store.py is **Whoosh BM25 only** (with a substring-count JSON fallback), and retriever.py is a one-line passthrough. There is **no FAISS/vector store** anywhere. So your project summary's "keyword + optional embeddings" is really "keyword only" today — important context for Section D.

**Lower-severity:** `datetime.utcnow()` is deprecated (chat.py); chat loads the first 6 pages alphabetically rather than via `index.md`; walker holds **all** file text in memory (fine at 50k LOC, risky at 500k+).

---

## Section B — Feature Enhancements (with justification)

| Enhancement | Why it matters | Builds on |
|---|---|---|
| **LLM map-reduce summarizer with hash cache** | Turns the static skeleton into the actual product; enables re-runs at near-zero cost | A1, `cache_dir` |
| **Confidence scoring per claim** (`high/medium/low`) | PRD promises it; frontmatter has no `confidence` field today. Gate strict-grounding on it | pages.py |
| **Citation *resolution* (not just presence)** | Lint only regex-matches `path:Lx-Ly` strings; it never checks the line range exists or still matches. Many citations are fake `:L1-L1` (generator.py) | lint/health.py |
| **Impact analysis command** (`codewiki impact <symbol>`) | "What breaks if I change X?" — only possible with a real call/dep graph (A4) | Section D |
| **Pluggable "analysis lenses"** | Same engine → Onboarding / Compliance / Security / **AI-Opportunity** views via prompt+template packs. This is what makes it a platform, not a tool | generator |
| **Spring/Java signal pack** | Unlocks the banking use case end-to-end | detectors |
| **Real `usage`-based budgeting + run manifest** | Cost predictability + resumability NFRs | A2 |

---

## Section C — Use Cases & Deployment

### Concrete use cases

| # | Use case | Who benefits | Problem solved | Impact |
|---|---|---|---|---|
| 1 | **Engineer onboarding wiki** | New hires, tech leads | Weeks of reverse-engineering | Ramp days→hours; fewer interrupt questions |
| 2 | **AI/ML Opportunity Discovery** (banking) | Innovation/AI office, PMs | "Where can AI help?" answered from *code truth* | Ranked, evidence-backed opportunity register across repos |
| 3 | **Compliance / PII data-flow map** | Risk, security, audit | Where regulated data is read/stored/sent | Audit-ready evidence base (GDPR/PCI/SR 11-7) |
| 4 | **Legacy modernization radar** | Architects | Which monolith pieces to strangle first | Prioritized migration backlog with hotspots |
| 5 | **M&A / due-diligence scan** | Corp dev, CTO | Honest picture of an acquired codebase | Risk + capability map in days, not months |
| 6 | **Capability & API catalog** | Platform teams | Duplicate builds across teams | Reuse; fewer redundant services |
| 7 | **Security posture wiki** | AppSec | Auth flows, secrets, OWASP touchpoints, kept fresh | Continuous, grounded threat surface |
| 8 | **Bus-factor capture** | Eng management | Tribal knowledge leaves with people | Undocumented logic encoded before attrition |

### Deployment models
- **Local CLI** (today) — dev laptops, sensitive code with local models (Ollama/vLLM). Strongest privacy story.
- **CI/CD job** — on merge to `main`, run `update` and publish the wiki as a build artifact / GitHub Pages. Keeps it fresh automatically (the "compounding" promise).
- **Daemon / webhook service** — subscribe to repo events; debounce; incremental update; post a changelog comment on the PR.
- **Cloud control-plane (enterprise)** — central scheduler fans out per-repo workers; results pushed to a shared, searchable portal.

---

## Section D — Knowledge Graph vs. Vector Search

**Context that changes the framing:** today CodeWiki is **keyword-only** (vectors unwired, A6), yet it *already* produces graph-shaped data (symbols, imports, signals) — it just underuses and partly breaks it (A4). So the real question isn't "replace vectors with a graph"; it's **"promote the latent code graph to a first-class index, and add vectors for the fuzzy recall BM25 can't do."**

| Dimension | Knowledge Graph | Vector Search | Winner for CodeWiki |
|---|---|---|---|
| Explainability ("why this result?") | Clear — follow typed edges | Opaque similarity | **Graph** |
| Relationship-type awareness (calls / imports / route→handler→model) | Explicit | Implicit | **Graph** |
| Impact radius ("what breaks if I change X?") | Native traversal | Brute force | **Graph** |
| Cycle / circular-dep detection | Native algorithm | Manual | **Graph** |
| Fuzzy / semantic ("where's the retry logic?") | Weak | Excellent | **Vector** |
| Cross-domain / NL questions | Bounded by schema | Open-ended | **Vector** |
| Storage & query speed (structural) | Fast, compact | Costly at scale | **Graph** |
| Hallucination risk in chat | Lower (edge-constrained) | Higher (semantic drift) | **Graph** |

**Recommendation: hybrid, graph-first / vectors-secondary.**
- The **graph** is the backbone for structure-truth: component diagrams (fixes A4), `impact` analysis, lint orphan/cycle detection, and capability→code traceability. It also *constrains* the LLM during generation (feed it the actual neighbors), which is the cheapest, strongest anti-hallucination lever.
- **Vectors** ride on top for semantic retrieval in `chat` and for "find similar logic across repos."
- **Retrieval flow:** graph locates the structural neighborhood → BM25 + vectors rank within it → LLM synthesizes with citations.

**Tooling — match your existing local-first, no-server ethos (you chose Whoosh, not Elasticsearch):**
- **v1: NetworkX** — in-process, zero infra, trivial to populate from existing `Symbol`/`import_graph`. Perfect for ≤~100k nodes.
- **Scale: Kùzu** — embedded graph DB with Cypher, columnar, handles large graphs, still no server. Natural upgrade path.
- **Neo4j** — only if you need a shared multi-user server later. Don't start here; it breaks the local ethos and adds ops burden.

> The graph is also the **federation substrate** for the 50k-engineer / 100+ repo case (Section C deployment): per-repo subgraphs → linked into an org graph keyed by shared services/APIs, giving a cross-repo capability map and breaking knowledge silos.

---

## Section E — Phase 7 Roadmap

**Principle: turn on the brain before adding the graph; build the graph before the fancy retrieval.**

| Step | Work | ROI | Risk |
|---|---|---|---|
| **7.1 LLM synthesis (map-reduce + hash cache)** | `wiki/summarizer.py`; file→module→system; cache by `FileRecord.hash` | **Highest** — this *is* the product | Med |
| **7.2 Real token usage + budget enforcement** | Return `usage` from `chat()`; wire `Budget`; fix `--dry-run` | High (cost trust) | Low |
| **7.3 Fix & enrich the code graph** | Resolve imports→files; populate `Symbol.calls`; NetworkX model | High (diagrams, impact, anti-hallucination) | Med |
| **7.4 True incremental update** | page↔source reverse index; regenerate only affected; contradiction flags | High (freshness promise) | Med |
| **7.5 Hybrid retrieval** | Graph-scope → BM25 + vectors; wire `embed()` + FAISS/Kùzu | Med-High | Med |
| **7.6 Java/Spring (tree-sitter + signal pack)** | Real grammars; Spring routes/entities; **unlocks banking** | High *if* banking is the target | Med |
| **7.7 `impact` + lint upgrades** | Traversal command; citation *resolution* in lint | Med | Low |

### 2-week pilot (validate the direction before committing)
- **Days 1–4:** 7.1 minimal — LLM summaries for file + system pages only, hash-cached. Run on one real medium repo; eyeball quality vs. today's static text.
- **Days 5–7:** 7.2 — capture real `usage`; produce an honest cost number for that repo.
- **Days 8–11:** 7.3 minimal — NetworkX graph from existing symbols/imports (resolved); regenerate the component diagram; add a throwaway `impact <symbol>` traversal.
- **Days 12–14:** Decision checkpoint — compare (a) wiki quality before/after LLM, (b) real cost vs. estimate, (c) graph diagram correctness. **Go/no-go on Kùzu + vectors.**
- **Success criteria:** LLM pages rated clearly better by a human reviewer; cost within ±15% of estimate; component diagram has zero phantom edges.

---

## Open Questions

1. **Is banking AI-Opportunity-Discovery the primary target?** If yes, **Java/Spring (7.6) jumps up the priority list** — today the parser and signal regexes produce ~nothing for Spring.
2. **Graph store commitment:** OK to start with NetworkX (in-process) and treat Kùzu as the scale path, keeping Neo4j off the table for v1?
3. **Generation cost ceiling per repo** — what's an acceptable $/run for, say, a 200k-LOC repo? This sets how aggressive the map-reduce + caching needs to be.
4. **Strict-grounding vs. inferred business intent** — when the LLM infers "why," do we allow `confidence: low` claims, or refuse anything without a citation? (PRD has both; they conflict.)
5. **Human-in-the-loop for `chat --file-back`** — require a `[DRAFT]`/review gate before answers enter the wiki, to prevent self-pollution?

---

Want me to (a) **save this as `docs/REVIEW_PHASE7.md`** for your team, (b) **write the Banking AI-Opportunity-Discovery spec** (Spring detectors, scoring rubric, governance fields, register format), or (c) **start the 2-week pilot at step 7.1** (wire the LLM into generation)? I'd recommend (c) — it validates the entire premise fastest.