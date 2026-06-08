"""BM25 index store using Whoosh with a simple fallback when unavailable."""

from __future__ import annotations

import json
from pathlib import Path

from codewiki.models import Snippet

try:
    from whoosh import index
    from whoosh.fields import ID, STORED, TEXT, Schema
    from whoosh.qparser import MultifieldParser
except Exception:  # pragma: no cover - optional runtime fallback
    index = None  # type: ignore[assignment]


class IndexStore:
    def __init__(self, index_dir: Path) -> None:
        self.index_dir = Path(index_dir)
        self._fallback_file = self.index_dir / "snippets.json"

    def build(self, snippets: list[Snippet]) -> None:
        self.index_dir.mkdir(parents=True, exist_ok=True)

        # Always persist snippet payloads for fallback and vector indexing.
        self._fallback_file.write_text(
            json.dumps([s.__dict__ for s in snippets], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if index is None:
            return

        schema = Schema(
            cite=ID(stored=True, unique=True),
            path=ID(stored=True),
            symbol=TEXT(stored=True),
            lang=ID(stored=True),
            body=TEXT(stored=True),
            meta=STORED,
        )

        if index.exists_in(self.index_dir):
            ix = index.open_dir(self.index_dir)
        else:
            ix = index.create_in(self.index_dir, schema)

        writer = ix.writer()
        for s in snippets:
            writer.update_document(
                cite=s.cite,
                path=s.metadata.get("path", ""),
                symbol=s.metadata.get("symbol", ""),
                lang=s.metadata.get("lang", "text"),
                body=s.text,
                meta=s.metadata,
            )
        writer.commit()

    def load_snippets(self) -> list[Snippet]:
        if not self._fallback_file.exists():
            return []

        try:
            data = json.loads(self._fallback_file.read_text(encoding="utf-8"))
        except Exception:
            return []

        out: list[Snippet] = []
        if not isinstance(data, list):
            return out

        for item in data:
            if not isinstance(item, dict):
                continue
            out.append(
                Snippet(
                    text=str(item.get("text", "")),
                    cite=str(item.get("cite", "")),
                    score=float(item.get("score", 0.0) or 0.0),
                    metadata=item.get("metadata", {}) if isinstance(item.get("metadata", {}), dict) else {},
                )
            )
        return out

    def search(self, query: str, top_k: int = 8) -> list[Snippet]:
        if index is None:
            if not self._fallback_file.exists():
                return []
            data = json.loads(self._fallback_file.read_text(encoding="utf-8"))
            ranked: list[Snippet] = []
            q = query.lower()
            for item in data:
                text = item.get("text", "")
                score = float(text.lower().count(q))
                if score > 0:
                    ranked.append(
                        Snippet(
                            text=text,
                            cite=item.get("cite", ""),
                            score=score,
                            metadata=item.get("metadata", {}),
                        )
                    )
            ranked.sort(key=lambda s: s.score, reverse=True)
            return ranked[:top_k]

        if not index.exists_in(self.index_dir):
            return []

        ix = index.open_dir(self.index_dir)
        parser = MultifieldParser(["body", "path", "symbol"], schema=ix.schema)
        q = parser.parse(query)
        out: list[Snippet] = []
        with ix.searcher() as searcher:
            results = searcher.search(q, limit=top_k)
            for hit in results:
                out.append(
                    Snippet(
                        text=hit.get("body", ""),
                        cite=hit.get("cite", ""),
                        score=float(hit.score),
                        metadata={
                            "path": hit.get("path", ""),
                            "symbol": hit.get("symbol", ""),
                            "lang": hit.get("lang", "text"),
                        },
                    )
                )
        return out
