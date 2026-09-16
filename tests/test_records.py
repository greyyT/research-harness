"""Tests for record schemas and serialization validation (PLAN.md §4.1, S1)."""

import json
import pytest

from research_corpus.records import (
    AdmissionDecision,
    ClaimRecord,
    ClaimStatus,
    ClaimType,
    Confidence,
    ContradictionRecord,
    EvidenceRef,
    SourceKind,
    SourceRecord,
    SourceTier,
    decode_source_record,
    from_json,
    to_json,
)


def test_exact_seven_claim_statuses():
    """Verify exact seven claim statuses from PRD §14."""
    expected = {
        "supported",
        "tentative",
        "contradicted",
        "needs_more_evidence",
        "source_too_weak",
        "unresolved",
        "rejected",
    }
    actual = {status.value for status in ClaimStatus}
    assert actual == expected
    assert len(ClaimStatus) == 7


def test_enums():
    """Verify ClaimType, Confidence, SourceTier, SourceKind, and AdmissionDecision."""
    assert len(ClaimType) == 5
    assert len(Confidence) == 3
    assert len(SourceTier) == 3
    assert len(SourceKind) == 15

    # Finding 1: exactly admitted and rejected
    assert len(AdmissionDecision) == 2
    assert {d.value for d in AdmissionDecision} == {"admitted", "rejected"}
    assert AdmissionDecision.admitted == "admitted"
    assert AdmissionDecision.rejected == "rejected"


def test_round_trip_evidence_ref():
    """Verify EvidenceRef round-trips through to_json and from_json."""
    ref = EvidenceRef(
        source_id="paper-001",
        evidence_type="benchmark",
        locator="Section 4.2, p. 7",
        excerpt="Graph memory improves multi-hop retrieval by 23%.",
    )
    serialized = to_json(ref)
    assert "\n" not in serialized

    restored = from_json(EvidenceRef, serialized)
    assert restored == ref


def test_round_trip_claim_record():
    """Verify ClaimRecord round-trips deterministically through to_json and from_json."""
    ref = EvidenceRef(
        source_id="source-1",
        evidence_type="paper",
        locator="p. 3",
        excerpt="Direct evidence text.",
    )
    claim = ClaimRecord(
        id="claim-042",
        claim="Graph memory improves multi-hop retrieval in benchmark X.",
        type=ClaimType.fact,
        confidence=Confidence.medium,
        sources=["source-1"],
        caveats=["Only evaluated on benchmark X"],
        conflicts=["claim-061"],
        status=ClaimStatus.supported,
        topic="agent-memory",
        researcher="architecture-researcher",
        evidence=[ref],
        created_at="2026-09-16T12:00:00Z",
    )

    json_str = to_json(claim)
    assert "\n" not in json_str

    restored = from_json(ClaimRecord, json_str)
    assert restored == claim
    assert restored.sources == ["source-1"]
    assert restored.status == ClaimStatus.supported
    assert restored.type == ClaimType.fact
    assert restored.confidence == Confidence.medium


def test_claim_record_derives_sources_if_omitted():
    """Verify sources is derived from evidence[].source_id order of appearance if omitted."""
    data = {
        "id": "claim-001",
        "claim": "Derived sources test.",
        "type": "fact",
        "confidence": "high",
        "status": "supported",
        "topic": "test",
        "researcher": "tester",
        "created_at": "2026-09-16T12:00:00Z",
        "evidence": [
            {
                "source_id": "src-a",
                "evidence_type": "benchmark",
                "locator": "line 1",
                "excerpt": "Excerpt A",
            },
            {
                "source_id": "src-b",
                "evidence_type": "benchmark",
                "locator": "line 2",
                "excerpt": "Excerpt B",
            },
            {
                "source_id": "src-a",  # Duplicate should be deduped in sources
                "evidence_type": "benchmark",
                "locator": "line 3",
                "excerpt": "Excerpt A2",
            },
        ],
    }
    claim = from_json(ClaimRecord, json.dumps(data))
    assert claim.sources == ["src-a", "src-b"]


def test_claim_record_accepts_matching_supplied_sources():
    """Verify supplied sources matching evidence sources in order is accepted."""
    data = {
        "id": "claim-001",
        "claim": "Matching sources test.",
        "type": "fact",
        "confidence": "high",
        "status": "supported",
        "topic": "test",
        "researcher": "tester",
        "created_at": "2026-09-16T12:00:00Z",
        "sources": ["src-a", "src-b"],
        "evidence": [
            {
                "source_id": "src-a",
                "evidence_type": "benchmark",
                "locator": "line 1",
                "excerpt": "Excerpt A",
            },
            {
                "source_id": "src-b",
                "evidence_type": "benchmark",
                "locator": "line 2",
                "excerpt": "Excerpt B",
            },
        ],
    }
    claim = from_json(ClaimRecord, json.dumps(data))
    assert claim.sources == ["src-a", "src-b"]


