"""AAC memory: one local SQLite file with text, keywords and vectors.

Framework section 2. What is stored is always the original text plus where
it came from (source) and what kind of thing it is (kind). "agent believes"
is never a fact until the user confirms it. Search is hybrid: SQLite FTS5
for exact words (names, numbers) and embeddings for meaning, merged by
reciprocal rank. Without an embedding model it falls back to keywords only.
Vectors keep the name of the model that made them, so switching models
means re-embedding from the text, nothing is lost.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import math
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import LOGGER

try:  # numpy makes vector search fast; pure Python is fine for a few thousand items
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

KINDS = ("fact", "preference", "event", "note")
SOURCES = ("user said", "agent believes", "sensor")
RRF_K = 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'note',
    source TEXT NOT NULL DEFAULT 'user said',
    confirmed INTEGER NOT NULL DEFAULT 1,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    embedding BLOB,
    embed_model TEXT
);
"""
_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(text, content='memories', content_rowid='rowid');
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, text) VALUES (new.rowid, new.text); END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.rowid, old.text); END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF text ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
  INSERT INTO memories_fts(rowid, text) VALUES (new.rowid, new.text); END;
"""


@dataclass
class Memory:
    """One remembered thing."""

    id: str
    text: str
    kind: str
    source: str
    confirmed: bool
    created: str
    updated: str
    embed_model: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """For the panel and tools."""
        return {
            "id": self.id,
            "text": self.text,
            "kind": self.kind,
            "source": self.source,
            "confirmed": self.confirmed,
            "created": self.created,
            "updated": self.updated,
            "embedded": self.embed_model is not None,
        }


def _row(row: sqlite3.Row) -> Memory:
    return Memory(
        id=row["id"],
        text=row["text"],
        kind=row["kind"],
        source=row["source"],
        confirmed=bool(row["confirmed"]),
        created=row["created"],
        updated=row["updated"],
        embed_model=row["embed_model"],
    )


def _pack(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def _unpack(blob: bytes) -> array:
    vec = array("f")
    vec.frombytes(blob)
    return vec


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# Filler words (English + German) make keyword matches noisy
_STOP = set(
    "the a an and or but is are was were be been to of in on at for with from by it this that "
    "these those my your our his her their me you we they i what which who whom how when where why "
    "do does did can could should would will shall may might must have has had not no yes please "
    "about again any some all just also than then there here new question check memory remember "
    "der die das ein eine einen und oder aber ist sind war waren zu von im in am an auf für mit "
    "bei es dies diese mein meine dein deine wir ihr sie ich du was welche welcher wer wie wann wo "
    "warum nicht kein keine ja bitte noch auch dann hier".split()
)


def _terms(text: str) -> list[str]:
    return [w for w in re.findall(r"\w+", text.lower()) if len(w) > 2 and w not in _STOP]


def _fts_query(text: str) -> str:
    """Content words as OR'ed prefix terms; FTS5 syntax characters are dropped."""
    return " OR ".join(f'"{w}"*' for w in _terms(text)[:12])


