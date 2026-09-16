"""End-to-end acceptance test for the research corpus layer (PLAN.md §5 S8a).

Drives the in-process API against a tmp_path run with deterministic fixtures:
- init_run and verify synthetic research-state.md banner + refusal as source candidate
- admit Tier-A/B/C sources from three fixture files + reject a fourth candidate
- ingest into LocalStubCorpus, re-ingest one to verify reused=True and unchanged file count
- corpus.query with citations attributed to correct source ids
- append two ClaimRecords citing query citations as a contradicted pair + ContradictionRecord
- negative check: appending a claim citing the rejected source id is refused by the ledger
- query all seven §14 dimensions via Ledger.query
- verify LedgerIndex.query(q) == Ledger.scan(q) across multiple query combinations
"""

from pathlib import Path
import pytest

from research_corpus.admission import (
    SourceCandidate,
    SourceIndex,
    admit,
    reject,
)
from research_corpus.artifacts import (
    SYNTHETIC_SUMMARY_BANNER,
    RunPaths,
    init_run,
)
from research_corpus.corpus.stub import LocalStubCorpus
from research_corpus.index import LedgerIndex
from research_corpus.ledger import ClaimQuery, Ledger
from research_corpus.records import (
    ClaimRecord,
    ClaimStatus,
    ClaimType,
    Confidence,
    ContradictionRecord,
    EvidenceRef,
    SourceKind,
    SourceTier,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "sources"


def test_end_to_end_corpus_acceptance_slice(tmp_path: Path) -> None:
    """Full deterministic in-process acceptance path covering PLAN §5 S8a."""
    run_slug = "memory-systems-e2e"
    paths = init_run(RunPaths(tmp_path, run_slug))

    # -------------------------------------------------------------------------
    # 1. State artifact check and refusal of research-state.md as source
    # -------------------------------------------------------------------------
    assert paths.research_state.is_file()
    state_content = paths.research_state.read_text(encoding="utf-8")
    assert state_content.startswith(SYNTHETIC_SUMMARY_BANNER)

    source_index = SourceIndex(paths)
    candidate_state_file = SourceCandidate(
        path=str(paths.research_state),
        title="Synthetic Research State",
    )
    with pytest.raises(ValueError, match="research-state.md is a synthetic summary"):
        admit(source_index, candidate_state_file, SourceKind.engineering_report, declared_by="tester")

    candidate_state_url = SourceCandidate(
        url="https://example.com/research-state.md",
        title="Web Research State",
    )
    with pytest.raises(ValueError, match="research-state.md is a synthetic summary"):
        admit(source_index, candidate_state_url, SourceKind.engineering_report, declared_by="tester")

    # -------------------------------------------------------------------------
    # 2. Admit Tier A, B, C sources from fixtures + reject fourth candidate
    # -------------------------------------------------------------------------
    file_a = FIXTURES_DIR / "tier-a-paper.txt"
    file_b = FIXTURES_DIR / "tier-b-report.txt"
    file_c = FIXTURES_DIR / "tier-c-blog.txt"

    cand_a = SourceCandidate(
        path=str(file_a),
        title="MemoryArena Hierarchical Memory Indexing Paper",
    )
    admitted_a = admit(
        source_index,
        cand_a,
        SourceKind.paper,
        declared_by="researcher-alpha",
        reason="Peer-reviewed benchmark paper on agent memory indexing",
    )
    assert admitted_a.tier == SourceTier.A

    cand_b = SourceCandidate(
        path=str(file_b),
        title="Production Infrastructure Memory Evaluation",
    )
    admitted_b = admit(
        source_index,
        cand_b,
        SourceKind.engineering_report,
        declared_by="researcher-beta",
        reason="Engineering report evaluating memory indexing at scale",
    )
    assert admitted_b.tier == SourceTier.B

    cand_c = SourceCandidate(
        path=str(file_c),
        title="Notes on Hierarchical Agent Memory Failure",
    )
    admitted_c = admit(
        source_index,
        cand_c,
        SourceKind.blog_post,
        declared_by="researcher-gamma",
        reason="Blog post reporting failure to reproduce latency reduction",
    )
    assert admitted_c.tier == SourceTier.C

    # Reject fourth candidate
    cand_d = SourceCandidate(
        url="https://forum.example.com/threads/unverified-agent-memory-rumors",
        title="Unverified Forum Thread",
    )
    rejected_d = reject(
        source_index,
        cand_d,
        declared_by="scout",
        reason="Unverified anonymous forum rumor with no reproducible data",
    )
    assert not rejected_d.admitted
    assert rejected_d.tier is None

    # -------------------------------------------------------------------------
    # 3. Ingest into LocalStubCorpus, verify dedup (reused=True, unchanged files)
    # -------------------------------------------------------------------------
    corpus = LocalStubCorpus(paths)
    ref_a = corpus.ingest(admitted_a, content=file_a)
    assert not ref_a.reused
    ref_b = corpus.ingest(admitted_b, content=file_b)
    assert not ref_b.reused
    ref_c = corpus.ingest(admitted_c, content=file_c)
    assert not ref_c.reused

    stub_files = list(paths.stub_corpus_dir.glob("*.txt"))
    assert len(stub_files) == 3

    # Re-ingest admitted_a
    ref_a_reingest = corpus.ingest(admitted_a, content="ignored alternate content")
    assert ref_a_reingest.reused is True
    assert len(list(paths.stub_corpus_dir.glob("*.txt"))) == 3
    assert (paths.stub_corpus_dir / f"{admitted_a.id}.txt").read_text(encoding="utf-8") == file_a.read_text(encoding="utf-8")

    # -------------------------------------------------------------------------
    # 4. Corpus query and citation attribution
    # -------------------------------------------------------------------------
    answer = corpus.query("hierarchical memory indexing retrieval latency")
    assert answer.provider == "stub"
    assert len(answer.citations) >= 3

    cited_source_ids = {c.source_id for c in answer.citations}
    assert admitted_a.id in cited_source_ids
    assert admitted_b.id in cited_source_ids
    assert admitted_c.id in cited_source_ids

    citation_a = next(c for c in answer.citations if c.source_id == admitted_a.id)
    citation_c = next(c for c in answer.citations if c.source_id == admitted_c.id)
    assert "40 percent" in citation_a.excerpt
    assert "failed to reduce retrieval latency" in citation_c.excerpt

    # -------------------------------------------------------------------------
    # 5. Ledger: append two claims from citations, contradiction pair, record
    # -------------------------------------------------------------------------
    ledger = Ledger(paths)
    claim_1_id = "claim-01-latency-reduction"
    claim_2_id = "claim-02-latency-increase"

    claim_1 = ClaimRecord(
        id=claim_1_id,
        claim="Hierarchical memory indexing reduces agent retrieval latency by 40% on MemoryArena.",
        type=ClaimType.fact,
        confidence=Confidence.high,
        sources=[citation_a.source_id],
        caveats=["Evaluated under synthetic MemoryArena benchmark conditions"],
        conflicts=[claim_2_id],
        status=ClaimStatus.contradicted,
        topic="agent-memory-systems",
        researcher="researcher-alpha",
        evidence=[
            EvidenceRef(
                source_id=citation_a.source_id,
                evidence_type="paper",
                locator=citation_a.locator,
                excerpt=citation_a.excerpt,
            )
        ],
        created_at="2026-09-16T12:00:00Z",
    )

    claim_2 = ClaimRecord(
        id=claim_2_id,
        claim="Hierarchical memory indexing failed to reduce latency and increased retrieval latency by 15% due to traversal overhead.",
        type=ClaimType.fact,
        confidence=Confidence.low,
        sources=[citation_c.source_id],
        caveats=["Based on unbenchmarked internal production-like tests"],
        conflicts=[claim_1_id],
        status=ClaimStatus.contradicted,
        topic="agent-memory-systems",
        researcher="researcher-gamma",
        evidence=[
            EvidenceRef(
                source_id=citation_c.source_id,
                evidence_type="blog_post",
                locator=citation_c.locator,
                excerpt=citation_c.excerpt,
            )
        ],
        created_at="2026-09-16T12:05:00Z",
    )

    ledger.append_claim(claim_1)
    ledger.append_claim(claim_2)

    contra_id = "contra-latency-001"
    contra = ContradictionRecord(
        id=contra_id,
        claim_ids=[claim_1_id, claim_2_id],
        description="Tier A paper reports 40% latency reduction while Tier C blog reports 15% latency increase.",
        status=ClaimStatus.contradicted,
        created_at="2026-09-16T12:10:00Z",
    )
    ledger.append_contradiction(contra)

    # -------------------------------------------------------------------------
    # 6. Negative check: citing rejected source id is refused by the ledger
    # -------------------------------------------------------------------------
    invalid_claim = ClaimRecord(
        id="claim-from-rejected-source",
        claim="Rumor about memory indexing performance.",
        type=ClaimType.fact,
        confidence=Confidence.low,
        sources=[rejected_d.id],
        caveats=[],
        conflicts=[],
        status=ClaimStatus.supported,
        topic="agent-memory-systems",
        researcher="scout",
        evidence=[
            EvidenceRef(
                source_id=rejected_d.id,
                evidence_type="forum",
                locator="thread 1",
                excerpt="Unverified rumor.",
            )
        ],
        created_at="2026-09-16T12:15:00Z",
    )
    with pytest.raises(ValueError, match="rejected and cannot be cited"):
        ledger.append_claim(invalid_claim)

    # -------------------------------------------------------------------------
    # 7. Query every §14 dimension via Ledger.query
    # -------------------------------------------------------------------------
    # Dimension 1: topic
    res_topic = ledger.query(ClaimQuery(topic="agent-memory-systems"))
    assert [c.id for c in res_topic] == [claim_1_id, claim_2_id]
    assert ledger.query(ClaimQuery(topic="unrelated-topic")) == []

    # Dimension 2: confidence
    res_conf_high = ledger.query(ClaimQuery(confidence=Confidence.high))
    assert [c.id for c in res_conf_high] == [claim_1_id]
    res_conf_low = ledger.query(ClaimQuery(confidence=Confidence.low))
    assert [c.id for c in res_conf_low] == [claim_2_id]

    # Dimension 3: researcher
    res_res_a = ledger.query(ClaimQuery(researcher="researcher-alpha"))
    assert [c.id for c in res_res_a] == [claim_1_id]
    res_res_g = ledger.query(ClaimQuery(researcher="researcher-gamma"))
    assert [c.id for c in res_res_g] == [claim_2_id]

    # Dimension 4: source
    res_src_a = ledger.query(ClaimQuery(source_id=admitted_a.id))
    assert [c.id for c in res_src_a] == [claim_1_id]
    res_src_c = ledger.query(ClaimQuery(source_id=admitted_c.id))
    assert [c.id for c in res_src_c] == [claim_2_id]

    # Dimension 5: evidence type
    res_ev_paper = ledger.query(ClaimQuery(evidence_type="paper"))
    assert [c.id for c in res_ev_paper] == [claim_1_id]
    res_ev_blog = ledger.query(ClaimQuery(evidence_type="blog_post"))
    assert [c.id for c in res_ev_blog] == [claim_2_id]

    # Dimension 6: contradiction
    res_contra_any = ledger.query(ClaimQuery(contradiction="any"))
    assert [c.id for c in res_contra_any] == [claim_1_id, claim_2_id]
    res_contra_none = ledger.query(ClaimQuery(contradiction="none"))
    assert res_contra_none == []
    res_contra_target = ledger.query(ClaimQuery(contradiction=claim_2_id))
    assert [c.id for c in res_contra_target] == [claim_1_id]
    res_contra_record = ledger.query(ClaimQuery(contradiction=f"contradiction:{contra_id}"))
    assert [c.id for c in res_contra_record] == [claim_1_id, claim_2_id]

    # Dimension 7: status
    res_stat_contra = ledger.query(ClaimQuery(status=ClaimStatus.contradicted))
    assert [c.id for c in res_stat_contra] == [claim_1_id, claim_2_id]
    res_stat_supp = ledger.query(ClaimQuery(status=ClaimStatus.supported))
    assert res_stat_supp == []

    # -------------------------------------------------------------------------
    # 8. Assert index ≡ scan equivalence across queries
    # -------------------------------------------------------------------------
    test_queries = [
        ClaimQuery(),
        ClaimQuery(topic="agent-memory-systems"),
        ClaimQuery(confidence=Confidence.high),
        ClaimQuery(confidence=Confidence.low),
        ClaimQuery(researcher="researcher-alpha"),
        ClaimQuery(source_id=admitted_a.id),
        ClaimQuery(evidence_type="paper"),
        ClaimQuery(status=ClaimStatus.contradicted),
        ClaimQuery(status=ClaimStatus.supported),
        ClaimQuery(contradiction="any"),
        ClaimQuery(contradiction="none"),
        ClaimQuery(contradiction=claim_1_id),
        ClaimQuery(contradiction=f"contradiction:{contra_id}"),
        ClaimQuery(topic="agent-memory-systems", confidence=Confidence.high, status=ClaimStatus.contradicted),
        ClaimQuery(topic="agent-memory-systems", researcher="researcher-alpha", evidence_type="paper"),
        ClaimQuery(topic="nonexistent"),
    ]

    index = LedgerIndex(paths)
    for q in test_queries:
        scan_results = ledger.scan(q)
        index_results = index.query(q)
        assert index_results == scan_results, f"Discrepancy for query {q}: {index_results} != {scan_results}"
