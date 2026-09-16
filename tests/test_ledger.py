"""Tests for Evidence Ledger JSONL store, pure scan query oracle, and seeded fixture (PLAN.md §4.3, §5 S2)."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from research_corpus.artifacts import RunPaths, init_run, load_source_index
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


# ---------------------------------------------------------------------------
# Fixture Validation
# ---------------------------------------------------------------------------


def test_seeded_fixture_satisfies_s2_contract():
    """Verify the committed seeded fixture meets all S2 requirements:

    - >= 3 admitted sources, >= 1 rejected source
    - >= 8 claims spanning all 7 statuses
    - >= 2 researchers
    - >= 3 topics
    - >= 2 evidence types
    - One contradiction pair/record
    """
    paths = RunPaths(FIXTURE_DIR.parent, FIXTURE_DIR.name)
    ledger = Ledger(paths)

    # 1. Sources check
    sources = load_source_index(paths)
    admitted_sources = [s for s in sources if s.admitted]
    rejected_sources = [s for s in sources if not s.admitted]
    assert len(admitted_sources) >= 3, f"Expected >= 3 admitted sources, got {len(admitted_sources)}"
    assert len(rejected_sources) >= 1, f"Expected >= 1 rejected source, got {len(rejected_sources)}"

    # 2. Claims count and statuses
    claims = list(ledger.iter_claims())
    assert len(claims) >= 8, f"Expected >= 8 claims, got {len(claims)}"

    statuses_present = {c.status for c in claims}
    assert statuses_present == set(ClaimStatus), "Seeded claims must span all seven ClaimStatus values"

    # 3. Researchers
    researchers_present = {c.researcher for c in claims}
    assert len(researchers_present) >= 2, f"Expected >= 2 researchers, got {len(researchers_present)}"

    # 4. Topics
    topics_present = {c.topic for c in claims}
    assert len(topics_present) >= 3, f"Expected >= 3 topics, got {len(topics_present)}"

    # 5. Evidence types
    evidence_types_present = {
        ref.evidence_type for c in claims for ref in c.evidence
    }
    assert len(evidence_types_present) >= 2, f"Expected >= 2 evidence types, got {len(evidence_types_present)}"

    # 6. Contradictions
    contradictions = list(ledger.iter_contradictions())
    assert len(contradictions) >= 1, "Expected at least 1 contradiction record"
    contra = contradictions[0]
    assert len(contra.claim_ids) == 2, "Expected contradiction record to link a pair of claims"
    assert contra.status == ClaimStatus.contradicted

    # Verify both referenced claims exist in claims.jsonl and reference each other
    claim_a = next(c for c in claims if c.id == contra.claim_ids[0])
    claim_b = next(c for c in claims if c.id == contra.claim_ids[1])
    assert claim_b.id in claim_a.conflicts
    assert claim_a.id in claim_b.conflicts


# ---------------------------------------------------------------------------
# Ledger Construction & Initialization
# ---------------------------------------------------------------------------


def test_ledger_requires_run_paths(tmp_path: Path):
    """Verify Ledger accepts only RunPaths, raising TypeError otherwise."""
    run_paths = RunPaths(tmp_path, "run-1")
    init_run(run_paths)

    ledger = Ledger(run_paths)
    assert ledger.paths == run_paths

    with pytest.raises(TypeError, match="Expected RunPaths"):
        Ledger(run_paths.run_dir)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="Expected RunPaths"):
        Ledger(str(run_paths.run_dir))  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="Expected RunPaths"):
        Ledger(12345)  # type: ignore[arg-type]


def test_ledger_empty_and_iteration(tmp_path: Path):
    """Verify ledger behaves correctly on an empty run directory."""
    paths = RunPaths(tmp_path, "empty-run")
    init_run(paths)
    ledger = Ledger(paths)

    assert list(ledger.iter_claims()) == []
    assert list(ledger.iter_contradictions()) == []
    assert list(ledger) == []
    assert ledger.scan() == []
    assert ledger.query() == []
    assert ledger.scan(ClaimQuery(topic="none")) == []


# ---------------------------------------------------------------------------
# Query Dimensions (Pure Scan Engine)
# ---------------------------------------------------------------------------


def _fixture_ledger() -> Ledger:
    return Ledger(RunPaths(FIXTURE_DIR.parent, FIXTURE_DIR.name))


def test_query_no_filters_returns_all_sorted_by_id():
    """Verify no-filter query returns all claims sorted by id."""
    ledger = _fixture_ledger()
    results = ledger.scan()
    assert len(results) == 8
    ids = [c.id for c in results]
    assert ids == sorted(ids)
    assert ids == [
        "claim-001",
        "claim-002",
        "claim-003",
        "claim-004",
        "claim-005",
        "claim-006",
        "claim-007",
        "claim-008",
    ]


def test_query_filter_topic():
    """Verify topic filtering matches exactly and preserves id sorting."""
    ledger = _fixture_ledger()

    res_mem = ledger.scan(ClaimQuery(topic="agent-memory"))
    assert [c.id for c in res_mem] == ["claim-001", "claim-002", "claim-007"]

    res_ret = ledger.scan(ClaimQuery(topic="retrieval-eval"))
    assert [c.id for c in res_ret] == ["claim-003", "claim-004", "claim-008"]

    res_comp = ledger.scan(ClaimQuery(topic="context-compaction"))
    assert [c.id for c in res_comp] == ["claim-005", "claim-006"]


def test_query_filter_confidence():
    """Verify confidence filtering matches enum values and coerced strings."""
    ledger = _fixture_ledger()

    res_high = ledger.scan(ClaimQuery(confidence=Confidence.high))
    assert [c.id for c in res_high] == ["claim-001", "claim-004"]

    # Coerced from string
    res_high_str = ledger.scan(ClaimQuery(confidence="high"))
    assert res_high_str == res_high

    res_med = ledger.scan(ClaimQuery(confidence=Confidence.medium))
    assert [c.id for c in res_med] == ["claim-002", "claim-003"]

    res_low = ledger.scan(ClaimQuery(confidence=Confidence.low))
    assert [c.id for c in res_low] == ["claim-005", "claim-006", "claim-007", "claim-008"]


def test_query_filter_researcher():
    """Verify researcher filtering matches exactly."""
    ledger = _fixture_ledger()

    res_arch = ledger.scan(ClaimQuery(researcher="architecture-researcher"))
    assert [c.id for c in res_arch] == ["claim-001", "claim-003", "claim-005", "claim-007"]

    res_bench = ledger.scan(ClaimQuery(researcher="benchmark-researcher"))
    assert [c.id for c in res_bench] == ["claim-002", "claim-004", "claim-006", "claim-008"]


def test_query_filter_source_id():
    """Verify source_id filtering checks claim.sources and evidence.source_id."""
    ledger = _fixture_ledger()

    res_arxiv = ledger.scan(ClaimQuery(source_id="src-arxiv-2401-mem"))
    assert [c.id for c in res_arxiv] == ["claim-001", "claim-003"]

    res_bench = ledger.scan(ClaimQuery(source_id="src-bench-memarena"))
    assert [c.id for c in res_bench] == ["claim-002", "claim-004"]

    res_blog = ledger.scan(ClaimQuery(source_id="src-blog-thoughts"))
    assert [c.id for c in res_blog] == ["claim-006"]

    res_none = ledger.scan(ClaimQuery(source_id="non-existent-source"))
    assert res_none == []


def test_query_filter_evidence_type():
    """Verify evidence_type filtering matches against evidence[].evidence_type."""
    ledger = _fixture_ledger()

    res_paper = ledger.scan(ClaimQuery(evidence_type="paper"))
    assert [c.id for c in res_paper] == ["claim-001", "claim-003"]

    res_bench = ledger.scan(ClaimQuery(evidence_type="benchmark"))
    assert [c.id for c in res_bench] == ["claim-002", "claim-004"]

    res_rep = ledger.scan(ClaimQuery(evidence_type="engineering_report"))
    assert [c.id for c in res_rep] == ["claim-006"]

    res_none = ledger.scan(ClaimQuery(evidence_type="video"))
    assert res_none == []


def test_query_filter_status():
    """Verify status filtering for each of the seven ClaimStatus values."""
    ledger = _fixture_ledger()

    expected_map = {
        ClaimStatus.supported: ["claim-001"],
        ClaimStatus.tentative: ["claim-002"],
        ClaimStatus.contradicted: ["claim-003", "claim-004"],
        ClaimStatus.needs_more_evidence: ["claim-005"],
        ClaimStatus.source_too_weak: ["claim-006"],
        ClaimStatus.unresolved: ["claim-007"],
        ClaimStatus.rejected: ["claim-008"],
    }
    for status, expected_ids in expected_map.items():
        res = ledger.scan(ClaimQuery(status=status))
        assert [c.id for c in res] == expected_ids, f"Mismatch for status {status}"

        # Also verify string coercion
        res_str = ledger.scan(ClaimQuery(status=status.value))
        assert res_str == res


def test_query_filter_contradiction_modes():
    """Verify all contradiction modes: 'any', 'none', claim id in conflicts, and 'contradiction:<id>'."""
    ledger = _fixture_ledger()

    # 1. "any" -> conflicts non-empty
    res_any = ledger.scan(ClaimQuery(contradiction="any"))
    assert [c.id for c in res_any] == ["claim-003", "claim-004"]

    # 2. "none" -> conflicts empty
    res_none = ledger.scan(ClaimQuery(contradiction="none"))
    assert [c.id for c in res_none] == [
        "claim-001",
        "claim-002",
        "claim-005",
        "claim-006",
        "claim-007",
        "claim-008",
    ]

    # 3. claim id in claim.conflicts
    res_c4 = ledger.scan(ClaimQuery(contradiction="claim-004"))
    assert [c.id for c in res_c4] == ["claim-003"]

    res_c3 = ledger.scan(ClaimQuery(contradiction="claim-003"))
    assert [c.id for c in res_c3] == ["claim-004"]

    res_c_unknown = ledger.scan(ClaimQuery(contradiction="claim-999"))
    assert res_c_unknown == []

    # 4. "contradiction:<id>" membership through ContradictionRecord.claim_ids
    res_contra = ledger.scan(ClaimQuery(contradiction="contradiction:contra-001"))
    assert [c.id for c in res_contra] == ["claim-003", "claim-004"]

    res_contra_missing = ledger.scan(ClaimQuery(contradiction="contradiction:contra-999"))
    assert res_contra_missing == []


def test_query_contradiction_allows_arbitrary_claim_ids(tmp_path: Path):
    """Verify contradiction filter allows arbitrary non-empty claim ids, including colons and words like conflict."""
    _, ledger = _create_test_run(tmp_path, "arbitrary-ids-test")

    # Claim with namespaced and word-like conflict IDs
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
    res_colon = ledger.scan(ClaimQuery(contradiction="urn:claim:123"))
    assert [c.id for c in res_colon] == ["c-001"]

    res_colon_2 = ledger.scan(ClaimQuery(contradiction="custom:prefix:456"))
    assert [c.id for c in res_colon_2] == ["c-001"]

    # Literal word "conflict"
    res_conflict = ledger.scan(ClaimQuery(contradiction="conflict"))
    assert [c.id for c in res_conflict] == ["c-001"]

    # Literal word "contradiction"
    res_contra_word = ledger.scan(ClaimQuery(contradiction="contradiction"))
    assert [c.id for c in res_contra_word] == ["c-001"]

    # Non-matching arbitrary id
    res_nomatch = ledger.scan(ClaimQuery(contradiction="urn:claim:999"))
    assert res_nomatch == []


def test_query_rejects_invalid_contradiction_syntax():
    """Verify invalid contradiction syntax (empty string, empty prefix ID) is rejected with ValueError."""
    with pytest.raises(ValueError, match="contradiction filter cannot be empty"):
        ClaimQuery(contradiction="")

    with pytest.raises(ValueError, match="contradiction filter cannot be empty"):
        ClaimQuery(contradiction="   ")

    with pytest.raises(ValueError, match="Invalid contradiction filter syntax"):
        ClaimQuery(contradiction="contradiction:")

    with pytest.raises(ValueError, match="Invalid contradiction filter syntax"):
        ClaimQuery(contradiction="contradiction:   ")

    with pytest.raises(ValueError, match="contradiction filter must be a string"):
        ClaimQuery(contradiction=123)  # type: ignore[arg-type]


def test_query_rejects_invalid_enum_filters():
    """Verify ClaimQuery rejects invalid status or confidence strings."""
    with pytest.raises(ValueError, match="Invalid Confidence"):
        ClaimQuery(confidence="ultra-high")

    with pytest.raises(ValueError, match="Invalid ClaimStatus"):
        ClaimQuery(status="pending")


def test_query_combined_filters_and():
    """Verify all set query filters combine with AND."""
    ledger = _fixture_ledger()

    # topic AND status
    q1 = ClaimQuery(topic="agent-memory", status=ClaimStatus.supported)
    assert [c.id for c in ledger.scan(q1)] == ["claim-001"]

    # topic AND researcher AND confidence
    q2 = ClaimQuery(
        topic="agent-memory",
        researcher="benchmark-researcher",
        confidence=Confidence.medium,
    )
    assert [c.id for c in ledger.scan(q2)] == ["claim-002"]

    # topic AND contradiction="any" AND confidence=high
    q3 = ClaimQuery(
        topic="retrieval-eval",
        contradiction="any",
        confidence=Confidence.high,
    )
    assert [c.id for c in ledger.scan(q3)] == ["claim-004"]

    # No match under combined filters returns []
    q_nomatch = ClaimQuery(
        topic="agent-memory",
        status=ClaimStatus.rejected,
    )
    assert ledger.scan(q_nomatch) == []


def test_query_scan_equivalence_in_s2():
    """Verify Ledger.query and Ledger.scan produce identical results in S2."""
    ledger = _fixture_ledger()

    queries = [
        None,
        ClaimQuery(),
        ClaimQuery(topic="agent-memory"),
        ClaimQuery(status=ClaimStatus.contradicted),
        ClaimQuery(contradiction="any"),
        ClaimQuery(researcher="architecture-researcher", confidence=Confidence.high),
    ]
    for q in queries:
        assert ledger.query(q) == ledger.scan(q)


# ---------------------------------------------------------------------------
# Appending, Invariants, and Append-Only Integrity
# ---------------------------------------------------------------------------


def _create_test_run(tmp_path: Path, slug: str) -> tuple[RunPaths, Ledger]:
    paths = RunPaths(tmp_path, slug)
    init_run(paths)

    # Seed an admitted source and a rejected source
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


def test_append_claim_success_and_append_only(tmp_path: Path):
    """Verify successful claim append writes to claims.jsonl without overwriting."""
    paths, ledger = _create_test_run(tmp_path, "append-test")

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
        evidence=[
            EvidenceRef(
                source_id="src-valid-1",
                evidence_type="paper",
                locator="p. 1",
                excerpt="Excerpt 1",
            )
        ],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(claim1)

    initial_content = paths.claims.read_text(encoding="utf-8")
    assert initial_content.count("\n") == 1
    assert "c-001" in initial_content

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
        evidence=[
            EvidenceRef(
                source_id="src-valid-1",
                evidence_type="paper",
                locator="p. 2",
                excerpt="Excerpt 2",
            )
        ],
        created_at="2026-09-16T12:05:00Z",
    )
    ledger.append(claim2)  # Testing generic append() dispatch

    updated_content = paths.claims.read_text(encoding="utf-8")
    assert updated_content.startswith(initial_content)
    assert updated_content.count("\n") == 2
    assert "c-002" in updated_content

    # Check both claims read back
    claims = list(ledger.iter_claims())
    assert [c.id for c in claims] == ["c-001", "c-002"]


def test_append_claim_rejects_duplicate_id(tmp_path: Path):
    """Verify ledger rejects appending a claim with a duplicate id."""
    _, ledger = _create_test_run(tmp_path, "dup-id-test")

    claim = ClaimRecord(
        id="c-dup",
        claim="Original claim.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[
            EvidenceRef(
                source_id="src-valid-1",
                evidence_type="paper",
                locator="p. 1",
                excerpt="Excerpt 1",
            )
        ],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(claim)

    duplicate_claim = ClaimRecord(
        id="c-dup",
        claim="Different text but duplicate ID.",
        type=ClaimType.fact,
        confidence=Confidence.low,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="bob",
        evidence=[
            EvidenceRef(
                source_id="src-valid-1",
                evidence_type="paper",
                locator="p. 1",
                excerpt="Excerpt 1",
            )
        ],
        created_at="2026-09-16T12:01:00Z",
    )
    with pytest.raises(ValueError, match="Duplicate claim id: 'c-dup'"):
        ledger.append_claim(duplicate_claim)


def test_append_claim_rejects_unknown_source_id(tmp_path: Path):
    """Verify ledger rejects a claim citing a source_id not present in source-index.json."""
    _, ledger = _create_test_run(tmp_path, "unknown-source-test")

    claim = ClaimRecord(
        id="c-unk",
        claim="Claim with unknown source.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-unknown-999"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[
            EvidenceRef(
                source_id="src-unknown-999",
                evidence_type="paper",
                locator="p. 1",
                excerpt="Excerpt",
            )
        ],
        created_at="2026-09-16T12:00:00Z",
    )
    with pytest.raises(ValueError, match="Unknown evidence source id: 'src-unknown-999'"):
        ledger.append_claim(claim)


def test_append_claim_rejects_rejected_source_id(tmp_path: Path):
    """Verify ledger rejects a claim citing a source marked admitted=False (I7)."""
    _, ledger = _create_test_run(tmp_path, "rejected-source-test")

    claim = ClaimRecord(
        id="c-rej",
        claim="Claim citing rejected source.",
        type=ClaimType.fact,
        confidence=Confidence.low,
        sources=["src-rejected-2"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[
            EvidenceRef(
                source_id="src-rejected-2",
                evidence_type="blog_post",
                locator="p. 1",
                excerpt="Excerpt",
            )
        ],
        created_at="2026-09-16T12:00:00Z",
    )
    with pytest.raises(ValueError, match="Evidence source id 'src-rejected-2' was rejected"):
        ledger.append_claim(claim)


def test_append_claim_rejects_research_state_as_source(tmp_path: Path):
    """Verify ledger rejects research-state.md when cited as an unadmitted/unknown source (I7)."""
    _, ledger = _create_test_run(tmp_path, "research-state-source-test")

    claim = ClaimRecord(
        id="c-state",
        claim="Claim citing research-state.md.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["research-state.md"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("research-state.md", "report", "p. 1", "Synthetic text")],
        created_at="2026-09-16T12:00:00Z",
    )
    with pytest.raises(ValueError, match="Unknown evidence source id: 'research-state.md'"):
        ledger.append_claim(claim)


def test_append_claim_empty_evidence_rules(tmp_path: Path):
    """Verify empty evidence[] is valid ONLY for needs_more_evidence, unresolved, or rejected (I7)."""
    _, ledger = _create_test_run(tmp_path, "empty-evidence-test")

    # Disallowed statuses with empty evidence
    disallowed_statuses = [
        ClaimStatus.supported,
        ClaimStatus.tentative,
        ClaimStatus.contradicted,
        ClaimStatus.source_too_weak,
    ]
    for idx, status in enumerate(disallowed_statuses):
        claim = ClaimRecord(
            id=f"c-empty-{idx}",
            claim=f"Empty evidence disallowed for {status.value}.",
            type=ClaimType.fact,
            confidence=Confidence.medium,
            sources=[],
            caveats=[],
            conflicts=[],
            status=status,
            topic="memory",
            researcher="alice",
            evidence=[],
            created_at="2026-09-16T12:00:00Z",
        )
        with pytest.raises(ValueError, match="must have at least one evidence reference"):
            ledger.append_claim(claim)

    # Allowed statuses with empty evidence
    allowed_statuses = [
        ClaimStatus.needs_more_evidence,
        ClaimStatus.unresolved,
        ClaimStatus.rejected,
    ]
    for idx, status in enumerate(allowed_statuses, start=10):
        claim = ClaimRecord(
            id=f"c-empty-{idx}",
            claim=f"Empty evidence allowed for {status.value}.",
            type=ClaimType.hypothesis,
            confidence=Confidence.low,
            sources=[],
            caveats=[],
            conflicts=[],
            status=status,
            topic="memory",
            researcher="alice",
            evidence=[],
            created_at="2026-09-16T12:00:00Z",
        )
        ledger.append_claim(claim)

    appended_ids = [c.id for c in ledger.iter_claims()]
    assert appended_ids == ["c-empty-10", "c-empty-11", "c-empty-12"]


def test_append_contradiction_success_and_validations(tmp_path: Path):
    """Verify appending ContradictionRecord enforces that referenced claims exist and rejects duplicates."""
    _, ledger = _create_test_run(tmp_path, "contra-test")

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
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "Text A")],
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
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 2", "Text B")],
        created_at="2026-09-16T12:05:00Z",
    )
    ledger.append_claim(claim1)
    ledger.append_claim(claim2)

    # 1. Contradiction referencing unknown claim ID fails
    bad_contra = ContradictionRecord(
        id="contra-bad",
        claim_ids=["c-001", "c-unknown"],
        description="References non-existent claim",
        status=ClaimStatus.contradicted,
        created_at="2026-09-16T12:10:00Z",
    )
    with pytest.raises(ValueError, match="references unknown claim id: 'c-unknown'"):
        ledger.append_contradiction(bad_contra)

    # 2. Contradiction with empty claim_ids fails
    empty_contra = ContradictionRecord(
        id="contra-empty",
        claim_ids=[],
        description="Empty claims",
        status=ClaimStatus.contradicted,
        created_at="2026-09-16T12:10:00Z",
    )
    with pytest.raises(ValueError, match="claim_ids cannot be empty"):
        ledger.append_contradiction(empty_contra)

    # 3. Successful append
    good_contra = ContradictionRecord(
        id="contra-1",
        claim_ids=["c-001", "c-002"],
        description="Disagreement between A and B",
        status=ClaimStatus.contradicted,
        created_at="2026-09-16T12:10:00Z",
    )
    ledger.append_contradiction(good_contra)
    assert [c.id for c in ledger.iter_contradictions()] == ["contra-1"]

    # 4. Duplicate contradiction ID fails
    dup_contra = ContradictionRecord(
        id="contra-1",
        claim_ids=["c-001", "c-002"],
        description="Duplicate",
        status=ClaimStatus.contradicted,
        created_at="2026-09-16T12:11:00Z",
    )
    with pytest.raises(ValueError, match="Duplicate contradiction id: 'contra-1'"):
        ledger.append_contradiction(dup_contra)


# ---------------------------------------------------------------------------
# Direct Instance Validation & Ledger Poisoning Prevention (Finding 1)
# ---------------------------------------------------------------------------


def test_append_claim_validates_direct_instance_via_codec(tmp_path: Path):
    """Verify invalid direct ClaimRecord instances fail validation and leave claims.jsonl byte-for-byte unchanged."""
    paths, ledger = _create_test_run(tmp_path, "codec-claim-validation-test")

    # Initial valid claim
    valid_claim = ClaimRecord(
        id="c-valid",
        claim="Valid initial claim.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "Initial text")],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(valid_claim)
    initial_bytes = paths.claims.read_bytes()

    # 1. Invalid empty/whitespace id
    bad_id_claim = replace(valid_claim, id="   ")
    with pytest.raises(ValueError, match="Field 'id'"):
        ledger.append_claim(bad_id_claim)
    assert paths.claims.read_bytes() == initial_bytes

    # 2. Source / evidence mismatch (sources doesn't match evidence source_ids)
    mismatch_claim = replace(valid_claim, id="c-mismatch", sources=["src-other"])
    with pytest.raises(ValueError, match="does not match evidence source ids"):
        ledger.append_claim(mismatch_claim)
    assert paths.claims.read_bytes() == initial_bytes

    # 3. Invalid evidence excerpt (empty string)
    bad_ref = EvidenceRef("src-valid-1", "paper", "p. 1", "")
    bad_excerpt_claim = replace(valid_claim, id="c-bad-excerpt", evidence=[bad_ref])
    with pytest.raises(ValueError, match="Field 'excerpt'"):
        ledger.append_claim(bad_excerpt_claim)
    assert paths.claims.read_bytes() == initial_bytes


def test_append_contradiction_validates_direct_instance_via_codec(tmp_path: Path):
    """Verify invalid direct ContradictionRecord instances fail validation and leave contradictions.jsonl byte-for-byte unchanged."""
    paths, ledger = _create_test_run(tmp_path, "codec-contra-validation-test")

    # Initial valid contradiction
    valid_claim = ClaimRecord(
        id="c-001",
        claim="Valid claim.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 1", "Text")],
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_claim(valid_claim)

    valid_contra = ContradictionRecord(
        id="contra-001",
        claim_ids=["c-001"],
        description="Initial description",
        status=ClaimStatus.contradicted,
        created_at="2026-09-16T12:00:00Z",
    )
    ledger.append_contradiction(valid_contra)
    initial_bytes = paths.contradictions.read_bytes()

    # 1. Invalid empty/whitespace id
    bad_id_contra = replace(valid_contra, id="")
    with pytest.raises(ValueError, match="Field 'id'"):
        ledger.append_contradiction(bad_id_contra)
    assert paths.contradictions.read_bytes() == initial_bytes

    # 2. Invalid empty created_at
    bad_date_contra = replace(valid_contra, id="contra-002", created_at="  ")
    with pytest.raises(ValueError, match="Field 'created_at'"):
        ledger.append_contradiction(bad_date_contra)
    assert paths.contradictions.read_bytes() == initial_bytes


# ---------------------------------------------------------------------------
# Corruption Handling (LedgerCorruptError)
# ---------------------------------------------------------------------------


def test_corrupt_line_in_claims_raises_ledger_corrupt_error(tmp_path: Path):
    """Verify malformed JSONL line in claims.jsonl raises LedgerCorruptError with exact file and line, returning NO partial results."""
    paths, ledger = _create_test_run(tmp_path, "corrupt-claims-test")

    # Write 1 valid claim, 1 corrupt line, 1 valid claim
    valid_claim_1 = ClaimRecord(
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
    valid_claim_3 = ClaimRecord(
        id="c-003",
        claim="Valid 3",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=["src-valid-1"],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="memory",
        researcher="alice",
        evidence=[EvidenceRef("src-valid-1", "paper", "p. 3", "T3")],
        created_at="2026-09-16T12:02:00Z",
    )

    lines = [
        to_json(valid_claim_1),
        "{bad json line at number 2",
        to_json(valid_claim_3),
    ]
    paths.claims.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Must raise LedgerCorruptError naming file and line 2; never return partial results
    with pytest.raises(LedgerCorruptError) as exc_info:
        ledger.scan()

    err = exc_info.value
    assert err.path == paths.claims
    assert err.line_number == 2
    assert "line 2" in str(err)
    assert str(paths.claims) in str(err)

    # Same check for iter_claims
    with pytest.raises(LedgerCorruptError) as exc_info2:
        list(ledger.iter_claims())
    assert exc_info2.value.line_number == 2


def test_empty_line_in_claims_raises_ledger_corrupt_error(tmp_path: Path):
    """Verify blank line in claims.jsonl is treated as corrupt."""
    paths, ledger = _create_test_run(tmp_path, "blank-line-claims-test")

    valid_claim = ClaimRecord(
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

    content = to_json(valid_claim) + "\n\n" + to_json(valid_claim) + "\n"
    paths.claims.write_text(content, encoding="utf-8")

    with pytest.raises(LedgerCorruptError) as exc_info:
        ledger.scan()
    assert exc_info.value.line_number == 2


def test_corrupt_line_in_contradictions_raises_ledger_corrupt_error(tmp_path: Path):
    """Verify malformed line in contradictions.jsonl raises LedgerCorruptError with exact file and line."""
    paths, ledger = _create_test_run(tmp_path, "corrupt-contra-test")

    paths.contradictions.write_text("{\"not\": \"valid contradiction schema\"}\n", encoding="utf-8")

    with pytest.raises(LedgerCorruptError) as exc_info:
        list(ledger.iter_contradictions())

    err = exc_info.value
    assert err.path == paths.contradictions
    assert err.line_number == 1
    assert "line 1" in str(err)