class MemoryDB:
    """Blocking SQLite access; call through the async wrappers of MemoryStore."""

    def __init__(self, path: Path) -> None:
        """Open (and create) the database."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        try:
            self._conn.executescript(_FTS)
            self.fts = True
        except sqlite3.OperationalError as err:  # SQLite without FTS5
            LOGGER.warning("SQLite has no FTS5 (%s); memory keyword search uses LIKE", err)
            self.fts = False
        self._conn.commit()

    def close(self) -> None:
        """Close the database."""
        with self._lock:
            self._conn.close()

    def add(self, text: str, kind: str, source: str, embedding=None, embed_model=None) -> Memory:
        """Insert a memory. 'agent believes' starts unconfirmed."""
        now = dt_util.utcnow().isoformat()
        mem_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._conn.execute(
                "INSERT INTO memories (id, text, kind, source, confirmed, created, updated, embedding, embed_model)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (mem_id, text, kind, source, int(source != "agent believes"), now, now,
                 _pack(embedding) if embedding else None, embed_model if embedding else None),
            )
            self._conn.commit()
        return self.get(mem_id)

    def get(self, mem_id: str) -> Memory | None:
        """One memory."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM memories WHERE id = ?", (mem_id,)).fetchone()
        return _row(row) if row else None

    def update(self, mem_id: str, embedding=None, embed_model=None, **fields: Any) -> Memory | None:
        """Change text/kind/source/confirmed (a new text drops the old vector)."""
        sets, values = [], []
        for key in ("text", "kind", "source", "confirmed"):
            if key in fields and fields[key] is not None:
                sets.append(f"{key} = ?")
                values.append(int(fields[key]) if key == "confirmed" else fields[key])
        if "text" in fields:
            sets += ["embedding = ?", "embed_model = ?"]
            values += [_pack(embedding) if embedding else None, embed_model if embedding else None]
        if not sets:
            return self.get(mem_id)
        sets.append("updated = ?")
        values += [dt_util.utcnow().isoformat(), mem_id]
        with self._lock:
            self._conn.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id = ?", values)  # noqa: S608
            self._conn.commit()
        return self.get(mem_id)

    def delete(self, mem_id: str) -> bool:
        """Remove a memory."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def list(self, limit: int = 200, unconfirmed: bool = False) -> list[Memory]:
        """Newest first."""
        query = "SELECT * FROM memories"
        if unconfirmed:
            query += " WHERE confirmed = 0"
        with self._lock:
            rows = self._conn.execute(f"{query} ORDER BY updated DESC LIMIT ?", (limit,)).fetchall()  # noqa: S608
        return [_row(r) for r in rows]

    def count(self) -> dict[str, int]:
        """Totals for the panel."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, SUM(confirmed = 0) AS open, SUM(embedding IS NOT NULL) AS emb FROM memories"
            ).fetchone()
        return {"total": row["n"] or 0, "unconfirmed": row["open"] or 0, "embedded": row["emb"] or 0}

    def keyword_search(self, text: str, limit: int) -> list[str]:
        """Ids by keyword relevance."""
        with self._lock:
            if self.fts and (query := _fts_query(text)):
                rows = self._conn.execute(
                    "SELECT m.id FROM memories_fts f JOIN memories m ON m.rowid = f.rowid"
                    " WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT ?",
                    (query, limit),
                ).fetchall()
            else:
                words = _terms(text)[:6] or [""]
                where = " OR ".join("lower(text) LIKE ?" for _ in words)
                rows = self._conn.execute(
                    f"SELECT id FROM memories WHERE {where} ORDER BY updated DESC LIMIT ?",  # noqa: S608
                    (*[f"%{w}%" for w in words], limit),
                ).fetchall()
        return [r["id"] for r in rows]

    def vector_search(self, vector: list[float], embed_model: str, limit: int) -> list[tuple[str, float]]:
        """Ids by cosine similarity (only vectors from the same model)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, embedding FROM memories WHERE embedding IS NOT NULL AND embed_model = ?",
                (embed_model,),
            ).fetchall()
        if not rows:
            return []
        if np is not None:
            matrix = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
            query = np.asarray(vector, dtype=np.float32)
            norms = np.linalg.norm(matrix, axis=1) * (np.linalg.norm(query) or 1.0)
            scores = (matrix @ query) / np.where(norms == 0, 1.0, norms)
            pairs = [(rows[i]["id"], float(scores[i])) for i in range(len(rows))]
        else:
            pairs = [(r["id"], _cosine(vector, _unpack(r["embedding"]))) for r in rows]
        pairs.sort(key=lambda p: p[1], reverse=True)
        return pairs[:limit]

    def missing_embeddings(self, embed_model: str, limit: int = 50) -> list[tuple[str, str]]:
        """(id, text) of memories without a vector from this model."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, text FROM memories WHERE embed_model IS NULL OR embed_model != ? LIMIT ?",
                (embed_model, limit),
            ).fetchall()
        return [(r["id"], r["text"]) for r in rows]

    def set_embedding(self, mem_id: str, vector: list[float], embed_model: str) -> None:
        """Store a vector (no 'updated' change: the content did not change)."""
        with self._lock:
            self._conn.execute(
                "UPDATE memories SET embedding = ?, embed_model = ? WHERE id = ?",
                (_pack(vector), embed_model, mem_id),
            )
            self._conn.commit()


