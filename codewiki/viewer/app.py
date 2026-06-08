"""FastAPI app for browsing generated wiki markdown pages."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from markdown_it import MarkdownIt
from pydantic import BaseModel, Field

from codewiki.config import CodeWikiConfig
from codewiki.pipeline import run_chat


class ChatRequest(BaseModel):
        question: str = Field(min_length=2, max_length=2000)
        file_back: bool = False


def create_app(wiki_root: Path, cfg: CodeWikiConfig | None = None) -> FastAPI:
    wiki_root = wiki_root.resolve()
    md = MarkdownIt("commonmark", {"html": True, "linkify": True})

    app = FastAPI(title="CodeWiki Viewer", version="0.1.0")

        def _safe_page_path(page_path: str) -> Path:
                path = (wiki_root / page_path).resolve()
                if not path.exists() or not path.is_file() or wiki_root not in path.parents:
                        raise HTTPException(status_code=404, detail="Page not found")
                return path

        def _page_list() -> list[str]:
                return sorted(
                        p.relative_to(wiki_root).as_posix() for p in wiki_root.rglob("*.md") if p.is_file()
                )

        def _layout(title: str, initial_page: str, pages: list[str]) -> str:
                page_items = "\n".join(
                        (
                                "<button class='nav-item' data-page='"
                                f"{escape(page)}'"
                                ">"
                                f"{escape(page)}"
                                "</button>"
                        )
                        for page in pages
                )
                chat_flag = "true" if cfg is not None else "false"
        return f"""
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
  <title>{title}</title>
    <link rel=\"preconnect\" href=\"https://fonts.googleapis.com\" />
    <link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin />
    <link href=\"https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap\" rel=\"stylesheet\" />
  <script src=\"https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js\"></script>
  <style>
        :root {{
            --bg-a: #0f1b2f;
            --bg-b: #1a3557;
            --bg-c: #f4c95d;
            --panel: rgba(248, 251, 255, 0.92);
            --panel-strong: #ffffff;
            --line: rgba(8, 25, 48, 0.12);
            --ink: #12233d;
            --ink-soft: #4d607c;
            --accent: #ff6b35;
            --accent-alt: #0077b6;
            --ok: #1f9d73;
            --warn: #be4d00;
            --shadow: 0 20px 50px rgba(6, 20, 40, 0.2);
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            color: var(--ink);
            font-family: "Space Grotesk", "Trebuchet MS", sans-serif;
            min-height: 100vh;
            background:
                radial-gradient(1300px 650px at -8% -10%, rgba(244, 201, 93, 0.28), transparent 70%),
                radial-gradient(900px 500px at 110% -20%, rgba(0, 119, 182, 0.35), transparent 68%),
                linear-gradient(145deg, var(--bg-a), var(--bg-b));
        }}
        .chrome {{
            display: grid;
            grid-template-columns: 320px minmax(0, 1fr) 360px;
            gap: 16px;
            padding: 16px;
            min-height: 100vh;
            opacity: 0;
            transform: translateY(12px);
            animation: reveal 600ms ease-out forwards;
        }}
        @keyframes reveal {{
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        .panel {{
            border: 1px solid var(--line);
            border-radius: 18px;
            background: var(--panel);
            backdrop-filter: blur(6px);
            box-shadow: var(--shadow);
            overflow: hidden;
        }}
        .panel-head {{
            padding: 16px 18px;
            border-bottom: 1px solid var(--line);
            background: linear-gradient(120deg, rgba(255, 255, 255, 0.92), rgba(245, 250, 255, 0.85));
        }}
        .brand {{
            margin: 0;
            letter-spacing: 0.02em;
            font-size: 1.1rem;
            font-weight: 700;
        }}
        .muted {{
            margin: 6px 0 0;
            color: var(--ink-soft);
            font-size: 0.88rem;
        }}
        .left-wrap {{ display: flex; flex-direction: column; min-height: calc(100vh - 32px); }}
        .search-wrap {{ padding: 12px 16px; border-bottom: 1px solid var(--line); }}
        .search {{
            width: 100%;
            border-radius: 12px;
            border: 1px solid rgba(18, 35, 61, 0.22);
            padding: 10px 12px;
            font: inherit;
            background: rgba(255, 255, 255, 0.85);
            color: var(--ink);
            outline: none;
        }}
        .search:focus {{ border-color: var(--accent-alt); box-shadow: 0 0 0 3px rgba(0, 119, 182, 0.2); }}
        .page-nav {{
            padding: 8px;
            overflow: auto;
            display: grid;
            align-content: start;
            gap: 6px;
        }}
        .nav-item {{
            border: 1px solid transparent;
            background: rgba(255, 255, 255, 0.65);
            color: var(--ink);
            padding: 9px 10px;
            border-radius: 10px;
            text-align: left;
            cursor: pointer;
            transition: transform 120ms ease, background 120ms ease, border-color 120ms ease;
            font: inherit;
            font-size: 0.9rem;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .nav-item:hover {{ background: rgba(255, 255, 255, 0.95); transform: translateX(2px); border-color: rgba(0, 119, 182, 0.4); }}
        .nav-item.active {{
            background: linear-gradient(120deg, rgba(0, 119, 182, 0.15), rgba(255, 107, 53, 0.15));
            border-color: rgba(0, 119, 182, 0.6);
            font-weight: 600;
        }}
        .content {{ display: flex; flex-direction: column; min-height: calc(100vh - 32px); }}
        .content-top {{
            padding: 14px 18px;
            border-bottom: 1px solid var(--line);
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }}
        .chip {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 9px;
            border-radius: 999px;
            background: rgba(31, 157, 115, 0.16);
            color: #0d5e46;
            font-size: 0.78rem;
            font-weight: 600;
        }}
        .content-body {{
            padding: 26px;
            overflow: auto;
            background: var(--panel-strong);
        }}
        .content-body h1, .content-body h2, .content-body h3 {{ color: #0b2444; }}
        .content-body p, .content-body li {{ line-height: 1.6; }}
        .content-body a {{ color: var(--accent-alt); text-decoration-thickness: 1px; }}
        .content-body pre {{
            overflow: auto;
            background: #101827;
            color: #d8e6ff;
            padding: 12px;
            border-radius: 10px;
            border: 1px solid rgba(216, 230, 255, 0.2);
        }}
        .content-body code {{ font-family: "IBM Plex Mono", Consolas, monospace; }}
        .content-body blockquote {{ border-left: 4px solid rgba(0, 119, 182, 0.6); margin: 0; padding: 8px 14px; background: rgba(230, 244, 255, 0.5); }}
        .chat {{ display: flex; flex-direction: column; min-height: calc(100vh - 32px); }}
        .chat-log {{
            flex: 1;
            padding: 14px;
            overflow: auto;
            display: grid;
            align-content: start;
            gap: 10px;
            background: linear-gradient(180deg, rgba(243, 248, 255, 0.95), rgba(255, 247, 241, 0.9));
        }}
        .bubble {{
            border-radius: 14px;
            padding: 12px;
            font-size: 0.92rem;
            border: 1px solid rgba(13, 36, 66, 0.12);
            box-shadow: 0 5px 15px rgba(18, 35, 61, 0.08);
        }}
        .bubble.user {{ background: #ffffff; justify-self: end; max-width: 90%; }}
        .bubble.assistant {{ background: #f3faff; justify-self: start; max-width: 100%; }}
        .chat-form {{
            border-top: 1px solid var(--line);
            padding: 12px;
            display: grid;
            gap: 8px;
            background: rgba(255, 255, 255, 0.92);
        }}
        .chat-form textarea {{
            width: 100%;
            min-height: 92px;
            max-height: 220px;
            resize: vertical;
            border: 1px solid rgba(18, 35, 61, 0.2);
            border-radius: 12px;
            padding: 10px;
            font: inherit;
        }}
        .chat-form textarea:focus {{ outline: none; border-color: var(--accent-alt); box-shadow: 0 0 0 3px rgba(0, 119, 182, 0.2); }}
        .row {{ display: flex; justify-content: space-between; align-items: center; gap: 10px; }}
        .hint {{ font-size: 0.8rem; color: var(--ink-soft); }}
        .btn {{
            border: none;
            border-radius: 12px;
            background: linear-gradient(120deg, var(--accent-alt), var(--accent));
            color: white;
            padding: 10px 14px;
            cursor: pointer;
            font: inherit;
            font-weight: 600;
            transition: transform 120ms ease, filter 120ms ease;
        }}
        .btn:hover {{ transform: translateY(-1px); filter: brightness(1.03); }}
        .btn:disabled {{ opacity: 0.6; cursor: not-allowed; }}
        .status {{ font-size: 0.82rem; color: var(--warn); min-height: 1.1rem; }}
        .status.ok {{ color: var(--ok); }}
        .loader {{
            width: 20px;
            height: 20px;
            border-radius: 50%;
            border: 2px solid rgba(0, 119, 182, 0.2);
            border-top-color: rgba(0, 119, 182, 0.9);
            animation: spin 800ms linear infinite;
            display: none;
        }}
        .loading .loader {{ display: inline-block; }}
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
        @media (max-width: 1220px) {{
            .chrome {{
                grid-template-columns: 280px minmax(0, 1fr);
                grid-template-areas:
                    "nav content"
                    "chat chat";
            }}
            .left-wrap {{ grid-area: nav; min-height: 520px; }}
            .content {{ grid-area: content; min-height: 520px; }}
            .chat {{ grid-area: chat; min-height: 400px; }}
        }}
        @media (max-width: 820px) {{
            .chrome {{ grid-template-columns: 1fr; padding: 10px; }}
            .left-wrap, .content, .chat {{ min-height: auto; }}
            .content-body {{ padding: 16px; }}
        }}
  </style>
</head>
<body>
    <div class=\"chrome\" id=\"app\">
        <aside class=\"panel left-wrap\">
            <div class=\"panel-head\">
                <h1 class=\"brand\">CodeWiki Explorer</h1>
                <p class=\"muted\">Structured documentation browser</p>
            </div>
            <div class=\"search-wrap\">
                <input id=\"page-search\" class=\"search\" placeholder=\"Filter pages...\" />
            </div>
            <div class=\"page-nav\" id=\"page-nav\">{page_items}</div>
        </aside>

        <main class=\"panel content\">
            <div class=\"content-top\">
                <div>
                    <div class=\"chip\">Live wiki rendering</div>
                    <div class=\"muted\" id=\"current-page\">{escape(initial_page)}</div>
                </div>
                <a href=\"/api/pages\" target=\"_blank\" rel=\"noreferrer\">JSON page index</a>
            </div>
            <article class=\"content-body\" id=\"content\">
                <p>Select a page to begin.</p>
            </article>
        </main>

        <section class=\"panel chat\" id=\"chat-panel\">
            <div class=\"panel-head\">
                <h2 class=\"brand\">Grounded Chat</h2>
                <p class=\"muted\" id=\"chat-subtitle\">Ask implementation questions with citations.</p>
            </div>
            <div class=\"chat-log\" id=\"chat-log\">
                <div class=\"bubble assistant\">Ask a question about this repository. I will answer from the local retrieval index and wiki context.</div>
            </div>
            <form class=\"chat-form\" id=\"chat-form\">
                <textarea id=\"question\" placeholder=\"Example: Where is retry logic implemented and how does it work?\"></textarea>
                <div class=\"row\">
                    <label class=\"hint\"><input type=\"checkbox\" id=\"file-back\" /> Save answer as wiki page</label>
                    <div class=\"row\">
                        <div class=\"loader\" id=\"loader\"></div>
                        <button id=\"send\" class=\"btn\" type=\"submit\">Send</button>
                    </div>
                </div>
                <div class=\"status\" id=\"chat-status\"></div>
            </form>
        </section>
    </div>

    <script>
        mermaid.initialize({{ startOnLoad: false, theme: "base", themeVariables: {{
            primaryColor: "#ecf6ff",
            primaryTextColor: "#0b2444",
            primaryBorderColor: "#0077b6",
            lineColor: "#315b8a",
            secondaryColor: "#fef0e9",
            tertiaryColor: "#f7fbff"
        }} }});

        const state = {{
            initialPage: {initial_page!r},
            chatEnabled: {chat_flag},
        }};

        const nav = document.getElementById("page-nav");
        const content = document.getElementById("content");
        const currentPage = document.getElementById("current-page");
        const searchInput = document.getElementById("page-search");

        function activate(page) {{
            for (const el of nav.querySelectorAll(".nav-item")) {{
                el.classList.toggle("active", el.dataset.page === page);
            }}
        }}

        async function loadPage(page) {{
            currentPage.textContent = page;
            activate(page);
            content.innerHTML = "<p>Loading page...</p>";
            try {{
                const res = await fetch(`/api/page/${{encodeURIComponent(page)}}`);
                if (!res.ok) throw new Error("Page load failed");
                const data = await res.json();
                content.innerHTML = data.html;
                for (const block of content.querySelectorAll("pre code.language-mermaid")) {{
                    const host = document.createElement("div");
                    host.className = "mermaid";
                    host.textContent = block.textContent || "";
                    block.parentElement.replaceWith(host);
                }}
                mermaid.run({{ querySelector: ".mermaid" }});
            }} catch (err) {{
                content.innerHTML = `<p>Unable to load page: ${{err}}</p>`;
            }}
        }}

        nav.addEventListener("click", (event) => {{
            const target = event.target;
            if (!(target instanceof HTMLElement)) return;
            if (!target.classList.contains("nav-item")) return;
            const page = target.dataset.page;
            if (page) loadPage(page);
        }});

        searchInput.addEventListener("input", () => {{
            const term = searchInput.value.trim().toLowerCase();
            for (const el of nav.querySelectorAll(".nav-item")) {{
                const show = !term || (el.dataset.page || "").toLowerCase().includes(term);
                el.style.display = show ? "block" : "none";
            }}
        }});

        const chatPanel = document.getElementById("chat-panel");
        const chatForm = document.getElementById("chat-form");
        const chatLog = document.getElementById("chat-log");
        const question = document.getElementById("question");
        const status = document.getElementById("chat-status");
        const sendBtn = document.getElementById("send");
        const subtitle = document.getElementById("chat-subtitle");

        function addBubble(kind, html) {{
            const node = document.createElement("div");
            node.className = `bubble ${{kind}}`;
            node.innerHTML = html;
            chatLog.appendChild(node);
            chatLog.scrollTop = chatLog.scrollHeight;
        }}

        if (!state.chatEnabled) {{
            subtitle.textContent = "Chat is unavailable because runtime config was not supplied to the viewer.";
            chatForm.style.display = "none";
        }}

        chatForm?.addEventListener("submit", async (event) => {{
            event.preventDefault();
            const text = question.value.trim();
            if (!text) return;

            addBubble("user", text.replace(/</g, "&lt;").replace(/>/g, "&gt;"));
            question.value = "";
            sendBtn.disabled = true;
            chatPanel.classList.add("loading");
            status.textContent = "Thinking...";
            status.classList.remove("ok");

            try {{
                const res = await fetch("/api/chat", {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: JSON.stringify({{
                        question: text,
                        file_back: document.getElementById("file-back").checked,
                    }}),
                }});
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || "Chat request failed");
                addBubble("assistant", data.answer_html);
                status.textContent = "Answer generated";
                status.classList.add("ok");
            }} catch (err) {{
                addBubble("assistant", "<p>Unable to complete request right now.</p>");
                status.textContent = String(err);
            }} finally {{
                sendBtn.disabled = false;
                chatPanel.classList.remove("loading");
            }}
        }});

        if (state.initialPage) {{
            loadPage(state.initialPage);
        }}
    </script>
</body>
</html>
"""

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
                pages = _page_list()
                initial = "index.md" if "index.md" in pages else (pages[0] if pages else "")
                return _layout("CodeWiki Viewer", initial, pages)

        @app.get("/api/pages")
        def pages() -> dict[str, list[str]]:
                return {"pages": _page_list()}

        @app.get("/api/page/{page_path:path}")
        def page_json(page_path: str) -> dict[str, Any]:
                path = _safe_page_path(page_path)
                src = path.read_text(encoding="utf-8", errors="ignore")
                return {
                        "path": page_path,
                        "html": md.render(src),
                        "raw": src,
                }

    @app.get("/page/{page_path:path}", response_class=HTMLResponse)
    def render_page(page_path: str) -> str:
                path = _safe_page_path(page_path)
                html = md.render(path.read_text(encoding="utf-8", errors="ignore"))
                return _layout(page_path, page_path, _page_list()).replace(
                        "<p>Select a page to begin.</p>", html
                )

        @app.post("/api/chat")
        def chat(request: ChatRequest) -> dict[str, str]:
                if cfg is None:
                        raise HTTPException(status_code=503, detail="Chat is disabled in this viewer instance")

                try:
                        answer = run_chat(request.question, cfg, file_back=request.file_back)
                except Exception as exc:
                        raise HTTPException(status_code=500, detail=f"Chat failed: {exc}") from exc

                return {
                        "answer": answer,
                        "answer_html": md.render(answer),
                }

    return app