def test_claim_record_rejects_extra_supplied_source():
    """Verify ClaimRecord rejects extra supplied source IDs not in evidence (Finding 6)."""
    data = {
        "id": "claim-001",
        "claim": "Extra source test.",
        "type": "fact",
        "confidence": "high",
        "status": "supported",
        "topic": "test",
        "researcher": "tester",
        "created_at": "2026-09-16T12:00:00Z",
        "sources": ["src-a", "src-extra"],
        "evidence": [
            {
                "source_id": "src-a",
                "evidence_type": "benchmark",
                "locator": "line 1",
                "excerpt": "Excerpt A",
            }
        ],
    }
    with pytest.raises(ValueError, match="does not match evidence source ids"):
        from_json(ClaimRecord, json.dumps(data))


def test_claim_record_rejects_missing_supplied_source():
    """Verify ClaimRecord rejects supplied sources missing an evidence source ID (Finding 6)."""
    data = {
        "id": "claim-001",
        "claim": "Missing source test.",
        "type": "fact",
        "confidence": "high",
        "status": "supported",
        "topic": "test",
        "researcher": "tester",
        "created_at": "2026-09-16T12:00:00Z",
        "sources": ["src-a"],
        "evidence": [
            {
                "source_id": "src-a",
                "evidence_type": "benchmark",
                "locator": "line 1",
                "excerpt": "Excerpt A",
            },
            {
                "source_id": "src-b",
                "evidence_type": "benchmark",
                "locator": "line 2",
                "excerpt": "Excerpt B",
            },
        ],
    }
    with pytest.raises(ValueError, match="does not match evidence source ids"):
        from_json(ClaimRecord, json.dumps(data))


def test_claim_record_rejects_out_of_order_supplied_sources():
    """Verify ClaimRecord rejects supplied sources with wrong ordering (Finding 6)."""
    data = {
        "id": "claim-001",
        "claim": "Out-of-order source test.",
        "type": "fact",
        "confidence": "high",
        "status": "supported",
        "topic": "test",
        "researcher": "tester",
        "created_at": "2026-09-16T12:00:00Z",
        "sources": ["src-b", "src-a"],
        "evidence": [
            {
                "source_id": "src-a",
                "evidence_type": "benchmark",
                "locator": "line 1",
                "excerpt": "Excerpt A",
            },
            {
                "source_id": "src-b",
                "evidence_type": "benchmark",
                "locator": "line 2",
                "excerpt": "Excerpt B",
            },
        ],
    }
    with pytest.raises(ValueError, match="does not match evidence source ids"):
        from_json(ClaimRecord, json.dumps(data))


def test_round_trip_contradiction_record():
    """Verify ContradictionRecord round-trips through to_json and from_json."""
    record = ContradictionRecord(
        id="contra-001",
        claim_ids=["claim-001", "claim-002"],
        description="Paper A claims 90% accuracy while Paper B claims 45% on the same benchmark.",
        status=ClaimStatus.contradicted,
        created_at="2026-09-16T12:00:00Z",
    )
    json_str = to_json(record)
    restored = from_json(ContradictionRecord, json_str)
    assert restored == record
    assert restored.status == ClaimStatus.contradicted


def test_round_trip_source_record_admitted():
    """Verify admitted SourceRecord round-trips through to_json and from_json."""
    record = SourceRecord(
        id="paper-mem-2024-abcd1234",
        kind=SourceKind.paper,
        tier=SourceTier.A,
        url="https://arxiv.org/abs/2401.00001",
        path=None,
        title="Agent Memory Systems",
        admitted=True,
        reason="Peer-reviewed primary paper",
        declared_by="scout",
        decided_at="2026-09-16T12:00:00Z",
        corpus_ref={"notebooklm": "nb-src-uuid-1"},
    )
    json_str = to_json(record)
    restored = from_json(SourceRecord, json_str)
    assert restored == record
    assert restored.admitted is True
    assert restored.tier == SourceTier.A
    assert restored.corpus_ref == {"notebooklm": "nb-src-uuid-1"}


def test_round_trip_source_record_rejected():
    """Verify rejected SourceRecord round-trips through to_json and from_json."""
    record = SourceRecord(
        id="blog-post-5678ef01",
        kind=SourceKind.blog_post,
        tier=None,
        url="https://medium.com/@user/unsupported-claim",
        path=None,
        title="My Thoughts on Agents",
        admitted=False,
        reason="Unsubstantiated speculation",
        declared_by="researcher-1",
        decided_at="2026-09-16T12:05:00Z",
        corpus_ref={},
    )
    json_str = to_json(record)
    restored = from_json(SourceRecord, json_str)
    assert restored == record
    assert restored.admitted is False
    assert restored.tier is None


