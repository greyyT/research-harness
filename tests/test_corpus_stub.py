"""Tests for Corpus interface and LocalStubCorpus (PLAN §4.6–4.7, §5 S5)."""

from pathlib import Path
import pytest

from research_corpus.admission import (
    AdmittedSource,
    SourceCandidate,
    SourceIndex,
    admit,
)
from research_corpus.artifacts import RunPaths, init_run, load_source_index
from research_corpus.corpus import (
    Citation,
    Corpus,
    CorpusAnswer,
    CorpusSourceRef,
    CorpusStatus,
    CorpusUnavailable,
)
from research_corpus.corpus.stub import LocalStubCorpus
from research_corpus.records import SourceKind, SourceRecord, SourceTier


def _admit_url_source(paths: RunPaths, url: str, title: str) -> AdmittedSource:
    """Helper to admit a URL source via real admit()."""
    index = SourceIndex(paths)
    candidate = SourceCandidate(url=url, title=title)
    return admit(index, candidate, SourceKind.paper, declared_by="researcher", reason="test")


def _admit_file_source(paths: RunPaths, file_path: Path, title: str) -> AdmittedSource:
    """Helper to admit a local file source via real admit()."""
    index = SourceIndex(paths)
    candidate = SourceCandidate(path=str(file_path), title=title)
    return admit(index, candidate, SourceKind.engineering_report, declared_by="researcher", reason="test")


# ---------------------------------------------------------------------------
# Protocol & Type Hierarchy
# ---------------------------------------------------------------------------


def test_stub_satisfies_corpus_protocol(tmp_path: Path):
    """Verify LocalStubCorpus satisfies the Corpus runtime-checkable protocol."""
    paths = init_run(RunPaths(tmp_path, "run-proto"))
    stub = LocalStubCorpus(paths)
    assert isinstance(stub, Corpus)
    assert stub.provider == "stub"


# ---------------------------------------------------------------------------
# Ingest & Deduplication
# ---------------------------------------------------------------------------


def test_double_ingest_returns_reused_and_preserves_files(tmp_path: Path):
    """Verify double ingest of the same source returns reused=True and does not rewrite file."""
    paths = init_run(RunPaths(tmp_path, "run-dedup"))
    stub = LocalStubCorpus(paths)
    source = _admit_url_source(paths, "https://example.com/p1", "Paper One")

    # First ingest
    ref1 = stub.ingest(source, content="Line 1: initial content\nLine 2: important fact")
    assert isinstance(ref1, CorpusSourceRef)
    assert ref1.source_id == source.id
    assert ref1.provider == "stub"
    assert ref1.provider_ref == source.id
    assert ref1.reused is False

    source_file = paths.stub_corpus_dir / f"{source.id}.txt"
    assert source_file.is_file()
    initial_text = source_file.read_text(encoding="utf-8")
    initial_mtime = source_file.stat().st_mtime_ns
    file_count_after_first = len(list(paths.stub_corpus_dir.glob("*.txt")))
    assert file_count_after_first == 1

    # Second ingest of same source (even with different content passed)
    ref2 = stub.ingest(source, content="Line 1: modified content that should not be written")
    assert isinstance(ref2, CorpusSourceRef)
    assert ref2.source_id == source.id
    assert ref2.provider == "stub"
    assert ref2.provider_ref == source.id
    assert ref2.reused is True

    # File count unchanged and content unrewritten
    file_count_after_second = len(list(paths.stub_corpus_dir.glob("*.txt")))
    assert file_count_after_second == 1
    assert source_file.read_text(encoding="utf-8") == initial_text
    assert source_file.stat().st_mtime_ns == initial_mtime


def test_ingest_updates_source_index_corpus_ref(tmp_path: Path):
    """Verify stub ingest updates SourceRecord.corpus_ref['stub'] in source-index.json."""
    paths = init_run(RunPaths(tmp_path, "run-index-ref"))
    stub = LocalStubCorpus(paths)
    source = _admit_url_source(paths, "https://example.com/p2", "Paper Two")

    records_before = load_source_index(paths)
    assert records_before[0].corpus_ref.get("stub") is None

    stub.ingest(source, content="Some knowledge text")

    records_after = load_source_index(paths)
    assert records_after[0].corpus_ref.get("stub") == source.id