def fuse(keyword_ids: list[str], vector_hits: list[tuple[str, float]], min_score: float = 0.35) -> list[str]:
    """Reciprocal rank fusion of both result lists (weak vector hits are dropped)."""
    scores: dict[str, float] = {}
    for rank, mem_id in enumerate(keyword_ids):
        scores[mem_id] = scores.get(mem_id, 0) + 1 / (RRF_K + rank)
    for rank, (mem_id, sim) in enumerate(vector_hits):
        if sim >= min_score:
            scores[mem_id] = scores.get(mem_id, 0) + 1 / (RRF_K + rank)
    return sorted(scores, key=scores.get, reverse=True)


def memory_path(hass: HomeAssistant) -> Path:
    """Where the database lives (inside HA's config dir, so backups include it)."""
    return Path(hass.config.path("aac", "memory.db"))


class MemoryStore:
    """Async front of the memory database, with embeddings."""

    def __init__(self, hass: HomeAssistant, path: Path, embedder) -> None:
        """embedder: async (texts) -> (vectors, model name) or None when not set up."""
        self.hass = hass
        self._path = path
        self._embedder = embedder
        self.db: MemoryDB | None = None
        self._reembed_task = None

    async def async_open(self) -> None:
        """Open the database in the executor."""
        self.db = await self.hass.async_add_executor_job(MemoryDB, self._path)

    async def async_close(self) -> None:
        """Close the database."""
        if self.db:
            await self.hass.async_add_executor_job(self.db.close)
            self.db = None

    async def _embed(self, texts: list[str]) -> tuple[list[list[float]] | None, str | None]:
        try:
            result = await self._embedder(texts)
        except Exception as err:  # noqa: BLE001 - memory must work without embeddings
            LOGGER.warning("Embedding failed, storing text only: %s", err)
            return None, None
        return result if result else (None, None)

    async def async_remember(self, text: str, kind: str = "note", source: str = "user said") -> Memory:
        """Store something."""
        vectors, model = await self._embed([text])
        return await self.hass.async_add_executor_job(
            self.db.add, text, kind, source, vectors[0] if vectors else None, model
        )

    async def async_recall(self, query: str, limit: int = 5, min_score: float = 0.35) -> list[Memory]:
        """Hybrid search: keywords + meaning."""
        keyword_ids = await self.hass.async_add_executor_job(self.db.keyword_search, query, 20)
        vector_hits: list[tuple[str, float]] = []
        vectors, model = await self._embed([query])
        if vectors:
            vector_hits = await self.hass.async_add_executor_job(
                self.db.vector_search, vectors[0], model, 20
            )
        ids = fuse(keyword_ids, vector_hits, min_score)[:limit]
        found = [await self.hass.async_add_executor_job(self.db.get, i) for i in ids]
        return [m for m in found if m]

    async def async_context(self, text: str, limit: int = 3) -> list[Memory]:
        """Memories worth handing to the model before it answers (context engine)."""
        if not _terms(text):
            return []
        # Stricter than explicit search: bge-m3 rates unrelated text ~0.4 similar
        return await self.async_recall(text, limit, min_score=0.5)

    async def async_update(self, mem_id: str, **fields: Any) -> Memory | None:
        """Change a memory (a new text gets a new vector)."""
        vector = model = None
        if fields.get("text"):
            vectors, model = await self._embed([fields["text"]])
            vector = vectors[0] if vectors else None
        return await self.hass.async_add_executor_job(
            lambda: self.db.update(mem_id, embedding=vector, embed_model=model, **fields)
        )

    async def async_delete(self, mem_id: str) -> bool:
        """Forget a memory."""
        return await self.hass.async_add_executor_job(self.db.delete, mem_id)

    async def async_list(self, limit: int = 200, unconfirmed: bool = False) -> list[Memory]:
        """Newest first."""
        return await self.hass.async_add_executor_job(self.db.list, limit, unconfirmed)

    async def async_count(self) -> dict[str, int]:
        """Totals."""
        return await self.hass.async_add_executor_job(self.db.count)

    async def async_reembed(self) -> int:
        """Give every memory a vector from the current embedding model (background job)."""
        done = 0
        while True:
            probe, model = await self._embed(["probe"])
            if not probe:
                return done
            missing = await self.hass.async_add_executor_job(self.db.missing_embeddings, model, 32)
            if not missing:
                return done
            vectors, model = await self._embed([text for _, text in missing])
            if not vectors:
                return done
            for (mem_id, _), vector in zip(missing, vectors, strict=True):
                await self.hass.async_add_executor_job(self.db.set_embedding, mem_id, vector, model)
            done += len(missing)