def test_decode_source_record():
    """Verify decode_source_record decodes valid dict and validates input."""
    raw = {
        "id": "paper-123",
        "kind": "paper",
        "tier": "A",
        "url": "https://example.com",
        "path": None,
        "title": "Title",
        "admitted": True,
        "reason": "Peer-reviewed",
        "declared_by": "scout",
        "decided_at": "2026-09-16T12:00:00Z",
        "corpus_ref": {"notebooklm": "uuid-1"},
    }
    record = decode_source_record(raw)
    assert record.id == "paper-123"
    assert record.kind == SourceKind.paper
    assert record.tier == SourceTier.A
    assert record.admitted is True

    # Non-dict raises ValueError
    with pytest.raises(ValueError, match="SourceRecord data must be a dict"):
        decode_source_record("not a dict")

    # Missing kind raises ValueError
    bad = dict(raw)
    del bad["kind"]
    with pytest.raises(ValueError, match="Field 'kind' is required"):
        decode_source_record(bad)


def test_decode_rejects_bad_status():
    """Verify decode rejects an invalid ClaimStatus."""
    data = {
        "id": "claim-001",
        "claim": "Bad status test.",
        "type": "fact",
        "confidence": "high",
        "status": "not_a_valid_status",
        "topic": "test",
        "researcher": "tester",
        "created_at": "2026-09-16T12:00:00Z",
        "evidence": [],
    }
    with pytest.raises(ValueError, match="Invalid ClaimStatus"):
        from_json(ClaimRecord, json.dumps(data))

    contra_data = {
        "id": "contra-001",
        "claim_ids": ["c1", "c2"],
        "description": "desc",
        "status": "bogus_status",
        "created_at": "2026-09-16T12:00:00Z",
    }
    with pytest.raises(ValueError, match="Invalid ClaimStatus"):
        from_json(ContradictionRecord, json.dumps(contra_data))


def test_decode_rejects_bad_tier():
    """Verify decode rejects an invalid SourceTier."""
    data = {
        "id": "src-001",
        "kind": "paper",
        "tier": "D",  # Invalid tier
        "title": "Title",
        "admitted": True,
        "reason": "Reason",
        "declared_by": "scout",
        "decided_at": "2026-09-16T12:00:00Z",
    }
    with pytest.raises(ValueError, match="Invalid SourceTier"):
        from_json(SourceRecord, json.dumps(data))


def test_decode_rejects_missing_or_empty_id():
    """Verify decode rejects missing or whitespace-only id across record types."""
    with pytest.raises(ValueError, match="Field 'id'"):
        from_json(
            ClaimRecord,
            json.dumps({
                "claim": "No id",
                "type": "fact",
                "confidence": "high",
                "status": "supported",
                "created_at": "2026-09-16T12:00:00Z",
            }),
        )

    with pytest.raises(ValueError, match="Field 'id'"):
        from_json(
            ClaimRecord,
            json.dumps({
                "id": "   ",
                "claim": "Empty id",
                "type": "fact",
                "confidence": "high",
                "status": "supported",
                "created_at": "2026-09-16T12:00:00Z",
            }),
        )

    with pytest.raises(ValueError, match="Field 'id'"):
        from_json(
            SourceRecord,
            json.dumps({
                "id": "",
                "kind": "paper",
                "title": "Title",
                "admitted": True,
                "declared_by": "scout",
                "decided_at": "2026-09-16T12:00:00Z",
            }),
        )

    with pytest.raises(ValueError, match="Field 'id'"):
        from_json(
            ContradictionRecord,
            json.dumps({
                "id": "",
                "claim_ids": ["c1"],
                "description": "desc",
                "status": "contradicted",
                "created_at": "2026-09-16T12:00:00Z",
            }),
        )


def test_decode_rejects_empty_excerpt():
    """Verify decode rejects empty or whitespace-only excerpt in EvidenceRef."""
    with pytest.raises(ValueError, match="Field 'excerpt'"):
        from_json(
            EvidenceRef,
            json.dumps({
                "source_id": "src-1",
                "evidence_type": "paper",
                "locator": "p. 1",
                "excerpt": "",
            }),
        )

    with pytest.raises(ValueError, match="Field 'excerpt'"):
        from_json(
            EvidenceRef,
            json.dumps({
                "source_id": "src-1",
                "evidence_type": "paper",
                "locator": "p. 1",
                "excerpt": "   \n\t ",
            }),
        )


def test_from_json_type_and_syntax_errors():
    """Verify from_json rejects non-string input, invalid JSON, or unsupported record classes."""
    with pytest.raises(TypeError, match="Expected str"):
        from_json(ClaimRecord, 123)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Invalid JSON string"):
        from_json(ClaimRecord, "{not valid json")

    with pytest.raises(ValueError, match="Expected JSON object"):
        from_json(ClaimRecord, "[\"an\", \"array\"]")

    class UnregisteredType:
        pass

    with pytest.raises(TypeError, match="Unsupported record type"):
        from_json(UnregisteredType, "{}")  # type: ignore[arg-type]
