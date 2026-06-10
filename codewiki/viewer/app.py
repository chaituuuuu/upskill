"""FastAPI app for browsing generated wiki markdown pages."""

from __future__ import annotations

import json
import re
from collections import defaultdict
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


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"<h([23])([^>]*)>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub("", html)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _inject_heading_ids(html: str) -> str:
    def replacer(m: re.Match) -> str:  # type: ignore[type-arg]
        level, attrs, inner = m.group(1), m.group(2), m.group(3)
        if "id=" in attrs:
            return m.group(0)
        text = _strip_tags(inner).strip()
        return f'<h{level}{attrs} id="{_slug(text)}">{inner}</h{level}>'
    return _HEADING_RE.sub(replacer, html)


# ---------------------------------------------------------------------------
# Navigation tree helpers
# ---------------------------------------------------------------------------

def _group_pages(pages: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for page in pages:
        parts = page.split("/")
        folder = "/".join(parts[:-1]) if len(parts) > 1 else ""
        groups[folder].append(page)
    return dict(sorted(groups.items()))


def _pretty_name(page: str) -> str:
    name = page.split("/")[-1].removesuffix(".md")
    return re.sub(r"[-_]", " ", name).title()


def _render_nav_tree(groups: dict[str, list[str]]) -> str:
    parts: list[str] = []
    for page in sorted(groups.get("", [])):
        parts.append(
            f"<button class='nav-item' data-page='{escape(page)}'>"
            f"<span class='ni-icon' aria-hidden='true'>&#9670;</span>"
            f"<span class='ni-label'>{escape(_pretty_name(page))}</span>"
            f"</button>"
        )
    for folder in sorted(k for k in groups if k):
        label = escape(folder.replace("/", " \u203a "))
        items = "".join(
            f"<button class='nav-item' data-page='{escape(p)}'>"
            f"<span class='ni-icon' aria-hidden='true'>\u00b7</span>"
            f"<span class='ni-label'>{escape(_pretty_name(p))}</span>"
            f"</button>"
            for p in sorted(groups[folder])
        )
        parts.append(
            f"<details class='nav-folder' open>"
            f"<summary class='nf-label'>{label}</summary>"
            f"<div class='nf-items'>{items}</div>"
            f"</details>"
        )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main app factory
# ---------------------------------------------------------------------------

def create_app(wiki_root: Path, cfg: CodeWikiConfig | None = None) -> FastAPI:
    wiki_root = wiki_root.resolve()
    md = MarkdownIt("commonmark", {"html": True, "linkify": True})
    app = FastAPI(title="CodeWiki Viewer", version="0.1.0")

    def _page_list() -> list[str]:
        return sorted(
            p.relative_to(wiki_root).as_posix()
            for p in wiki_root.rglob("*.md")
            if p.is_file()
        )

    def _safe_path(page_path: str) -> Path:
        path = (wiki_root / page_path).resolve()
        if not path.is_file() or wiki_root not in path.parents:
            raise HTTPException(404, "Page not found")
        return path

    def _render(src: str) -> str:
        return _inject_heading_ids(md.render(src))

    def _shell(pages: list[str], initial_page: str = "") -> str:
        tree_html = _render_nav_tree(_group_pages(pages))
        pages_json = json.dumps(pages)
        chat_enabled_js = "true" if cfg is not None else "false"
        dot_class = "chat-dot off" if cfg is None else "chat-dot"
        initial_page_js = json.dumps(initial_page)
        return _build_shell(tree_html, pages_json, chat_enabled_js, dot_class, initial_page_js)

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        pages   = _page_list()
        initial = "index.md" if "index.md" in pages else (pages[0] if pages else "")
        return _shell(pages, initial)

    @app.get("/api/pages")
    def api_pages() -> dict[str, list[str]]:
        return {"pages": _page_list()}

    @app.get("/api/page/{page_path:path}")
    def api_page(page_path: str) -> dict[str, Any]:
        path = _safe_path(page_path)
        src  = path.read_text(encoding="utf-8", errors="ignore")
        return {"path": page_path, "html": _render(src), "raw": src}

    @app.get("/page/{page_path:path}", response_class=HTMLResponse)
    def legacy_page(page_path: str) -> str:
        pages   = _page_list()
        initial = "index.md" if "index.md" in pages else (pages[0] if pages else "")
        shell   = _shell(pages, initial)
        boot    = f"<script>setTimeout(()=>loadPage({json.dumps(page_path)}),20);</script>"
        return shell.replace("</body>", boot + "</body>")

    @app.post("/api/chat")
    def api_chat(request: ChatRequest) -> dict[str, str]:
        if cfg is None:
            raise HTTPException(503, "Chat is disabled: viewer started without runtime config")
        try:
            answer = run_chat(request.question, cfg, file_back=request.file_back)
        except Exception as exc:
            raise HTTPException(500, f"Chat failed: {exc}") from exc
        return {"answer": answer, "answer_html": md.render(answer)}

    return app


# ---------------------------------------------------------------------------
# Shell template (extracted to avoid huge f-string inside closure)
# ---------------------------------------------------------------------------

def _build_shell(
    tree_html: str,
    pages_json: str,
    chat_enabled_js: str,
    dot_class: str,
    initial_page_js: str,
) -> str:
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>CodeWiki</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Inter:ital,wght@0,400;0,500;0,600;0,700;1,400&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet"/>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --sidebar-w:256px;--right-w:296px;--header-h:50px;
  --bg:#0d1117;--bg2:#161b22;--bg3:#1c2230;
  --border:rgba(255,255,255,.09);
  --text:#e6edf3;--text2:#8b949e;--text3:#4a5568;
  --accent:#3b82f6;--accent2:#f97316;--green:#3fb950;--red:#f85149;
  --surface:#1c2433;--surface2:#222d3e;
}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--text);font-family:"Inter",system-ui,sans-serif;height:100vh;overflow:hidden;display:flex;flex-direction:column;font-size:14px;line-height:1.5}
/* header */
.hdr{height:var(--header-h);min-height:var(--header-h);background:var(--bg2);border-bottom:1px solid var(--border);display:flex;align-items:center;padding:0 16px;gap:10px;z-index:100}
.hdr-logo{font-weight:700;font-size:.95rem;letter-spacing:.03em;color:var(--text);display:flex;align-items:center;gap:8px;text-decoration:none}
.hdr-logo .la{color:var(--accent)}
.hdr-spacer{flex:1}
.hdr-btn{display:flex;align-items:center;gap:7px;color:var(--text2);font:inherit;font-size:.82rem;cursor:pointer;border:1px solid var(--border);padding:5px 10px;border-radius:7px;background:var(--bg3);transition:border-color 150ms,color 150ms}
.hdr-btn:hover{border-color:var(--accent);color:var(--text)}
.kbd{background:var(--surface2);border:1px solid var(--border);padding:1px 5px;border-radius:4px;font-size:.74rem;font-family:"JetBrains Mono",monospace;color:var(--text3)}
/* layout */
.layout{display:flex;flex:1;overflow:hidden}
/* sidebar */
.sidebar{width:var(--sidebar-w);min-width:var(--sidebar-w);background:var(--bg2);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
.sb-sw{padding:9px 10px;border-bottom:1px solid var(--border)}
.sb-search{width:100%;background:var(--bg3);border:1px solid var(--border);border-radius:7px;padding:7px 10px;color:var(--text);font:inherit;font-size:.83rem;outline:none}
.sb-search::placeholder{color:var(--text3)}
.sb-search:focus{border-color:var(--accent)}
.sb-tree{flex:1;overflow-y:auto;padding:6px}
.sb-tree::-webkit-scrollbar{width:3px}
.sb-tree::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
/* nav folder */
.nav-folder{margin-bottom:4px}
details.nav-folder>summary{list-style:none}
details.nav-folder>summary::-webkit-details-marker{display:none}
.nf-label{display:flex;align-items:center;gap:5px;padding:4px 6px;color:var(--text3);font-size:.72rem;font-weight:600;letter-spacing:.07em;text-transform:uppercase;cursor:pointer;border-radius:5px;user-select:none}
.nf-label::before{content:"▾";margin-right:2px;font-size:.65rem;transition:transform 200ms}
details.nav-folder:not([open]) .nf-label::before{transform:rotate(-90deg)}
.nf-label:hover{color:var(--text2);background:var(--bg3)}
.nf-items{padding-left:10px}
/* nav items */
.nav-item{width:100%;display:flex;align-items:center;gap:6px;padding:5px 7px;background:none;border:none;color:var(--text2);font:inherit;font-size:.85rem;text-align:left;cursor:pointer;border-radius:6px;transition:background 100ms,color 100ms;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.nav-item:hover{background:var(--bg3);color:var(--text)}
.nav-item.active{background:rgba(59,130,246,.15);color:#93c5fd;font-weight:500}
.ni-icon{font-size:.68rem;opacity:.4;flex-shrink:0}
.nav-item.active .ni-icon{opacity:1;color:var(--accent)}
.ni-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* content */
.content-col{flex:1;min-width:0;overflow-y:auto;background:var(--bg);display:flex;flex-direction:column}
.content-col::-webkit-scrollbar{width:5px}
.content-col::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px}
/* breadcrumb */
.breadcrumb{position:sticky;top:0;z-index:10;background:rgba(13,17,23,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--border);padding:8px 28px;display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:.79rem;color:var(--text3);flex-shrink:0}
.crumb{display:flex;align-items:center;gap:5px;flex-wrap:wrap;min-width:0}
.crumb-part{color:var(--text2)}
.crumb-sep{color:var(--text3)}
.crumb-active{color:var(--text);font-weight:500}
.bc-actions{display:flex;gap:7px;flex-shrink:0}
.icon-btn{background:var(--bg3);border:1px solid var(--border);color:var(--text2);padding:3px 8px;border-radius:5px;cursor:pointer;font:inherit;font-size:.75rem;transition:border-color 120ms,color 120ms;white-space:nowrap}
.icon-btn:hover{border-color:var(--accent);color:var(--text)}
/* article */
.article{max-width:800px;width:100%;margin:0 auto;padding:40px 32px 100px}
.article h1{font-size:1.85rem;font-weight:700;color:#f0f6ff;margin-bottom:14px;line-height:1.2;padding-bottom:12px;border-bottom:1px solid var(--border)}
.article h2{font-size:1.22rem;font-weight:600;color:#cdd9ee;margin:32px 0 10px;padding-bottom:7px;border-bottom:1px solid var(--border)}
.article h3{font-size:1.04rem;font-weight:600;color:#b0c4de;margin:22px 0 8px}
.article h4{font-size:.92rem;font-weight:600;color:var(--text2);margin:16px 0 5px}
.article p{color:var(--text);margin-bottom:12px;line-height:1.75}
.article ul,.article ol{padding-left:22px;margin-bottom:14px}
.article li{margin-bottom:5px;line-height:1.7;color:var(--text)}
.article a{color:#60a5fa;text-underline-offset:2px;text-decoration-thickness:1px}
.article a:hover{color:#93c5fd}
.article blockquote{border-left:3px solid var(--accent);padding:10px 16px;margin:16px 0;background:rgba(59,130,246,.07);border-radius:0 7px 7px 0;color:var(--text2)}
.article table{width:100%;border-collapse:collapse;margin:20px 0;font-size:.87rem;overflow-x:auto;display:block}
.article th{background:var(--surface);padding:8px 12px;text-align:left;color:var(--text);border:1px solid var(--border);font-weight:600;white-space:nowrap}
.article td{padding:7px 12px;border:1px solid var(--border);color:var(--text2)}
.article tr:nth-child(even) td{background:rgba(255,255,255,.02)}
.article pre{background:var(--surface)!important;border:1px solid var(--border);border-radius:9px;padding:15px;overflow-x:auto;margin:16px 0}
.article pre code{background:none!important;padding:0!important;font-family:"JetBrains Mono",Consolas,monospace;font-size:.83rem;line-height:1.6}
.article code{background:rgba(110,118,129,.15);padding:2px 6px;border-radius:4px;font-size:.86em;font-family:"JetBrains Mono",Consolas,monospace;color:#ffa8c4}
.article pre code{color:inherit}
.article hr{border:none;border-top:1px solid var(--border);margin:28px 0}
.article img{max-width:100%;border-radius:8px;border:1px solid var(--border)}
.article .mermaid{background:var(--surface);border-radius:8px;padding:16px;margin:16px 0;border:1px solid var(--border)}
/* right col */
.right-col{width:var(--right-w);min-width:var(--right-w);background:var(--bg2);border-left:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
/* toc */
.toc-panel{border-bottom:1px solid var(--border);display:flex;flex-direction:column;max-height:42%;min-height:64px;overflow:hidden}
.panel-head{padding:8px 12px;font-size:.7rem;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--text3);border-bottom:1px solid var(--border);flex-shrink:0;display:flex;align-items:center;justify-content:space-between}
.toc-list{overflow-y:auto;padding:5px}
.toc-list::-webkit-scrollbar{width:3px}
.toc-list::-webkit-scrollbar-thumb{background:var(--border)}
.toc-item{display:block;padding:4px 7px;font-size:.8rem;color:var(--text2);text-decoration:none;border-radius:5px;border-left:2px solid transparent;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;transition:background 100ms,border-color 100ms,color 100ms}
.toc-item:hover{background:var(--bg3);color:var(--text);border-left-color:var(--accent)}
.toc-item.h3{padding-left:18px;font-size:.76rem;color:var(--text3)}
.toc-empty{padding:10px 12px;font-size:.79rem;color:var(--text3)}
/* chat */
.chat-panel{flex:1;display:flex;flex-direction:column;overflow:hidden;min-height:0}
.chat-head-row{display:flex;align-items:center;gap:7px}
.chat-dot{width:6px;height:6px;border-radius:50%;background:var(--green);box-shadow:0 0 5px var(--green)}
.chat-dot.off{background:var(--text3);box-shadow:none}
.chat-log{flex:1;overflow-y:auto;padding:10px;display:flex;flex-direction:column;gap:8px;min-height:0}
.chat-log::-webkit-scrollbar{width:3px}
.chat-log::-webkit-scrollbar-thumb{background:var(--border)}
.msg{border-radius:9px;padding:9px 11px;font-size:.82rem;line-height:1.6;border:1px solid var(--border);word-break:break-word}
.msg.user{background:rgba(59,130,246,.12);align-self:flex-end;max-width:92%;border-color:rgba(59,130,246,.28);color:#bfdbfe}
.msg.bot{background:var(--surface);align-self:flex-start;max-width:100%;color:var(--text)}
.msg.bot p{margin-bottom:6px;line-height:1.55}
.msg.bot p:last-child{margin-bottom:0}
.msg.bot code{font-size:.78em}
.chat-form-wrap{padding:9px;border-top:1px solid var(--border);flex-shrink:0}
.chat-input{width:100%;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:7px;padding:7px 9px;font:inherit;font-size:.82rem;resize:none;outline:none;min-height:56px;max-height:140px}
.chat-input::placeholder{color:var(--text3)}
.chat-input:focus{border-color:var(--accent)}
.chat-footer{display:flex;align-items:center;justify-content:space-between;margin-top:7px;gap:6px}
.chat-hint{font-size:.72rem;color:var(--text3)}
.send-btn{background:var(--accent);color:#fff;border:none;border-radius:6px;padding:5px 13px;font:inherit;font-size:.8rem;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:4px;transition:filter 120ms,transform 80ms}
.send-btn:hover{filter:brightness(1.12)}
.send-btn:active{transform:scale(.97)}
.send-btn:disabled{opacity:.5;cursor:not-allowed}
.file-back-label{font-size:.73rem;color:var(--text3);display:flex;align-items:center;gap:4px;cursor:pointer}
.file-back-label:hover{color:var(--text2)}
.typing-dot{width:5px;height:5px;border-radius:50%;background:var(--accent);animation:tdot 1s infinite ease-in-out;display:inline-block;margin:0 1px}
.typing-dot:nth-child(2){animation-delay:.15s}
.typing-dot:nth-child(3){animation-delay:.3s}
@keyframes tdot{0%,80%,100%{transform:scale(.6);opacity:.5}40%{transform:scale(1);opacity:1}}
/* quick jumper */
.overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);backdrop-filter:blur(4px);display:flex;align-items:flex-start;justify-content:center;padding-top:16vh;z-index:1000;opacity:0;pointer-events:none;transition:opacity 150ms}
.overlay.open{opacity:1;pointer-events:auto}
.jumper{background:var(--bg2);border:1px solid rgba(59,130,246,.45);border-radius:13px;width:540px;max-width:90vw;box-shadow:0 30px 90px rgba(0,0,0,.65);overflow:hidden}
.jumper-input{width:100%;background:transparent;border:none;padding:15px 18px;font:inherit;font-size:.97rem;color:var(--text);outline:none;border-bottom:1px solid var(--border)}
.jumper-input::placeholder{color:var(--text3)}
.jumper-results{max-height:310px;overflow-y:auto;padding:5px}
.j-item{display:flex;align-items:center;gap:9px;padding:8px 11px;border-radius:7px;cursor:pointer;font-size:.88rem;color:var(--text2);transition:background 80ms,color 80ms}
.j-item:hover,.j-item.sel{background:rgba(59,130,246,.15);color:var(--text)}
.j-name{font-weight:500}
.j-path{font-size:.76rem;color:var(--text3);margin-left:auto;font-family:"JetBrains Mono",monospace}
/* skeleton */
.skel{background:linear-gradient(90deg,var(--surface) 25%,var(--surface2) 50%,var(--surface) 75%);background-size:200% 100%;border-radius:5px;animation:shim 1.5s infinite}
@keyframes shim{0%{background-position:200% 0}100%{background-position:-200% 0}}
/* responsive */
@media(max-width:1080px){:root{--right-w:260px}}
@media(max-width:880px){.right-col{display:none}}
@media(max-width:640px){.sidebar{display:none}.article{padding:20px 14px 60px}.breadcrumb{padding:7px 14px}}
</style>
</head>
<body>
<header class="hdr">
  <a class="hdr-logo" href="/">Code<span class="la">Wiki</span></a>
  <div class="hdr-spacer"></div>
  <button class="hdr-btn" id="open-jumper" title="Quick jump (Ctrl+K)">
    <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
    Search pages <kbd class="kbd">&#8963;K</kbd>
  </button>
</header>
<div class="layout">
  <nav class="sidebar" aria-label="Wiki pages">
    <div class="sb-sw"><input class="sb-search" id="sb-search" placeholder="Filter pages&#8230;" autocomplete="off" aria-label="Filter pages"/></div>
    <div class="sb-tree" id="sb-tree">""" + tree_html + """</div>
  </nav>
  <div class="content-col" id="content-col">
    <div class="breadcrumb" id="breadcrumb">
      <div class="crumb" id="crumb-items" aria-live="polite"></div>
      <div class="bc-actions"><button class="icon-btn" id="copy-md-btn">&#8856; Copy MD</button></div>
    </div>
    <article class="article" id="article" aria-live="polite">
      <div style="padding:64px 0;text-align:center;color:var(--text3)">
        <div style="font-size:2.4rem;margin-bottom:14px">&#128218;</div>
        <div style="font-size:1.05rem;font-weight:600;color:var(--text2);margin-bottom:8px">Select a page to get started</div>
        <div style="font-size:.83rem">Use the sidebar or press <kbd class="kbd">&#8963;K</kbd> to search all pages</div>
      </div>
    </article>
  </div>
  <aside class="right-col" aria-label="Table of contents and chat">
    <div class="toc-panel">
      <div class="panel-head"><span>On this page</span></div>
      <div class="toc-list" id="toc-list"><div class="toc-empty">No headings yet</div></div>
    </div>
    <div class="chat-panel">
      <div class="panel-head">
        <div class="chat-head-row">
          <span class=\"""" + dot_class + """\" id="chat-dot" aria-hidden="true"></span>
          <span>Grounded Chat</span>
        </div>
      </div>
      <div class="chat-log" id="chat-log">
        <div class="msg bot">Ask anything about this codebase &#8212; I answer from the code index and wiki context.</div>
      </div>
      <div id="chat-form-wrap">
        <div class="chat-form-wrap">
          <textarea class="chat-input" id="chat-input" placeholder="e.g. Where is the retry logic?" rows="3" aria-label="Chat question"></textarea>
          <div class="chat-footer">
            <label class="file-back-label" title="Save this answer as a wiki page"><input type="checkbox" id="file-back"/> Save to wiki</label>
            <div style="display:flex;align-items:center;gap:6px">
              <span class="chat-hint"><kbd class="kbd">&#8984;</kbd><kbd class="kbd">&#8629;</kbd></span>
              <button class="send-btn" id="send-btn" type="button">Send</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </aside>
</div>
<div class="overlay" id="overlay" role="dialog" aria-modal="true" aria-label="Quick jump to page">
  <div class="jumper">
    <input class="jumper-input" id="jumper-input" placeholder="Jump to page&#8230;" autocomplete="off" aria-label="Search pages"/>
    <div class="jumper-results" id="jumper-results" role="listbox"></div>
  </div>
</div>
<script>
mermaid.initialize({startOnLoad:false,theme:"dark",themeVariables:{background:"#1c2433",primaryColor:"#1e3a5f",primaryTextColor:"#d4e4ff",primaryBorderColor:"#3b82f6",lineColor:"#5a8fca",secondaryColor:"#2a1f3d",tertiaryColor:"#1c2433"}});
const ALL_PAGES=""" + pages_json + """;
const CHAT_ENABLED=""" + chat_enabled_js + """;
const INITIAL_PAGE=""" + initial_page_js + """;
let currentPage='',currentRaw='';
function getHashPage(){const h=location.hash;return h.startsWith('#/')?decodeURIComponent(h.slice(2)):''}
function setHashPage(p){history.pushState(null,'','#/'+encodeURIComponent(p))}
window.addEventListener('popstate',()=>{const p=getHashPage();if(p)loadPage(p,false)});
document.getElementById('sb-search').addEventListener('input',function(){
  const term=this.value.trim().toLowerCase(),tree=document.getElementById('sb-tree');
  for(const btn of tree.querySelectorAll('.nav-item')){btn.style.display=!term||(btn.dataset.page||'').toLowerCase().includes(term)?'':'none'}
  for(const f of tree.querySelectorAll('.nav-folder')){
    const any=[...f.querySelectorAll('.nav-item')].some(b=>b.style.display!=='none');
    f.style.display=any?'':'none';if(term)f.open=true;
  }
});
document.getElementById('sb-tree').addEventListener('click',e=>{
  const btn=e.target.closest('.nav-item');if(!btn)return;
  const p=btn.dataset.page;if(p){setHashPage(p);loadPage(p,false)}
});
function activateNav(page){
  for(const btn of document.querySelectorAll('#sb-tree .nav-item')){
    const active=btn.dataset.page===page;btn.classList.toggle('active',active);
    if(active){const f=btn.closest('details.nav-folder');if(f)f.open=true;btn.scrollIntoView({block:'nearest'})}
  }
}
function setBreadcrumb(page){
  const parts=page.split('/');
  let html='';
  parts.forEach((part,i)=>{
    if(i>0)html+='<span class="crumb-sep"> \u203a </span>';
    const label=i===parts.length-1?part.replace(/\\.md$/,''):part;
    html+=i===parts.length-1?`<span class="crumb-active">${label}</span>`:`<span class="crumb-part">${label}</span>`;
  });
  document.getElementById('crumb-items').innerHTML=html;
}
function buildToc(articleEl){
  const toc=document.getElementById('toc-list');
  const headings=[...articleEl.querySelectorAll('h2[id],h3[id]')];
  if(!headings.length){toc.innerHTML='<div class="toc-empty">No headings</div>';return}
  toc.innerHTML=headings.map(h=>`<a class="toc-item ${h.tagName.toLowerCase()}" href="#${h.id}">${h.textContent}</a>`).join('');
}
function resolveRelative(dir,file){
  const base=dir?dir.split('/'):[],parts=file.split('/');
  for(const p of parts){if(p==='..')base.pop();else if(p!=='.')base.push(p)}
  return base.join('/');
}
function interceptLinks(articleEl,pagePath){
  for(const a of articleEl.querySelectorAll('a[href]')){
    const href=a.getAttribute('href')||'';
    if(href.startsWith('http')||href.startsWith('mailto:'))continue;
    const mdPart=href.includes('.md#')?href.split('.md#')[0]+'.md':href;
    const anchor=href.includes('.md#')?href.split('.md#')[1]:'';
    if(!mdPart.endsWith('.md'))continue;
    a.addEventListener('click',e=>{
      e.preventDefault();
      const dir=pagePath.split('/').slice(0,-1).join('/');
      const resolved=resolveRelative(dir,mdPart);
      setHashPage(resolved);
      loadPage(resolved,false).then(()=>{
        if(anchor)setTimeout(()=>{const el=document.getElementById(anchor);if(el)el.scrollIntoView({behavior:'smooth',block:'start'})},80);
      });
    });
  }
}
async function loadPage(page,updateHash=true){
  if(updateHash)setHashPage(page);
  currentPage=page;activateNav(page);setBreadcrumb(page);
  const article=document.getElementById('article');
  article.innerHTML=`<div style="padding:28px 0">
    <div class="skel" style="height:26px;width:52%;margin-bottom:14px"></div>
    <div class="skel" style="height:14px;width:84%;margin-bottom:9px"></div>
    <div class="skel" style="height:14px;width:68%;margin-bottom:9px"></div>
    <div class="skel" style="height:14px;width:76%;margin-bottom:22px"></div>
    <div class="skel" style="height:18px;width:38%;margin-bottom:12px"></div>
    <div class="skel" style="height:14px;width:90%;margin-bottom:9px"></div>
    <div class="skel" style="height:14px;width:62%"></div></div>`;
  try{
    const res=await fetch('/api/page/'+encodeURIComponent(page));
    if(!res.ok)throw new Error('HTTP '+res.status);
    const data=await res.json();
    currentRaw=data.raw||'';
    article.innerHTML=data.html;
    for(const block of article.querySelectorAll('pre code')){
      if(!block.className.includes('language-mermaid'))hljs.highlightElement(block);
    }
    for(const block of article.querySelectorAll('pre code.language-mermaid')){
      const host=document.createElement('div');host.className='mermaid';host.textContent=block.textContent||'';
      block.closest('pre').replaceWith(host);
    }
    mermaid.run({querySelector:'.mermaid'});
    buildToc(article);interceptLinks(article,page);
    document.getElementById('content-col').scrollTo({top:0,behavior:'instant'});
  }catch(err){
    article.innerHTML=`<p style="color:var(--red);padding:16px 0">Failed to load <strong>${page}</strong>: ${err}</p>`;
  }
}
document.getElementById('copy-md-btn').addEventListener('click',async()=>{
  if(!currentRaw)return;
  try{await navigator.clipboard.writeText(currentRaw)}catch(_){}
  const btn=document.getElementById('copy-md-btn'),old=btn.textContent;
  btn.textContent='\u2713 Copied';setTimeout(()=>{btn.textContent=old},1800);
});
const overlay=document.getElementById('overlay'),jumperInput=document.getElementById('jumper-input'),jumperResults=document.getElementById('jumper-results');
let jSel=-1;
function openJumper(){overlay.classList.add('open');jumperInput.value='';jSel=-1;renderJumper('');requestAnimationFrame(()=>jumperInput.focus())}
function closeJumper(){overlay.classList.remove('open')}
function renderJumper(term){
  const lower=term.toLowerCase();
  const filtered=term?ALL_PAGES.filter(p=>p.toLowerCase().includes(lower)).slice(0,14):ALL_PAGES.slice(0,14);
  jSel=-1;
  if(!filtered.length){jumperResults.innerHTML='<div style="padding:12px;text-align:center;color:var(--text3);font-size:.83rem">No pages match</div>';return}
  jumperResults.innerHTML=filtered.map((p,i)=>{
    const name=p.split('/').pop().replace(/\\.md$/,''),dir=p.split('/').slice(0,-1).join('/');
    return `<div class="j-item" data-page="${p}" data-i="${i}" role="option"><span class="j-name">&#128196; ${name}</span>${dir?`<span class="j-path">${dir}/</span>`:''}</div>`;
  }).join('');
  for(const item of jumperResults.querySelectorAll('.j-item')){
    item.addEventListener('click',()=>{const p=item.dataset.page;if(p){setHashPage(p);loadPage(p,false);closeJumper()}});
  }
}
jumperInput.addEventListener('input',()=>renderJumper(jumperInput.value.trim()));
jumperInput.addEventListener('keydown',e=>{
  const items=[...jumperResults.querySelectorAll('.j-item')];
  if(e.key==='ArrowDown'){e.preventDefault();jSel=Math.min(jSel+1,items.length-1);items.forEach((el,i)=>el.classList.toggle('sel',i===jSel));if(items[jSel])items[jSel].scrollIntoView({block:'nearest'})}
  else if(e.key==='ArrowUp'){e.preventDefault();jSel=Math.max(jSel-1,-1);items.forEach((el,i)=>el.classList.toggle('sel',i===jSel))}
  else if(e.key==='Enter'){const c=jSel>=0?items[jSel]:items[0];if(c){const p=c.dataset.page;if(p){setHashPage(p);loadPage(p,false);closeJumper()}}}
  else if(e.key==='Escape')closeJumper();
});
overlay.addEventListener('click',e=>{if(e.target===overlay)closeJumper()});
document.getElementById('open-jumper').addEventListener('click',openJumper);
document.addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key==='k'){e.preventDefault();openJumper()}
  if(e.key==='Escape'&&overlay.classList.contains('open'))closeJumper();
});
const chatLog=document.getElementById('chat-log'),chatInput=document.getElementById('chat-input'),sendBtn=document.getElementById('send-btn');
if(!CHAT_ENABLED){
  document.getElementById('chat-dot').classList.add('off');
  document.getElementById('chat-form-wrap').innerHTML='<p style="padding:10px 12px;font-size:.78rem;color:var(--text3)">Chat unavailable: viewer started without runtime config.</p>';
}
function addMsg(kind,html){
  const el=document.createElement('div');el.className='msg '+kind;el.innerHTML=html;
  chatLog.appendChild(el);chatLog.scrollTop=chatLog.scrollHeight;return el;
}
async function sendChat(){
  const text=chatInput.value.trim();if(!text||!CHAT_ENABLED)return;
  addMsg('user',text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'));
  chatInput.value='';sendBtn.disabled=true;
  const thinking=addMsg('bot','<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>');
  try{
    const res=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:text,file_back:document.getElementById('file-back').checked})});
    const data=await res.json();if(!res.ok)throw new Error(data.detail||'Chat error');
    thinking.innerHTML=data.answer_html;
  }catch(err){thinking.innerHTML=`<span style="color:var(--red)">Error: ${err}</span>`}
  finally{sendBtn.disabled=false;chatLog.scrollTop=chatLog.scrollHeight}
}
sendBtn.addEventListener('click',sendChat);
chatInput.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();sendChat()}});
const startPage=getHashPage()||INITIAL_PAGE;
if(startPage)loadPage(startPage,!getHashPage());
</script>
</body>
</html>"""
