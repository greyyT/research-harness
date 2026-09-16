"""Tests for rebuildable SQLite index, freshness/recovery, and scan-equivalence oracle (PLAN.md §4.4, I8, I10, S3)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest

from research_corpus.artifacts import RunPaths, init_run
from research_corpus.index import SCHEMA_VERSION, LedgerIndex
from research_corpus.ledger import (
    ClaimQuery,
    Ledger,
    LedgerCorruptError,
)
from research_corpus.records import (
    ClaimRecord,
    ClaimStatus,
    ClaimType,
    Confidence,
    ContradictionRecord,
    EvidenceRef,
    to_json,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ledger"


@pytest.fixture(scope="session", autouse=True)
def cleanup_fixture_sqlite():
    # Also clean up before session in case prior run left it
    fixture_sqlite = FIXTURE_DIR / "evidence" / "index.sqlite"
    for p in [
        fixture_sqlite,
        fixture_sqlite.with_name("index.sqlite-wal"),
        fixture_sqlite.with_name("index.sqlite-shm"),
    ]:
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass

    yield

    for p in [
        fixture_sqlite,
        fixture_sqlite.with_name("index.sqlite-wal"),
        fixture_sqlite.with_name("index.sqlite-shm"),
    ]:
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


def _fixture_ledger() -> Ledger:
    return Ledger(RunPaths(FIXTURE_DIR.parent, FIXTURE_DIR.name))


def _create_test_run(tmp_path: Path, slug: str) -> tuple[RunPaths, Ledger]:
    paths = RunPaths(tmp_path, slug)
    init_run(paths)

    index_data = {
        "sources": [
            {
                "id": "src-valid-1",
                "kind": "paper",
                "tier": "A",
                "url": "https://example.com/paper1.pdf",
                "path": None,
                "title": "Valid Paper 1",
                "admitted": True,
                "reason": "Authoritative paper",
                "declared_by": "scout",
                "decided_at": "2026-09-16T10:00:00Z",
                "corpus_ref": {"notebooklm": "uuid-1"},
            },
            {
                "id": "src-rejected-2",
                "kind": "blog_post",
                "tier": None,
                "url": "https://example.com/rejected-blog",
                "path": None,
                "title": "Rejected Blog",
                "admitted": False,
                "reason": "Low credibility",
                "declared_by": "scout",
                "decided_at": "2026-09-16T10:05:00Z",
                "corpus_ref": {},
            },
        ],
        "corpus": {},
    }
    paths.source_index.write_text(json.dumps(index_data, indent=2), encoding="utf-8")
    return paths, Ledger(paths)


# ---------------------------------------------------------------------------
# 1. Equivalence Oracle: SQLite Index === JSONL Scan (~35 Query Combinations)
# ---------------------------------------------------------------------------


REPRESENTATIVE_QUERIES = [
    # Baseline
    None,
    ClaimQuery(),
    # Topic dimension
    ClaimQuery(topic="agent-memory"),
    ClaimQuery(topic="retrieval-eval"),
    ClaimQuery(topic="context-compaction"),
    ClaimQuery(topic="non-existent-topic"),
    # Confidence dimension (enum + string coercion)
    ClaimQuery(confidence=Confidence.high),
    ClaimQuery(confidence="high"),
    ClaimQuery(confidence=Confidence.medium),
    ClaimQuery(confidence=Confidence.low),
    # Researcher dimension
    ClaimQuery(researcher="architecture-researcher"),
    ClaimQuery(researcher="benchmark-researcher"),
    ClaimQuery(researcher="unknown-researcher"),
    # Source ID dimension (via sources or evidence)
    ClaimQuery(source_id="src-arxiv-2401-mem"),
    ClaimQuery(source_id="src-bench-memarena"),
    ClaimQuery(source_id="src-blog-thoughts"),
    ClaimQuery(source_id="src-nonexistent"),
    # Evidence type dimension
    ClaimQuery(evidence_type="paper"),
    ClaimQuery(evidence_type="benchmark"),
    ClaimQuery(evidence_type="engineering_report"),
    ClaimQuery(evidence_type="video"),
    # Status dimension (all 7 statuses)
    ClaimQuery(status=ClaimStatus.supported),
    ClaimQuery(status=ClaimStatus.tentative),
    ClaimQuery(status=ClaimStatus.contradicted),
    ClaimQuery(status=ClaimStatus.needs_more_evidence),
    ClaimQuery(status=ClaimStatus.source_too_weak),
    ClaimQuery(status=ClaimStatus.unresolved),
    ClaimQuery(status=ClaimStatus.rejected),
    # Contradiction dimension (all 4 modes)
    ClaimQuery(contradiction="any"),
    ClaimQuery(contradiction="none"),
    ClaimQuery(contradiction="claim-004"),
    ClaimQuery(contradiction="claim-003"),
    ClaimQuery(contradiction="claim-999"),
    ClaimQuery(contradiction="contradiction:contra-001"),
    ClaimQuery(contradiction="contradiction:contra-999"),
    # Combined filters (AND semantics)
    ClaimQuery(topic="agent-memory", status=ClaimStatus.supported),
    ClaimQuery(
        topic="agent-memory",
        researcher="benchmark-researcher",
        confidence=Confidence.medium,
    ),
    ClaimQuery(
        topic="retrieval-eval",
        contradiction="any",
        confidence=Confidence.high,
    ),
    ClaimQuery(topic="agent-memory", status=ClaimStatus.rejected),
    ClaimQuery(
        source_id="src-arxiv-2401-mem",
        evidence_type="paper",
        researcher="architecture-researcher",
    ),
    ClaimQuery(contradiction="none", confidence=Confidence.low),
    ClaimQuery(
        topic="agent-memory",
        researcher="architecture-researcher",
        confidence=Confidence.high,
        status=ClaimStatus.supported,
    ),
]


def test_fixture_equivalence_for_representative_queries(tmp_path: Path):
    """Verify index.query and ledger.scan produce identical results across >35 query combinations."""
    # Copy fixture into tmp_path to avoid creating index.sqlite in fixture dir
    dest_dir = tmp_path / "seeded-run"
    shutil.copytree(FIXTURE_DIR, dest_dir, ignore=shutil.ignore_patterns("*.sqlite*"))

    paths = RunPaths(tmp_path, "seeded-run")
    ledger = Ledger(paths)
    index = ledger.index

    # Index does not exist initially
    assert not paths.index_sqlite.exists()

    for idx, q in enumerate(REPRESENTATIVE_QUERIES):
        scan_results = ledger.scan(q)
        query_results = ledger.query(q)
        index_results = index.query(q)

        assert query_results == scan_results, f"ledger.query != ledger.scan for query #{idx}: {q}"
        assert index_results == scan_results, f"index.query != ledger.scan for query #{idx}: {q}"

        # Verify deterministic sorting by id
        ids = [c.id for c in query_results]
        assert ids == sorted(ids), f"Results not sorted by id for query #{idx}: {q}"

    # Index now exists and is fresh
    assert paths.index_sqlite.exists()
    assert index.is_fresh()


def test_hydrated_records_exactly_equal_scan_records(tmp_path: Path):
    """Verify that every field, type, and sub-structure of hydrated records equals scan records."""
    dest_dir = tmp_path / "seeded-run"
    shutil.copytree(FIXTURE_DIR, dest_dir, ignore=shutil.ignore_patterns("*.sqlite*"))
    paths = RunPaths(tmp_path, "seeded-run")
    ledger = Ledger(paths)

    scan_claims = ledger.scan()
    index_claims = ledger.query()

    assert len(scan_claims) == 8
    assert len(index_claims) == 8

    for sc, ic in zip(scan_claims, index_claims):
        assert sc == ic
        assert type(sc) is type(ic)
        assert sc.id == ic.id
        assert sc.claim == ic.claim
        assert sc.type == ic.type
        assert type(sc.type) is ClaimType
        assert sc.confidence == ic.confidence
        assert type(sc.confidence) is Confidence
        assert sc.status == ic.status
        assert type(sc.status) is ClaimStatus
        assert sc.sources == ic.sources
        assert sc.caveats == ic.caveats
        assert sc.conflicts == ic.conflicts
        assert sc.topic == ic.topic
        assert sc.researcher == ic.researcher
        assert sc.created_at == ic.created_at
        assert len(sc.evidence) == len(ic.evidence)
        for se, ie in zip(sc.evidence, ic.evidence):
            assert se == ie
            assert type(se) is EvidenceRef
            assert se.source_id == ie.source_id
            assert se.evidence_type == ie.evidence_type
            assert se.locator == ie.locator
            assert se.excerpt == ie.excerpt


# ---------------------------------------------------------------------------
# 2. Append & Rebuild Behavior
# ---------------------------------------------------------------------------


def test_rebuild_after_canonical_claim_append_reflects_new_row(tmp_path: Path):
    """Verify appending a claim to JSONL marks index stale and next query transparently rebuilds."""
    paths, ledger = _create_test_run(tmp_path, "append-rebuild-test")

    claim1 = ClaimRecord(
        id="c-001",
        claim="Claim one text.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "Excerpt 1")],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(claim1)

    # Initial query builds index
    assert [c.id for c in ledger.query()] == ["c-001"]
    assert paths.index_sqlite.exists()
    assert ledger.index.is_fresh()

    # Append second claim (touches claims.jsonl)
    claim2 = ClaimRecord(
        id="c-002",
        claim="Claim two text.",
        type=ClaimType.inference,
        confidence=Confidence.medium,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.tentative,
        topic="memory",
        researcher="bob",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 2", "Excerpt 2")],
        created_at="2026-09-16T12:05:00Z",
    )
    ledger.append_claim(claim2)

    # Index is now stale
    assert not ledger.index.is_fresh()

    # Query automatically rebuilds and returns both rows
    results = ledger.query()
    assert [c.id for c in results] == ["c-001", "c-002"]
    assert ledger.index.is_fresh()
    assert ledger.query() == ledger.scan()


def test_rebuild_after_canonical_contradiction_append(tmp_path: Path):
    """Verify appending a ContradictionRecord marks index stale and reflects in contradiction queries."""
    paths, ledger = _create_test_run(tmp_path, "contra-rebuild-test")

    claim1 = ClaimRecord(
        id="c-001",
        claim="Claim A.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=["c-002"],
        status=ClaimStatus.contradicted,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "Excerpt A")],
        created_at="2026-09-16T12:00:00Z",
    )
    claim2 = ClaimRecord(
        id="c-002",
        claim="Claim B.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=["c-001"],
        status=ClaimStatus.contradicted,
        topic="memory",
        researcher="bob",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 2", "Excerpt B")],
        created_at="2026-09-16T12:01:00Z",
    )
    ledger.append_claim(claim1)
    ledger.append_claim(claim2)

    # Initial query builds index
    assert len(ledger.query()) == 2
    assert ledger.index.is_fresh()

    # Before adding contradiction record, query by contradiction:contra-001 returns []
    assert ledger.query(ClaimQuery(contradiction="contradiction:contra-001")) == []

    # Append contradiction record (touches contradictions.jsonl)
    contra = ContradictionRecord(
        id="contra-001",
        claim_ids=["c-001", "c-002"],
        description="A contradicts B",
        status=ClaimStatus.contradicted,
        created_at="2026-09-16T12:02:00Z",
    )
    ledger.append_contradiction(contra)

    assert not ledger.index.is_fresh()

    # Next query transparently rebuilds and reflects the contradiction
    res = ledger.query(ClaimQuery(contradiction="contradiction:contra-001"))
    assert [c.id for c in res] == ["c-001", "c-002"]
    assert ledger.index.is_fresh()


# ---------------------------------------------------------------------------
# 3. Transparent Rebuild on Deleted Index
# ---------------------------------------------------------------------------


def test_deleted_index_transparently_rebuilds(tmp_path: Path):
    """Verify that deleting index.sqlite causes subsequent query to rebuild transparently."""
    paths, ledger = _create_test_run(tmp_path, "deleted-index-test")

    claim = ClaimRecord(
        id="c-001",
        claim="Claim text.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "Excerpt 1")],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(claim)

    assert len(ledger.query()) == 1
    assert paths.index_sqlite.exists()

    # Delete index file
    paths.index_sqlite.unlink()
    assert not paths.index_sqlite.exists()
    assert not ledger.index.is_fresh()

    # Query transparently rebuilds
    results = ledger.query()
    assert [c.id for c in results] == ["c-001"]
    assert paths.index_sqlite.exists()
    assert ledger.index.is_fresh()


# ---------------------------------------------------------------------------
# 4. Freshness Metadata Checks (All 4 Stats + Schema Version)
# ---------------------------------------------------------------------------


def test_stale_index_detected_by_claims_mtime(tmp_path: Path):
    """Verify modifying claims.jsonl mtime triggers freshness mismatch."""
    paths, ledger = _create_test_run(tmp_path, "stale-claims-mtime")
    claim = ClaimRecord(
        id="c-001",
        claim="Claim text.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "Excerpt 1")],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(claim)
    assert len(ledger.query()) == 1
    assert ledger.index.is_fresh()

    # Alter mtime by 100 seconds
    st = paths.claims.stat()
    os.utime(paths.claims, (st.st_atime, st.st_mtime + 100))

    assert not ledger.index.is_fresh()
    assert len(ledger.query()) == 1
    assert ledger.index.is_fresh()


def test_stale_index_detected_by_claims_size(tmp_path: Path):
    """Verify tampering with recorded claims_size in meta triggers freshness mismatch."""
    paths, ledger = _create_test_run(tmp_path, "stale-claims-size")
    claim = ClaimRecord(
        id="c-001",
        claim="Claim text.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "Excerpt 1")],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(claim)
    ledger.query()
    assert ledger.index.is_fresh()

    # Tamper with claims_size in meta
    conn = sqlite3.connect(paths.index_sqlite)
    conn.execute("UPDATE meta SET value = '999999' WHERE key = 'claims_size'")
    conn.commit()
    conn.close()

    assert not ledger.index.is_fresh()
    assert len(ledger.query()) == 1
    assert ledger.index.is_fresh()


def test_stale_index_detected_by_contradictions_mtime(tmp_path: Path):
    """Verify modifying contradictions.jsonl mtime triggers freshness mismatch."""
    paths, ledger = _create_test_run(tmp_path, "stale-contra-mtime")
    ledger.query()
    assert ledger.index.is_fresh()

    st = paths.contradictions.stat()
    os.utime(paths.contradictions, (st.st_atime, st.st_mtime + 50))

    assert not ledger.index.is_fresh()
    ledger.query()
    assert ledger.index.is_fresh()


def test_stale_index_detected_by_contradictions_size(tmp_path: Path):
    """Verify tampering with recorded contradictions_size triggers freshness mismatch."""
    paths, ledger = _create_test_run(tmp_path, "stale-contra-size")
    ledger.query()
    assert ledger.index.is_fresh()

    conn = sqlite3.connect(paths.index_sqlite)
    conn.execute("UPDATE meta SET value = '888888' WHERE key = 'contradictions_size'")
    conn.commit()
    conn.close()

    assert not ledger.index.is_fresh()
    ledger.query()
    assert ledger.index.is_fresh()


def test_stale_index_detected_by_schema_version(tmp_path: Path):
    """Verify differing schema_version in meta triggers rebuild."""
    paths, ledger = _create_test_run(tmp_path, "stale-schema-version")
    ledger.query()
    assert ledger.index.is_fresh()

    conn = sqlite3.connect(paths.index_sqlite)
    conn.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    assert not ledger.index.is_fresh()
    ledger.query()
    assert ledger.index.is_fresh()

    # Verify reset to SCHEMA_VERSION
    conn = sqlite3.connect(paths.index_sqlite)
    cur = conn.cursor()
    cur.execute("SELECT value FROM meta WHERE key = 'schema_version'")
    row = cur.fetchone()
    conn.close()
    assert row[0] == str(SCHEMA_VERSION)


# ---------------------------------------------------------------------------
# 5. Corrupt / Unreadable SQLite Recovery
# ---------------------------------------------------------------------------


def test_corrupt_sqlite_transparently_rebuilds(tmp_path: Path):
    """Verify garbage content in index.sqlite transparently rebuilds rather than raising SQLite error."""
    paths, ledger = _create_test_run(tmp_path, "corrupt-sqlite-test")
    claim = ClaimRecord(
        id="c-001",
        claim="Claim text.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "Excerpt 1")],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(claim)
    assert len(ledger.query()) == 1

    # Overwrite SQLite file with random garbage text
    paths.index_sqlite.write_text("CORRUPTED NON-SQLITE HEADER DATA")

    assert not ledger.index.is_fresh()

    # Must transparently rebuild without raising sqlite3.DatabaseError
    results = ledger.query()
    assert [c.id for c in results] == ["c-001"]
    assert ledger.index.is_fresh()


def test_empty_sqlite_file_transparently_rebuilds(tmp_path: Path):
    """Verify truncated (0 byte) SQLite file transparently rebuilds."""
    paths, ledger = _create_test_run(tmp_path, "empty-sqlite-test")
    claim = ClaimRecord(
        id="c-001",
        claim="Claim text.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "Excerpt 1")],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(claim)
    assert len(ledger.query()) == 1

    paths.index_sqlite.write_text("")
    assert not ledger.index.is_fresh()

    results = ledger.query()
    assert [c.id for c in results] == ["c-001"]
    assert ledger.index.is_fresh()


def test_missing_tables_sqlite_transparently_rebuilds(tmp_path: Path):
    """Verify valid SQLite database missing the meta table transparently rebuilds."""
    paths, ledger = _create_test_run(tmp_path, "missing-tables-test")
    claim = ClaimRecord(
        id="c-001",
        claim="Claim text.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "Excerpt 1")],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(claim)
    ledger.query()

    # Recreate database with only a dummy table
    paths.index_sqlite.unlink()
    conn = sqlite3.connect(paths.index_sqlite)
    conn.execute("CREATE TABLE dummy (x INT)")
    conn.commit()
    conn.close()

    assert not ledger.index.is_fresh()

    results = ledger.query()
    assert [c.id for c in results] == ["c-001"]
    assert ledger.index.is_fresh()


def test_unreadable_sqlite_transparently_rebuilds(tmp_path: Path):
    """Verify unreadable (mode 000) index file transparently rebuilds without error."""
    paths, ledger = _create_test_run(tmp_path, "unreadable-sqlite-test")
    claim = ClaimRecord(
        id="c-001",
        claim="Claim text.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "Excerpt 1")],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(claim)
    ledger.query()

    # Set mode to 000 (unreadable)
    paths.index_sqlite.chmod(0o000)
    try:
        assert not ledger.index.is_fresh()
        results = ledger.query()
        assert [c.id for c in results] == ["c-001"]
        assert ledger.index.is_fresh()
    finally:
        # Restore permissions for cleanup
        if paths.index_sqlite.exists():
            paths.index_sqlite.chmod(0o644)


# ---------------------------------------------------------------------------
# 6. Canonical Corruption Visibility
# ---------------------------------------------------------------------------


def test_corrupt_jsonl_raises_ledger_corrupt_error_on_query(tmp_path: Path):
    """Verify that corrupt claims.jsonl raises LedgerCorruptError and never hides corruption."""
    paths, ledger = _create_test_run(tmp_path, "corrupt-jsonl-test")
    claim = ClaimRecord(
        id="c-001",
        claim="Valid 1",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "T1")],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(claim)

    # Initial query succeeds and builds index
    assert len(ledger.query()) == 1

    # Now append corrupt line to claims.jsonl
    with open(paths.claims, "a", encoding="utf-8") as f:
        f.write("{invalid json line\n")

    # Index is now stale, query must rebuild and surface LedgerCorruptError
    with pytest.raises(LedgerCorruptError) as exc_info:
        ledger.query()

    err = exc_info.value
    assert err.path == paths.claims
    assert err.line_number == 2
    assert "line 2" in str(err)


def test_corrupt_contradictions_raises_ledger_corrupt_error_on_query(tmp_path: Path):
    """Verify that corrupt contradictions.jsonl raises LedgerCorruptError on rebuild."""
    paths, ledger = _create_test_run(tmp_path, "corrupt-contra-jsonl-test")
    ledger.query()

    # Append corrupt line to contradictions.jsonl
    with open(paths.contradictions, "a", encoding="utf-8") as f:
        f.write('{"not": "valid contradiction"}\n')

    with pytest.raises(LedgerCorruptError) as exc_info:
        ledger.query()

    err = exc_info.value
    assert err.path == paths.contradictions
    assert err.line_number == 1


# ---------------------------------------------------------------------------
# 7. Git Ignore Verification
# ---------------------------------------------------------------------------


def test_git_check_ignore_matches_index_and_sidecars():
    """Verify git check-ignore matches per-run index.sqlite and SQLite sidecars."""
    test_paths = [
        "research/example-memory-systems/evidence/index.sqlite",
        "research/example-memory-systems/evidence/index.sqlite-wal",
        "research/example-memory-systems/evidence/index.sqlite-shm",
        "research/topic-abc/evidence/index.sqlite",
    ]
    for p in test_paths:
        res = subprocess.run(
            ["git", "check-ignore", p],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f"git check-ignore failed for {p}: {res.stderr}"
        assert res.stdout.strip() == p


# ---------------------------------------------------------------------------
# 8. Edge Cases: Empty Run, Direct Usage, Arbitrary IDs
# ---------------------------------------------------------------------------


def test_empty_run_query_returns_empty_list(tmp_path: Path):
    """Verify querying an empty run returns [] and builds a valid empty index."""
    paths = RunPaths(tmp_path, "empty-run")
    init_run(paths)
    ledger = Ledger(paths)

    assert ledger.query() == []
    assert paths.index_sqlite.exists()
    assert ledger.index.is_fresh()
    assert ledger.query(ClaimQuery(topic="agent-memory")) == []


def test_direct_ledger_index_api(tmp_path: Path):
    """Verify direct instantiation and methods of LedgerIndex."""
    paths, ledger = _create_test_run(tmp_path, "direct-index-test")

    with pytest.raises(TypeError, match="Expected RunPaths"):
        LedgerIndex("not-run-paths")  # type: ignore[arg-type]

    idx = LedgerIndex(paths)
    assert not idx.is_fresh()

    idx.rebuild()
    assert idx.is_fresh()

    # Query with non-ClaimQuery raises TypeError
    with pytest.raises(TypeError, match="Expected ClaimQuery"):
        idx.query(12345)  # type: ignore[arg-type]

    assert idx.query() == []


def test_query_contradiction_literal_claim_ids_and_namespaces(tmp_path: Path):
    """Verify contradiction filter with arbitrary literal IDs matches in SQLite index."""
    paths, ledger = _create_test_run(tmp_path, "arbitrary-ids-test")

    claim1 = ClaimRecord(
        id="c-001",
        claim="Claim with unusual conflict IDs.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=["urn:claim:123", "conflict", "contradiction", "custom:prefix:456"],
        status=ClaimStatus.contradicted,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "Text")],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(claim1)

    # Namespaced id containing ':'
    q_colon = ClaimQuery(contradiction="urn:claim:123")
    assert [c.id for c in ledger.query(q_colon)] == ["c-001"]
    assert ledger.query(q_colon) == ledger.scan(q_colon)

    q_colon_2 = ClaimQuery(contradiction="custom:prefix:456")
    assert [c.id for c in ledger.query(q_colon_2)] == ["c-001"]
    assert ledger.query(q_colon_2) == ledger.scan(q_colon_2)

    # Literal word "conflict"
    q_conflict = ClaimQuery(contradiction="conflict")
    assert [c.id for c in ledger.query(q_conflict)] == ["c-001"]
    assert ledger.query(q_conflict) == ledger.scan(q_conflict)

    # Literal word "contradiction"
    q_contra = ClaimQuery(contradiction="contradiction")
    assert [c.id for c in ledger.query(q_contra)] == ["c-001"]
    assert ledger.query(q_contra) == ledger.scan(q_contra)

    # Non-matching arbitrary id
    q_nomatch = ClaimQuery(contradiction="urn:claim:999")
    assert ledger.query(q_nomatch) == []
    assert ledger.query(q_nomatch) == ledger.scan(q_nomatch)


# ---------------------------------------------------------------------------
# 9. Rebuild Resilience When Deletion Fails (Finding 1)
# ---------------------------------------------------------------------------


def test_rebuild_correct_when_unlink_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify that if index.sqlite cannot be unlinked, rebuild resets tables and produces exact canonical content."""
    paths, ledger = _create_test_run(tmp_path, "unlink-fail-test")

    claim1 = ClaimRecord(
        id="c-001",
        claim="Claim one text.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "Excerpt 1")],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(claim1)

    # Initial query builds index with c-001
    assert [c.id for c in ledger.query()] == ["c-001"]
    assert paths.index_sqlite.exists()

    # Append claim2 to JSONL
    claim2 = ClaimRecord(
        id="c-002",
        claim="Claim two text.",
        type=ClaimType.inference,
        confidence=Confidence.medium,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.tentative,
        topic="memory",
        researcher="bob",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 2", "Excerpt 2")],
        created_at="2026-09-16T12:05:00Z",
    )
    ledger.append_claim(claim2)

    # Monkeypatch unlink to simulate file-deletion failure
    orig_unlink = Path.unlink

    def fail_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self.name.startswith("index.sqlite"):
            raise OSError("Simulated unlink failure")
        orig_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    # Rebuild must not fail with duplicate id / UNIQUE constraint or merge errors
    ledger.index.rebuild()

    # Index now contains exactly the canonical records (c-001 and c-002)
    results = ledger.index.query()
    assert [c.id for c in results] == ["c-001", "c-002"]
    assert results == ledger.scan()

    # Test claim deletion from JSONL: rewrite claims.jsonl with only c-002
    paths.claims.write_text(to_json(claim2) + "\n", encoding="utf-8")
    assert not ledger.index.is_fresh()

    # Rebuild with unlink still failing: must drop tables so stale c-001 is purged
    ledger.index.rebuild()

    results_after_deletion = ledger.index.query()
    assert [c.id for c in results_after_deletion] == ["c-002"]
    assert results_after_deletion == ledger.scan()


# ---------------------------------------------------------------------------
# 10. Fallback to Pure Scan When SQLite Unavailable & Corruption Visibility (Finding 2)
# ---------------------------------------------------------------------------


def test_sqlite_unavailable_falls_back_to_scan_on_open_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify Ledger.query falls back to scan when SQLite open fails."""
    paths, ledger = _create_test_run(tmp_path, "sqlite-open-fail-test")
    claim = ClaimRecord(
        id="c-001",
        claim="Claim text.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "Excerpt 1")],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(claim)

    def fail_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("Simulated sqlite open error")

    monkeypatch.setattr(sqlite3, "connect", fail_connect)

    # Valid JSONL remains queryable via pure scan
    results = ledger.query()
    assert [c.id for c in results] == ["c-001"]
    assert results == ledger.scan()

    # Query with filters still works via scan
    q_filtered = ClaimQuery(topic="memory", status=ClaimStatus.supported)
    assert [c.id for c in ledger.query(q_filtered)] == ["c-001"]
    assert ledger.query(ClaimQuery(topic="other")) == []


def test_sqlite_unavailable_falls_back_to_scan_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify Ledger.query falls back to scan when SQLite rebuild write fails."""
    paths, ledger = _create_test_run(tmp_path, "sqlite-write-fail-test")
    claim = ClaimRecord(
        id="c-001",
        claim="Claim text.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "Excerpt 1")],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(claim)

    orig_connect = sqlite3.connect

    def fail_connect(*args: object, **kwargs: object) -> object:
        real_conn = orig_connect(*args, **kwargs)

        class FailingCursor:
            def __init__(self, cur: sqlite3.Cursor) -> None:
                self._cur = cur

            def executescript(self, sql: str) -> object:
                if "CREATE TABLE" in sql:
                    raise sqlite3.DatabaseError("Simulated disk I/O error on schema creation")
                return self._cur.executescript(sql)

            def __getattr__(self, name: str) -> object:
                return getattr(self._cur, name)

        class FailingConnection:
            def __init__(self, c: sqlite3.Connection) -> None:
                self._c = c

            def cursor(self) -> FailingCursor:
                return FailingCursor(self._c.cursor())

            def __enter__(self) -> FailingConnection:
                self._c.__enter__()
                return self

            def __exit__(self, *a: object) -> object:
                return self._c.__exit__(*a)

            def __getattr__(self, name: str) -> object:
                return getattr(self._c, name)

        return FailingConnection(real_conn)

    monkeypatch.setattr(sqlite3, "connect", fail_connect)

    results = ledger.query()
    assert [c.id for c in results] == ["c-001"]
    assert results == ledger.scan()


def test_sqlite_unavailable_falls_back_to_scan_on_query_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify Ledger.query falls back to scan when SQLite query execution fails."""
    paths, ledger = _create_test_run(tmp_path, "sqlite-query-fail-test")
    claim = ClaimRecord(
        id="c-001",
        claim="Claim text.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "Excerpt 1")],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(claim)

    # Initial query builds a fresh index
    assert [c.id for c in ledger.query()] == ["c-001"]

    orig_connect = sqlite3.connect

    def fail_connect(*args: object, **kwargs: object) -> object:
        real_conn = orig_connect(*args, **kwargs)

        class FailingCursor:
            def __init__(self, cur: sqlite3.Cursor) -> None:
                self._cur = cur

            def execute(self, sql: str, *a: object, **kw: object) -> object:
                if "SELECT c.json" in sql:
                    raise sqlite3.OperationalError("Simulated database table locked error")
                return self._cur.execute(sql, *a, **kw)

            def __getattr__(self, name: str) -> object:
                return getattr(self._cur, name)

        class FailingConnection:
            def __init__(self, c: sqlite3.Connection) -> None:
                self._c = c

            def cursor(self) -> FailingCursor:
                return FailingCursor(self._c.cursor())

            def __enter__(self) -> FailingConnection:
                self._c.__enter__()
                return self

            def __exit__(self, *a: object) -> object:
                return self._c.__exit__(*a)

            def __getattr__(self, name: str) -> object:
                return getattr(self._c, name)

        return FailingConnection(real_conn)

    monkeypatch.setattr(sqlite3, "connect", fail_connect)

    # Query fails on SQLite, falls back to scan
    results = ledger.query()
    assert [c.id for c in results] == ["c-001"]
    assert results == ledger.scan()


def test_sqlite_unavailable_surfaces_ledger_corrupt_error_on_malformed_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify malformed JSONL still raises LedgerCorruptError when SQLite is unavailable."""
    paths, ledger = _create_test_run(tmp_path, "sqlite-fail-corrupt-jsonl-test")
    claim = ClaimRecord(
        id="c-001",
        claim="Valid 1",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "T1")],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(claim)

    # Append corrupt line
    with open(paths.claims, "a", encoding="utf-8") as f:
        f.write("{invalid json line\n")

    def fail_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("Simulated sqlite open error")

    monkeypatch.setattr(sqlite3, "connect", fail_connect)

    # Must raise LedgerCorruptError naming file and line, not catch or hide it
    with pytest.raises(LedgerCorruptError) as exc_info:
        ledger.query()

    err = exc_info.value
    assert err.path == paths.claims
    assert err.line_number == 2


def test_sqlite_unavailable_surfaces_corrupt_contradictions_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify malformed contradictions.jsonl raises LedgerCorruptError when SQLite fails."""
    paths, ledger = _create_test_run(tmp_path, "sqlite-fail-corrupt-contra-test")

    with open(paths.contradictions, "a", encoding="utf-8") as f:
        f.write('{"not": "valid contradiction"}\n')

    def fail_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("Simulated sqlite open error")

    monkeypatch.setattr(sqlite3, "connect", fail_connect)

    with pytest.raises(LedgerCorruptError) as exc_info:
        ledger.query()

    err = exc_info.value
    assert err.path == paths.contradictions
    assert err.line_number == 1


def test_query_does_not_catch_invalid_inputs():
    """Verify Ledger.query and scan reject invalid inputs with TypeError/ValueError directly."""
    ledger = _fixture_ledger()

    with pytest.raises(TypeError, match="Expected ClaimQuery"):
        ledger.query("not-a-query")  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="Expected ClaimQuery"):
        ledger.scan("not-a-query")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Invalid Confidence"):
        ledger.query(ClaimQuery(confidence="ultra"))  # type: ignore[arg-type]
