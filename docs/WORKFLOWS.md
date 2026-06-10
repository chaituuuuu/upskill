# CodeWiki — Command Workflows

> **What this document is:** step-by-step flows for every CLI command, showing which modules run, in what order, and where the LLM / graph / cache get involved. Companion to [`ARCHITECTURE.md`](ARCHITECTURE.md) (the static structure) and [`IMPROVEMENTS.md`](IMPROVEMENTS.md) (gaps).
>
> Verified against the codebase: **2026-06-09.** Entry points: [cli.py](../codewiki/cli.py) → [pipeline.py](../codewiki/pipeline.py).

---

## 0. CLI map

```mermaid
flowchart LR
    U([User]) --> CLI[cli.py Typer app]
    CLI --> ping & generate & update & chat & lint & impact & serve & version
    ping --> LLMc[llm/client.py]
    generate --> RG[pipeline.run_generate]
    update --> RU[pipeline.run_update]
    chat --> RC[pipeline.run_chat]
    lint --> RL[pipeline.run_lint_pipeline]
    impact --> RI[pipeline.run_impact]
    serve --> V[viewer/app.py]
```

Every command first calls `_load_cfg(...)` which merges `codewiki.yaml` → `.env` → env vars → CLI flags into a `CodeWikiConfig`.

---

## 1. `ping` — verify the LLM endpoint

The smoke test that proves the configurable endpoint works.

```mermaid
sequenceDiagram
    participant U as User
    participant CLI as cli.ping
    participant Cfg as config.load_config
    participant Cl as LLMClient
    participant EP as LLM endpoint
    U->>CLI: codewiki ping [--model --base-url --show-config]
    CLI->>Cfg: load + apply overrides
    CLI->>Cl: chat("Say 'CodeWiki ping successful.'")
    Cl->>EP: POST /chat/completions
    EP-->>Cl: completion + usage
    Cl-->>CLI: LLMResult(text, usage)
    CLI->>CLI: budget.record_from_response(usage)
    CLI-->>U: ✓ reply + real token count
```

**Why it matters:** confirms `base_url`/`model`/`api_key` and returns **real** token usage (not a hardcoded estimate).

---

## 2. `generate` — build the full wiki

The main event. `run_generate` in [pipeline.py](../codewiki/pipeline.py).

```mermaid
sequenceDiagram
    participant CLI as cli.generate
    participant P as run_generate
    participant Src as source.py
    participant Wk as walker.py
    participant Pr as parser.py
    participant RM as repo_map.py
    participant Sg as detectors.py (+packs/lens)
    participant Ix as chunker+store
    participant G as CodeGraph
    participant Gen as generate_wiki
    participant Sum as summarizer.py
    participant EP as LLM endpoint

    CLI->>P: run_generate(source, cfg)
    P->>Src: resolve_source (local or git clone)
    P->>Wk: walk_source → FileRecord[]
    P->>Pr: parse_symbols → Symbol[] (calls, imports)
    P->>RM: build_repo_map → RepoMap
    P->>Sg: detect_signals → Signal[]
    P->>Ix: chunk_symbols → IndexStore.build (BM25)
    P->>G: build_from_repo (nodes + edges)
    P->>Gen: generate_wiki(..., code_graph, lens, budget)
    Gen->>Sum: summarize_repository(code_graph=…)
    loop per file (concurrent, cached by hash)
        Sum->>EP: file summary prompt (+ graph neighbors)
        EP-->>Sum: JSON FileSummary
    end
    Sum->>EP: module reduce, then system reduce
    EP-->>Sum: Module/System summaries
    Sum-->>Gen: SummaryBundle
    Gen->>Gen: strict-grounding gate → emit pages → lens.extra_pages
    Gen->>Gen: _refresh_pagemap + rebuild_index + append_log
    Gen-->>P: pages_written
    P->>P: write .codewiki_manifest.json
    P-->>CLI: GenerateResult (files, symbols, signals, pages)
```

### `--dry-run` variant
Runs ingest → parse → signals, then `estimate_repository_tokens` (which includes graph neighbor context size) and prints a cost table. **Writes nothing.**

### Offline / endpoint-down behavior
If `summarize_repository` raises, `generate_wiki` catches it, sets `summary_bundle = None`, and emits a **static fallback wiki** (structural facts only). The tool always produces output.

---

## 3. `update` — incremental, diff-aware refresh

The "compounding artifact" mechanism. `update_wiki` in [updater.py](../codewiki/wiki/updater.py).

```mermaid
flowchart TD
    A[walk_source → new manifest of file hashes] --> B[load old .codewiki_manifest.json]
    B --> C[_diff → added / removed / changed]
    C --> D{any changes?}
    D -->|no| Z[log 'no changes'; exit]
    D -->|yes| E[load_pagemap]
    E --> F{pagemap exists?}
    F -->|no| G[full generate_wiki + warn 'pagemap missing']
    F -->|yes| H[resolve_affected_pages ∪ _predict_pages]
    H --> I[partition affected → locked vs regen]
    I --> J[capture before-text of regen pages]
    J --> K[generate_wiki only_pages=regen_pages]
    K --> L[delete stale pages that were not re-emitted]
    L --> M[locked pages → write .proposed.md siblings]
    M --> N[compare before/after summary → contradiction?]
    N -->|yes| O[mark frontmatter.contradiction + append_contradiction to log]
    N --> P[return rich change report]
```