def test_ingest_fails_loudly_on_corrupt_source_index_without_saving_file(tmp_path: Path):
    """Verify a corrupt source-index.json causes ingest to raise and leaves stub file unwritten."""
    paths = init_run(RunPaths(tmp_path, "run-corrupt-index"))
    stub = LocalStubCorpus(paths)
    source = _admit_url_source(paths, "https://example.com/p-corrupt", "Paper Corrupt")

    # Corrupt source-index.json
    paths.source_index.write_text("{invalid json", encoding="utf-8")

    with pytest.raises(ValueError, match="Corrupt JSON"):
        stub.ingest(source, content="Some knowledge text")

    # Verify <source_id>.txt was NOT written (dedup won't block retry)
    source_file = paths.stub_corpus_dir / f"{source.id}.txt"
    assert not source_file.exists()
    assert not stub.has_source(source.id)


def test_ingest_non_admitted_source_raises_type_error(tmp_path: Path):
    """Verify passing a non-AdmittedSource to ingest raises TypeError (enforced by type)."""
    paths = init_run(RunPaths(tmp_path, "run-typecheck"))
    stub = LocalStubCorpus(paths)

    # Raw SourceCandidate
    candidate = SourceCandidate(url="https://example.com/test", title="Test")
    with pytest.raises(TypeError, match="AdmittedSource"):
        stub.ingest(candidate, content="Text")  # type: ignore[arg-type]

    # Raw SourceRecord
    record = SourceRecord(
        id="raw-rec",
        kind=SourceKind.paper,
        tier=SourceTier.A,
        url="https://example.com/raw",
        path=None,
        title="Raw",
        admitted=True,
        reason="",
        declared_by="scout",
        decided_at="2026-09-16T00:00:00Z",
        corpus_ref={},
    )
    with pytest.raises(TypeError, match="AdmittedSource"):
        stub.ingest(record, content="Text")  # type: ignore[arg-type]

    # Other non-AdmittedSource types
    with pytest.raises(TypeError, match="AdmittedSource"):
        stub.ingest("not-a-source", content="Text")  # type: ignore[arg-type]


def test_ingest_url_only_without_content_raises_value_error(tmp_path: Path):
    """Verify URL-only admitted source ingested without content raises ValueError."""
    paths = init_run(RunPaths(tmp_path, "run-nocontent"))
    stub = LocalStubCorpus(paths)
    source = _admit_url_source(paths, "https://example.com/url-only", "URL Only")

    with pytest.raises(ValueError, match="Stub corpus requires content"):
        stub.ingest(source, content=None)


def test_ingest_with_path_object_content(tmp_path: Path):
    """Verify ingest accepts content as a Path instance."""
    paths = init_run(RunPaths(tmp_path, "run-path-content"))
    stub = LocalStubCorpus(paths)
    source = _admit_url_source(paths, "https://example.com/doc", "Doc")

    content_file = tmp_path / "external_content.txt"
    content_file.write_text("Line 1 from external file\nLine 2 info", encoding="utf-8")

    ref = stub.ingest(source, content=content_file)
    assert ref.reused is False
    saved = (paths.stub_corpus_dir / f"{source.id}.txt").read_text(encoding="utf-8")
    assert saved == "Line 1 from external file\nLine 2 info"


def test_ingest_with_local_file_candidate_without_explicit_content(tmp_path: Path):
    """Verify ingest of local file candidate reads file when content is None."""
    paths = init_run(RunPaths(tmp_path, "run-file-candidate"))
    stub = LocalStubCorpus(paths)

    src_file = tmp_path / "local_paper.txt"
    src_file.write_text("Line 1 local text\nLine 2 benchmark numbers", encoding="utf-8")

    source = _admit_file_source(paths, src_file, "Local Paper")
    ref = stub.ingest(source, content=None)
    assert ref.reused is False

    saved = (paths.stub_corpus_dir / f"{source.id}.txt").read_text(encoding="utf-8")
    assert saved == "Line 1 local text\nLine 2 benchmark numbers"


# ---------------------------------------------------------------------------
# Query & Attribution
# ---------------------------------------------------------------------------


