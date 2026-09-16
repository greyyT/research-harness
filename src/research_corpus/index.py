"""Per-run rebuildable SQLite index for the Evidence Ledger (PLAN.md §4.4, I10)."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

from research_corpus.artifacts import RunPaths
from research_corpus.ledger import (
    ClaimQuery,
    _read_claims_file,
    _read_contradictions_file,
)
from research_corpus.records import (
    ClaimRecord,
    ClaimStatus,
    Confidence,
    from_json,
)

SCHEMA_VERSION = 1

DROP_TABLES_SQL = """
DROP TABLE IF EXISTS contradiction_claims;
DROP TABLE IF EXISTS contradictions;
DROP TABLE IF EXISTS claim_conflicts;
DROP TABLE IF EXISTS claim_evidence;
DROP TABLE IF EXISTS claim_sources;
DROP TABLE IF EXISTS claims;
DROP TABLE IF EXISTS meta;
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    researcher TEXT NOT NULL,
    type TEXT NOT NULL,
    confidence TEXT NOT NULL,
    status TEXT NOT NULL,
    claim TEXT NOT NULL,
    created_at TEXT NOT NULL,
    json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_sources (
    claim_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    PRIMARY KEY (claim_id, source_id)
);

CREATE TABLE IF NOT EXISTS claim_evidence (
    claim_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    evidence_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_conflicts (
    claim_id TEXT NOT NULL,
    other_id TEXT NOT NULL,
    PRIMARY KEY (claim_id, other_id)
);

CREATE TABLE IF NOT EXISTS contradictions (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contradiction_claims (
    contradiction_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    PRIMARY KEY (contradiction_id, claim_id)
);

CREATE INDEX IF NOT EXISTS idx_claims_topic ON claims(topic);
CREATE INDEX IF NOT EXISTS idx_claims_confidence ON claims(confidence);
CREATE INDEX IF NOT EXISTS idx_claims_researcher ON claims(researcher);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);
CREATE INDEX IF NOT EXISTS idx_claim_sources_source ON claim_sources(source_id);
CREATE INDEX IF NOT EXISTS idx_claim_evidence_type ON claim_evidence(evidence_type);
CREATE INDEX IF NOT EXISTS idx_claim_evidence_source ON claim_evidence(source_id);
CREATE INDEX IF NOT EXISTS idx_claim_conflicts_other ON claim_conflicts(other_id);
CREATE INDEX IF NOT EXISTS idx_contradiction_claims_claim ON contradiction_claims(claim_id);
"""


class LedgerIndex:
    """Disposable, rebuildable SQLite query index derived from canonical JSONL files."""

    def __init__(self, paths: RunPaths) -> None:
        if not isinstance(paths, RunPaths):
            raise TypeError(f"Expected RunPaths, got {type(paths)}")
        self.paths = paths

    def _get_jsonl_stats(self) -> tuple[int, int, int, int]:
        claims_mtime_ns, claims_size = 0, 0
        if self.paths.claims.exists():
            st = self.paths.claims.stat()
            claims_mtime_ns, claims_size = st.st_mtime_ns, st.st_size

        contra_mtime_ns, contra_size = 0, 0
        if self.paths.contradictions.exists():
            st = self.paths.contradictions.stat()
            contra_mtime_ns, contra_size = st.st_mtime_ns, st.st_size

        return (claims_mtime_ns, claims_size, contra_mtime_ns, contra_size)

    def _remove_index_file(self) -> None:
        for p in [
            self.paths.index_sqlite,
            self.paths.index_sqlite.with_name(self.paths.index_sqlite.name + "-wal"),
            self.paths.index_sqlite.with_name(self.paths.index_sqlite.name + "-shm"),
        ]:
            try:
                if p.exists() or p.is_symlink():
                    p.unlink()
            except OSError:
                pass

    def is_fresh(self) -> bool:
        """Check whether index.sqlite exists, is readable SQLite, and matches JSONL stats and schema version."""
        if not self.paths.index_sqlite.is_file():
            return False
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(self.paths.index_sqlite)
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM meta")
            meta = dict(cursor.fetchall())
        except (sqlite3.Error, OSError):
            return False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        if meta.get("schema_version") != str(SCHEMA_VERSION):
            return False

        try:
            recorded = (
                int(meta["claims_mtime_ns"]),
                int(meta["claims_size"]),
                int(meta["contradictions_mtime_ns"]),
                int(meta["contradictions_size"]),
            )
        except (KeyError, ValueError):
            return False

        current = self._get_jsonl_stats()
        return recorded == current

    def rebuild(self) -> None:
        """Rebuild the SQLite index from scratch from canonical JSONL files."""
        self._remove_index_file()

        # Load canonical JSONL first: any LedgerCorruptError surfaces immediately
        claims_data = _read_claims_file(self.paths.claims)
        contradictions_data = _read_contradictions_file(self.paths.contradictions)

        stats = self._get_jsonl_stats()

        self.paths.index_sqlite.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.paths.index_sqlite)
        try:
            with conn:
                cursor = conn.cursor()
                cursor.executescript(DROP_TABLES_SQL)
                cursor.executescript(SCHEMA_SQL)

                for claim, raw_json in claims_data:
                    cursor.execute(
                        """
                        INSERT INTO claims (id, topic, researcher, type, confidence, status, claim, created_at, json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            claim.id,
                            claim.topic,
                            claim.researcher,
                            claim.type.value,
                            claim.confidence.value,
                            claim.status.value,
                            claim.claim,
                            claim.created_at,
                            raw_json,
                        ),
                    )
                    for src_id in claim.sources:
                        cursor.execute(
                            "INSERT OR IGNORE INTO claim_sources (claim_id, source_id) VALUES (?, ?)",
                            (claim.id, src_id),
                        )
                    for ref in claim.evidence:
                        cursor.execute(
                            "INSERT INTO claim_evidence (claim_id, source_id, evidence_type) VALUES (?, ?, ?)",
                            (claim.id, ref.source_id, ref.evidence_type),
                        )
                    for other_id in claim.conflicts:
                        cursor.execute(
                            "INSERT OR IGNORE INTO claim_conflicts (claim_id, other_id) VALUES (?, ?)",
                            (claim.id, other_id),
                        )

                for contra, raw_json in contradictions_data:
                    cursor.execute(
                        "INSERT INTO contradictions (id, status, json) VALUES (?, ?, ?)",
                        (contra.id, contra.status.value, raw_json),
                    )
                    for cid in contra.claim_ids:
                        cursor.execute(
                            "INSERT OR IGNORE INTO contradiction_claims (contradiction_id, claim_id) VALUES (?, ?)",
                            (contra.id, cid),
                        )

                meta_rows = [
                    ("schema_version", str(SCHEMA_VERSION)),
                    ("claims_mtime_ns", str(stats[0])),
                    ("claims_size", str(stats[1])),
                    ("contradictions_mtime_ns", str(stats[2])),
                    ("contradictions_size", str(stats[3])),
                ]
                cursor.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", meta_rows)
        finally:
            conn.close()

    def ensure_fresh(self) -> None:
        """Ensure the index exists and is up to date with JSONL; rebuild if not."""
        if not self.is_fresh():
            self.rebuild()

    def _query_once(self, query: ClaimQuery | None) -> list[ClaimRecord]:
        self.ensure_fresh()
        conn = sqlite3.connect(self.paths.index_sqlite)
        try:
            cursor = conn.cursor()
            clauses: list[str] = []
            params: list[Any] = []

            if query is not None:
                if query.topic is not None:
                    clauses.append("c.topic = ?")
                    params.append(query.topic)

                if query.confidence is not None:
                    conf_val = (
                        query.confidence.value
                        if isinstance(query.confidence, Confidence)
                        else Confidence(query.confidence).value
                    )
                    clauses.append("c.confidence = ?")
                    params.append(conf_val)

                if query.researcher is not None:
                    clauses.append("c.researcher = ?")
                    params.append(query.researcher)

                if query.source_id is not None:
                    clauses.append(
                        "(EXISTS (SELECT 1 FROM claim_sources cs WHERE cs.claim_id = c.id AND cs.source_id = ?) "
                        "OR EXISTS (SELECT 1 FROM claim_evidence ce WHERE ce.claim_id = c.id AND ce.source_id = ?))"
                    )
                    params.extend([query.source_id, query.source_id])

                if query.evidence_type is not None:
                    clauses.append(
                        "EXISTS (SELECT 1 FROM claim_evidence ce WHERE ce.claim_id = c.id AND ce.evidence_type = ?)"
                    )
                    params.append(query.evidence_type)

                if query.status is not None:
                    status_val = (
                        query.status.value
                        if isinstance(query.status, ClaimStatus)
                        else ClaimStatus(query.status).value
                    )
                    clauses.append("c.status = ?")
                    params.append(status_val)

                if query.contradiction is not None:
                    if query.contradiction == "any":
                        clauses.append(
                            "EXISTS (SELECT 1 FROM claim_conflicts cc WHERE cc.claim_id = c.id)"
                        )
                    elif query.contradiction == "none":
                        clauses.append(
                            "NOT EXISTS (SELECT 1 FROM claim_conflicts cc WHERE cc.claim_id = c.id)"
                        )
                    elif query.contradiction.startswith("contradiction:"):
                        target_contra_id = query.contradiction[len("contradiction:"):].strip()
                        clauses.append(
                            "EXISTS (SELECT 1 FROM contradiction_claims ctc WHERE ctc.claim_id = c.id AND ctc.contradiction_id = ?)"
                        )
                        params.append(target_contra_id)
                    else:
                        clauses.append(
                            "EXISTS (SELECT 1 FROM claim_conflicts cc WHERE cc.claim_id = c.id AND cc.other_id = ?)"
                        )
                        params.append(query.contradiction)

            sql = "SELECT c.json FROM claims c"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY c.id ASC"

            cursor.execute(sql, params)
            rows = cursor.fetchall()
            return [from_json(ClaimRecord, r[0]) for r in rows]
        finally:
            conn.close()

    def query(self, query: ClaimQuery | None = None) -> list[ClaimRecord]:
        """Query claims from the SQLite index, transparently rebuilding once on failure."""
        if query is not None and not isinstance(query, ClaimQuery):
            raise TypeError(f"Expected ClaimQuery, got {type(query)}")
        try:
            return self._query_once(query)
        except (sqlite3.Error, OSError):
            self._remove_index_file()
            self.rebuild()
            return self._query_once(query)


__all__ = [
    "LedgerIndex",
    "SCHEMA_VERSION",
]
