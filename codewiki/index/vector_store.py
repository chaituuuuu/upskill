"""Vector index over snippets with optional FAISS backend and local caching."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from codewiki.config import CodeWikiConfig
from codewiki.llm.budget import Budget
from codewiki.llm.client import LLMClient
from codewiki.llm.retry import with_retry
from codewiki.models import Snippet

try:  # pragma: no cover - optional dependency
    import numpy as np
except Exception:  # pragma: no cover - optional dependency
    np = None  # type: ignore[assignment]

try:  # pragma: no cover - optional dependency
    import faiss
except Exception:  # pragma: no cover - optional dependency
    faiss = None  # type: ignore[assignment]


class VectorStore:
    """Persistent snippet embedding index used by hybrid retrieval."""

    def __init__(self, cache_dir: Path, *, backend: str = "faiss") -> None:
        self._root = Path(cache_dir) / "vectors"
        self._backend = backend
        self._meta_path = self._root / "meta.json"
        self._vectors_json = self._root / "vectors.json"
        self._vectors_npy = self._root / "vectors.npy"
        self._faiss_path = self._root / "index.faiss"

    async def search(
        self,
        *,
        cfg: CodeWikiConfig,
        snippets: list[Snippet],
        query: str,
        top_k: int,
        budget: Budget | None = None,
    ) -> list[Snippet]:
        if not snippets or not cfg.llm.embedding_model:
            return []

        vectors, cites = await self._ensure_vectors(cfg=cfg, snippets=snippets, budget=budget)
        if not vectors or not cites:
            return []

        query_vec = await self._embed_query(cfg=cfg, query=query, budget=budget)
        if not query_vec:
            return []

        idx_scores = self._rank(vectors=vectors, query_vec=query_vec, top_k=top_k)
        by_cite = {item.cite: item for item in snippets}

        out: list[Snippet] = []
        for idx, score in idx_scores:
            if idx < 0 or idx >= len(cites):
                continue
            source = by_cite.get(cites[idx])
            if source is None:
                continue
            out.append(
                Snippet(
                    text=source.text,
                    cite=source.cite,
                    score=float(score),
                    metadata=source.metadata,
                )
            )
        return out

    async def _ensure_vectors(
        self,
        *,
        cfg: CodeWikiConfig,
        snippets: list[Snippet],
        budget: Budget | None,
    ) -> tuple[list[list[float]], list[str]]:
        self._root.mkdir(parents=True, exist_ok=True)

        model = cfg.llm.embedding_model or ""
        signature = _signature(snippets, model)

        meta = self._load_meta()
        if (
            meta.get("signature") == signature
            and meta.get("model") == model
            and isinstance(meta.get("cites"), list)
        ):
            cites = [str(item) for item in meta.get("cites", [])]
            vectors = self._load_vectors()
            if vectors and len(vectors) == len(cites):
                return vectors, cites

        vectors = await self._embed_snippets(cfg=cfg, snippets=snippets, budget=budget)
        cites = [item.cite for item in snippets]
        if not vectors:
            return [], []

        self._persist_vectors(vectors)
        self._persist_meta(
            {
                "signature": signature,
                "model": model,
                "dim": len(vectors[0]) if vectors else 0,
                "count": len(vectors),
                "backend": self._backend,
                "cites": cites,
            }
        )

        if self._backend == "faiss" and faiss is not None and np is not None:
            self._persist_faiss(vectors)

        return vectors, cites

    async def _embed_snippets(
        self,
        *,
        cfg: CodeWikiConfig,
        snippets: list[Snippet],
        budget: Budget | None,
    ) -> list[list[float]]:
        out: list[list[float]] = []
        batch_size = 24

        async with LLMClient(cfg.llm) as client:
            for i in range(0, len(snippets), batch_size):
                batch = snippets[i : i + batch_size]
                texts = [item.text[:4000] for item in batch]
                if budget is not None:
                    budget.record(
                        prompt_tokens=sum(Budget.estimate(text) for text in texts),
                        completion_tokens=0,
                    )
                vectors = await with_retry(client.embed, texts, retries=2)
                out.extend(vectors)
        return out

    async def _embed_query(
        self,
        *,
        cfg: CodeWikiConfig,
        query: str,
        budget: Budget | None,
    ) -> list[float]:
        if budget is not None:
            budget.record(prompt_tokens=Budget.estimate(query), completion_tokens=0)
        async with LLMClient(cfg.llm) as client:
            vectors = await with_retry(client.embed, [query], retries=2)
        if not vectors:
            return []
        return [float(item) for item in vectors[0]]

    def _rank(
        self,
        *,
        vectors: list[list[float]],
        query_vec: list[float],
        top_k: int,
    ) -> list[tuple[int, float]]:
        if self._backend == "faiss" and faiss is not None and np is not None and self._faiss_path.exists():
            try:
                return self._rank_faiss(query_vec=query_vec, top_k=top_k)
            except Exception:
                pass

        if np is not None:
            return self._rank_numpy(vectors=vectors, query_vec=query_vec, top_k=top_k)
        return self._rank_python(vectors=vectors, query_vec=query_vec, top_k=top_k)

    def _rank_faiss(self, *, query_vec: list[float], top_k: int) -> list[tuple[int, float]]:
        if faiss is None or np is None:
            return []

        index = faiss.read_index(str(self._faiss_path))
        q = np.array([query_vec], dtype=np.float32)
        faiss.normalize_L2(q)
        scores, idxs = index.search(q, top_k)
        out: list[tuple[int, float]] = []
        for idx, score in zip(idxs[0], scores[0]):
            if idx < 0:
                continue
            out.append((int(idx), float(score)))
        return out

    def _rank_numpy(
        self,
        *,
        vectors: list[list[float]],
        query_vec: list[float],
        top_k: int,
    ) -> list[tuple[int, float]]:
        if np is None:
            return []

        matrix = np.asarray(vectors, dtype=np.float32)
        query = np.asarray(query_vec, dtype=np.float32)

        matrix_norm = np.linalg.norm(matrix, axis=1)
        query_norm = np.linalg.norm(query)
        denom = matrix_norm * query_norm + 1e-8
        sims = (matrix @ query) / denom

        if top_k >= len(vectors):
            idxs = np.argsort(-sims)
        else:
            idxs = np.argpartition(-sims, top_k - 1)[:top_k]
            idxs = idxs[np.argsort(-sims[idxs])]

        return [(int(i), float(sims[i])) for i in idxs]

    def _rank_python(
        self,
        *,
        vectors: list[list[float]],
        query_vec: list[float],
        top_k: int,
    ) -> list[tuple[int, float]]:
        scored: list[tuple[int, float]] = []
        for idx, vec in enumerate(vectors):
            score = _cosine(vec, query_vec)
            scored.append((idx, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]

    def _persist_faiss(self, vectors: list[list[float]]) -> None:
        if faiss is None or np is None or not vectors:
            return

        matrix = np.asarray(vectors, dtype=np.float32)
        faiss.normalize_L2(matrix)

        index = faiss.IndexFlatIP(matrix.shape[1])
        index.add(matrix)
        faiss.write_index(index, str(self._faiss_path))

    def _persist_vectors(self, vectors: list[list[float]]) -> None:
        if np is not None:
            np.save(self._vectors_npy, np.asarray(vectors, dtype=np.float32))
            if self._vectors_json.exists():
                self._vectors_json.unlink()
            return

        self._vectors_json.write_text(json.dumps(vectors), encoding="utf-8")

    def _load_vectors(self) -> list[list[float]]:
        if np is not None and self._vectors_npy.exists():
            loaded = np.load(self._vectors_npy, allow_pickle=False)
            return loaded.astype(np.float32).tolist()

        if self._vectors_json.exists():
            try:
                data = json.loads(self._vectors_json.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return [
                        [float(value) for value in row]
                        for row in data
                        if isinstance(row, list)
                    ]
            except Exception:
                return []
        return []

    def _persist_meta(self, payload: dict) -> None:
        self._meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_meta(self) -> dict:
        if not self._meta_path.exists():
            return {}
        try:
            raw = json.loads(self._meta_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return raw if isinstance(raw, dict) else {}


def _signature(snippets: list[Snippet], model: str) -> str:
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    for item in snippets:
        text_hash = hashlib.sha256(item.text.encode("utf-8", errors="ignore")).hexdigest()
        h.update(item.cite.encode("utf-8"))
        h.update(text_hash.encode("utf-8"))
    return h.hexdigest()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0

    n = min(len(a), len(b))
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        dot += a[i] * b[i]
        na += a[i] * a[i]
        nb += b[i] * b[i]

    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)