def test_two_sources_queryable_together_with_correct_attribution(tmp_path: Path):
    """Verify two sources ingested and queryable together with correct per-source attribution."""
    paths = init_run(RunPaths(tmp_path, "run-multi-query"))
    stub = LocalStubCorpus(paths)

    s1 = _admit_url_source(paths, "https://example.com/quantum", "Quantum Paper")
    s2 = _admit_url_source(paths, "https://example.com/classical", "Classical Report")

    s1_text = (
        "Introduction to modern computing architectures\n"
        "Quantum computing uses superposition qubits for exponential speedup\n"
        "Conclusions and future directions\n"
    )
    s2_text = (
        "Overview of semiconductor devices\n"
        "Classical computing relies on silicon transistors and binary bits\n"
        "Performance limits of standard hardware\n"
    )

    stub.ingest(s1, content=s1_text)
    stub.ingest(s2, content=s2_text)

    # Query matching both sources
    answer = stub.query("computing architectures and binary bits")
    assert isinstance(answer, CorpusAnswer)
    assert answer.provider == "stub"
    assert len(answer.citations) == 2

    # Check attribution per source
    cit_by_source = {c.source_id: c for c in answer.citations}
    assert s1.id in cit_by_source
    assert s2.id in cit_by_source

    c1 = cit_by_source[s1.id]
    assert c1.source_id == s1.id
    assert c1.provider_ref == s1.id
    assert "computing" in c1.excerpt.lower()
    assert c1.locator.startswith("line ")

    c2 = cit_by_source[s2.id]
    assert c2.source_id == s2.id
    assert c2.provider_ref == s2.id
    assert "bits" in c2.excerpt.lower() or "binary" in c2.excerpt.lower()
    assert c2.locator.startswith("line ")

    # Check answer concatenation with [1], [2] markers
    assert "[1]" in answer.answer
    assert "[2]" in answer.answer
    assert c1.excerpt in answer.answer
    assert c2.excerpt in answer.answer


def test_source_ids_restricts_query_scope(tmp_path: Path):
    """Verify source_ids restricts query scope to only the specified sources."""
    paths = init_run(RunPaths(tmp_path, "run-scope"))
    stub = LocalStubCorpus(paths)

    s1 = _admit_url_source(paths, "https://example.com/s1", "Source One")
    s2 = _admit_url_source(paths, "https://example.com/s2", "Source Two")

    stub.ingest(s1, content="Line 1: database replication mechanism\nLine 2: network topology")
    stub.ingest(s2, content="Line 1: database indexing strategies\nLine 2: memory buffers")

    # Scope to s1 only
    ans_s1 = stub.query("database", source_ids=[s1.id])
    assert len(ans_s1.citations) == 1
    assert ans_s1.citations[0].source_id == s1.id
    assert ans_s1.citations[0].locator == "line 1"

    # Scope to s2 only
    ans_s2 = stub.query("database", source_ids=[s2.id])
    assert len(ans_s2.citations) == 1
    assert ans_s2.citations[0].source_id == s2.id
    assert ans_s2.citations[0].locator == "line 1"


def test_query_raises_value_error_for_uningested_source_ids(tmp_path: Path):
    """Verify source_ids with uningested or missing ids raises ValueError naming the missing ids."""
    paths = init_run(RunPaths(tmp_path, "run-missing-sid"))
    stub = LocalStubCorpus(paths)

    s1 = _admit_url_source(paths, "https://example.com/s1", "Source One")
    stub.ingest(s1, content="Distributed consensus algorithm Paxos and Raft")

    # Mixed valid and invalid source_id
    with pytest.raises(ValueError, match="nonexistent-id-999"):
        stub.query("consensus algorithm", source_ids=[s1.id, "nonexistent-id-999"])

    # Exclusively invalid source_id
    with pytest.raises(ValueError, match="nonexistent-id-999"):
        stub.query("consensus algorithm", source_ids=["nonexistent-id-999"])


def test_query_with_only_valid_ingested_source_ids(tmp_path: Path):
    """Verify query with multiple valid ingested source_ids succeeds and scopes to them."""
    paths = init_run(RunPaths(tmp_path, "run-valid-scope"))
    stub = LocalStubCorpus(paths)

    s1 = _admit_url_source(paths, "https://example.com/s1", "Source One")
    s2 = _admit_url_source(paths, "https://example.com/s2", "Source Two")
    s3 = _admit_url_source(paths, "https://example.com/s3", "Source Three")

    stub.ingest(s1, content="Distributed consensus Paxos")
    stub.ingest(s2, content="Distributed consensus Raft")
    stub.ingest(s3, content="Distributed consensus Zab")

    ans = stub.query("consensus", source_ids=[s1.id, s3.id])
    assert len(ans.citations) == 2
    c_ids = {c.source_id for c in ans.citations}
    assert c_ids == {s1.id, s3.id}


