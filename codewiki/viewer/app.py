"""FastAPI app for browsing generated wiki markdown pages."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from markdown_it import MarkdownIt


def create_app(wiki_root: Path) -> FastAPI:
    wiki_root = wiki_root.resolve()
    md = MarkdownIt("commonmark", {"html": True, "linkify": True})

    app = FastAPI(title="CodeWiki Viewer", version="0.1.0")

    def _layout(title: str, body: str) -> str:
        return f"""
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
  <title>{title}</title>
  <script src=\"https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js\"></script>
  <script>mermaid.initialize({{ startOnLoad: true }});</script>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; margin:0; background:#f5f7fb; color:#18212b; }}
    .wrap {{ max-width: 1000px; margin: 0 auto; padding: 24px; }}
    .card {{ background:white; border-radius:12px; padding:24px; box-shadow:0 6px 24px rgba(10,20,30,.08); }}
    a {{ color:#0f5ec9; text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    pre {{ overflow:auto; background:#0f1720; color:#d4e2ff; padding:12px; border-radius:8px; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    .meta {{ margin-bottom: 12px; opacity: .75; }}
  </style>
</head>
<body>
  <div class=\"wrap\">{body}</div>
</body>
</html>
"""

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        pages = sorted(
            p.relative_to(wiki_root).as_posix() for p in wiki_root.rglob("*.md") if p.is_file()
        )
        links = "\n".join(f"<li><a href='/page/{p}'>{p}</a></li>" for p in pages)
        body = (
            "<div class='card'><h1>CodeWiki Viewer</h1>"
            "<p class='meta'>Browse generated markdown pages.</p>"
            f"<ul>{links}</ul></div>"
        )
        return _layout("CodeWiki Viewer", body)

    @app.get("/page/{page_path:path}", response_class=HTMLResponse)
    def render_page(page_path: str) -> str:
        path = (wiki_root / page_path).resolve()
        if not path.exists() or not path.is_file() or wiki_root not in path.parents:
            raise HTTPException(status_code=404, detail="Page not found")

        src = path.read_text(encoding="utf-8", errors="ignore")
        html = md.render(src)
        body = (
            "<div class='card'>"
            f"<p class='meta'><a href='/'>← index</a> · {page_path}</p>"
            f"{html}</div>"
        )
        return _layout(page_path, body)

    return app