Key behaviors:
- **Targeted regen:** only pages whose source provenance intersects changed files are rewritten (`only_pages`); the summary cache makes unchanged files free.
- **Human-edit safety:** pages with `human_edited` or a `<!-- codewiki:locked -->` marker are **never overwritten** — a `.proposed.md` sibling is written instead.
- **Contradiction tracking:** if a regenerated page's `## Summary` materially changes, it's flagged and logged.

> ⚠️ **Design note:** affected pages are resolved **twice** — precisely via `resolve_affected_pages` (pagemap) *and* heuristically via `_predict_pages` (which hardcodes page-path conventions). See [IMPROVEMENTS.md](IMPROVEMENTS.md) §Duplicated page-path logic.

---

## 4. `chat` — grounded Q&A (hybrid retrieval)

`run_chat` → `answer_question` in [chat.py](../codewiki/query/chat.py).

```mermaid
sequenceDiagram
    participant CLI as cli.chat
    participant P as run_chat
    participant G as CodeGraph (if --source / manifest)
    participant R as retriever.retrieve
    participant BM as BM25 store
    participant VS as VectorStore (if enabled)
    participant W as wiki index.md
    participant EP as LLM endpoint

    CLI->>P: chat(question, --source?, --file-back?)
    opt source available
        P->>G: build_from_repo (for graph-scoped boost)
    end
    P->>R: retrieve(question, cfg, code_graph)
    R->>BM: BM25 top-k
    opt embeddings enabled
        R->>VS: vector cosine top-k
    end
    R->>R: RRF fuse + graph neighborhood boost
    R-->>P: top-k snippets (with citations)
    P->>W: load wiki context via index.md order
    P->>EP: synthesize answer (only provided context + citations)
    EP-->>P: grounded answer
    opt --file-back
        P->>P: write workflows/qa-*.md
    end
    P-->>CLI: answer (or evidence-list fallback if LLM down)
```

- **Graph scope is optional:** with `--source` (or a recorded manifest source), a real `CodeGraph` boosts structurally-related snippets; otherwise a metadata (symbol-sharing) fallback boost is used.
- **Fallback:** if the endpoint is unavailable, the answer degrades to a citation-backed evidence list — never empty.

---

## 5. `impact` — "what breaks if I change X?"

`run_impact` → [impact.py](../codewiki/query/impact.py).

```mermaid
flowchart LR
    A[target: symbol id or file path] --> B[ingest + parse + build CodeGraph]
    B --> C[code_graph.impact_analysis = backend.ancestors]
    C --> D[affected_files = ancestors minus external]
    D --> E{wiki/.codewiki_pagemap.json?}
    E -->|yes| F[map files → affected wiki pages]
    E -->|no| G[affected_pages = empty]
    F --> H[print affected files + pages]
    G --> H
```

Answers the impact-radius question by walking **graph ancestors** (everything that transitively depends on the target), then maps those files to wiki pages via the pagemap.

---

## 6. `lint` — wiki health

`run_lint_pipeline` → [health.py](../codewiki/lint/health.py).

```mermaid
flowchart TD
    A[scan wiki/*.md, skip .proposed.md] --> B[per page: extract citations + links]
    B --> C[classify each citation]
    C --> C1[placeholder = :L1-L1]
    C --> C2[unresolved = file missing — needs --source]
    C --> C3[stale = line range beyond EOF]
    B --> D[broken_links = .md links to missing pages]
    B --> E[missing_citations = has '## Sources' but none found]
    B --> F[orphans = no inbound links]
    C1 & C2 & C3 & D & E & F --> G[report counts + samples]
```

> Citation **resolution** (vs. mere presence) requires `--source` so it can open the referenced files and check line ranges.

---

## 7. `serve` — local web viewer

`create_app` in [viewer/app.py](../codewiki/viewer/app.py) (FastAPI + markdown-it). Renders the `wiki/` tree with a nav sidebar (grouped by folder), injects heading anchors, renders Mermaid, and exposes a chat panel that calls `run_chat` under the hood.

```mermaid
flowchart LR
    B([Browser]) --> FA[FastAPI app]
    FA --> Nav[nav tree from wiki/ folders]
    FA --> Page[markdown-it render + heading ids + mermaid]
    FA --> ChatEP[/chat → pipeline.run_chat/]
```

---

## 8. Cross-cutting: how cost stays bounded

```mermaid
flowchart LR
    Cache[(summary cache<br/>by file hash)] -->|hit = 0 tokens| Map[map stage]
    Map --> Budget[Budget records real usage]
    Budget --> Stop{over token_budget?}
    Stop -->|yes| Raise[BudgetExceeded]
    Stop -->|no| Continue[continue]
    Dry[--dry-run] -->|estimate first| Map
```

- First run pays per file; **re-runs are near-free** thanks to hash caching.
- `--dry-run` estimates before spending; `token_budget` hard-stops a runaway run.
- Vectors are signature-cached and only computed when `embedding.enabled`.

---

## 9. Quick reference — module touchpoints per command

| Command | source | walker | parser | repo_map | signals | graph | index | summarizer | generator | retriever | LLM |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| `ping` | | | | | | | | | | | ✅ |
| `generate` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | | ✅ |
| `update` | ✅ | ✅ | ✅ | ✅ | ✅ | | | ✅* | ✅ | | ✅* |
| `chat` | ✅? | ✅? | ✅? | ✅? | | ✅? | ✅ | | | ✅ | ✅ |
| `impact` | ✅ | ✅ | ✅ | ✅ | | ✅ | | | | | |
| `lint` | ✅? | | | | | | | | | | |
| `serve` | | | | | | | ✅ | | | ✅ | ✅ |

✅ = always, ✅* = via `generate_wiki` for affected pages, ✅? = only when `--source` is supplied.