def test_query_no_matches_returns_empty_answer(tmp_path: Path):
    """Verify query with no matching terms returns empty citations and answer."""
    paths = init_run(RunPaths(tmp_path, "run-nomatch"))
    stub = LocalStubCorpus(paths)

    s1 = _admit_url_source(paths, "https://example.com/s1", "Source One")
    stub.ingest(s1, content="Apples oranges bananas")

    ans = stub.query("quantum mechanics astrophysics")
    assert ans.citations == []
    assert ans.answer == ""
    assert ans.provider == "stub"


def test_query_ordering_by_score_desc_and_id_asc(tmp_path: Path):
    """Verify query orders results by score descending, then source_id ascending."""
    paths = init_run(RunPaths(tmp_path, "run-order"))
    stub = LocalStubCorpus(paths)

    s1 = _admit_url_source(paths, "https://example.com/a-source", "A Source")
    s2 = _admit_url_source(paths, "https://example.com/b-source", "B Source")
    s3 = _admit_url_source(paths, "https://example.com/c-source", "C Source")

    # s1 matches 1 term: "alpha"
    stub.ingest(s1, content="Only alpha mentioned here")
    # s2 matches 2 terms: "alpha beta"
    stub.ingest(s2, content="Both alpha and beta are discussed")
    # s3 matches 1 term: "alpha"
    stub.ingest(s3, content="Another alpha mention")

    ans = stub.query("alpha beta gamma")
    assert len(ans.citations) == 3
    # s2 has highest score (2 terms), must come first
    assert ans.citations[0].source_id == s2.id
    # s1 and s3 tie on score (1 term each), broken by source_id ascending
    remaining_ids = [ans.citations[1].source_id, ans.citations[2].source_id]
    assert remaining_ids == sorted([s1.id, s3.id])


def test_query_determinism(tmp_path: Path):
    """Verify identical queries return byte-identical CorpusAnswer structures."""
    paths = init_run(RunPaths(tmp_path, "run-det"))
    stub = LocalStubCorpus(paths)

    s1 = _admit_url_source(paths, "https://example.com/det1", "Deterministic 1")
    s2 = _admit_url_source(paths, "https://example.com/det2", "Deterministic 2")

    stub.ingest(s1, content="Line 1: neural network training loss\nLine 2: validation error")
    stub.ingest(s2, content="Line 1: gradient descent optimization\nLine 2: learning rate schedule")

    first_answer = stub.query("neural gradient optimization")
    for _ in range(10):
        subsequent_answer = stub.query("neural gradient optimization")
        assert subsequent_answer == first_answer


# ---------------------------------------------------------------------------
# Availability & Error Taxonomy (I11)
# ---------------------------------------------------------------------------


def test_available_true_status(tmp_path: Path):
    """Verify status() returns available=True when stub is available."""
    paths = init_run(RunPaths(tmp_path, "run-avail-true"))
    stub = LocalStubCorpus(paths, available=True)

    status = stub.status()
    assert isinstance(status, CorpusStatus)
    assert status.available is True
    assert status.provider == "stub"
    assert status.detail != ""


def test_available_false_toggle_behavior(tmp_path: Path):
    """Verify available=False: status() probe does not raise; other methods raise CorpusUnavailable."""
    paths = init_run(RunPaths(tmp_path, "run-unavail"))
    source = _admit_url_source(paths, "https://example.com/avail-test", "Avail Test")

    stub = LocalStubCorpus(paths, available=False)

    # status() is the single non-raising probe
    status = stub.status()
    assert isinstance(status, CorpusStatus)
    assert status.available is False
    assert status.provider == "stub"
    assert "stub marked unavailable" in status.detail

    # has_source raises CorpusUnavailable
    with pytest.raises(CorpusUnavailable, match="stub marked unavailable"):
        stub.has_source(source.id)

    # ingest raises CorpusUnavailable
    with pytest.raises(CorpusUnavailable, match="stub marked unavailable"):
        stub.ingest(source, content="content")

    # query raises CorpusUnavailable
    with pytest.raises(CorpusUnavailable, match="stub marked unavailable"):
        stub.query("question")


def test_has_source_reporting(tmp_path: Path):
    """Verify has_source accurately reports presence/absence of sources."""
    paths = init_run(RunPaths(tmp_path, "run-has-source"))
    stub = LocalStubCorpus(paths)
    source = _admit_url_source(paths, "https://example.com/probe", "Probe")

    assert stub.has_source(source.id) is False
    stub.ingest(source, content="Valid text")
    assert stub.has_source(source.id) is True
