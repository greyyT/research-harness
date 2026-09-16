"""Tests for source admission, A/B/C tiering, and SourceIndex (PLAN.md §4.5, §5 S4)."""

import json
from pathlib import Path
import time
import urllib.parse

import pytest

from research_corpus.admission import (
    TIER_MAP,
    AdmittedSource,
    SourceCandidate,
    SourceIndex,
    admit,
    derive_source_id,
    normalize_url,
    reject,
    slugify,
)
from research_corpus.artifacts import RunPaths, init_run, load_source_index
from research_corpus.records import SourceKind, SourceRecord, SourceTier


# ---------------------------------------------------------------------------
# Tier Mapping (§9)
# ---------------------------------------------------------------------------


def test_tier_map_covers_all_source_kinds():
    """Verify TIER_MAP contains an entry for every SourceKind enum member."""
    assert set(TIER_MAP.keys()) == set(SourceKind)
    assert len(TIER_MAP) == len(SourceKind)


@pytest.mark.parametrize(
    ("kind", "expected_tier"),
    [
        # Tier A
        (SourceKind.paper, SourceTier.A),
        (SourceKind.official_benchmark, SourceTier.A),
        (SourceKind.official_repository, SourceTier.A),
        (SourceKind.official_specification, SourceTier.A),
        (SourceKind.official_documentation, SourceTier.A),
        (SourceKind.original_dataset, SourceTier.A),
        # Tier B
        (SourceKind.engineering_report, SourceTier.B),
        (SourceKind.independent_reproduction, SourceTier.B),
        (SourceKind.technical_analysis, SourceTier.B),
        (SourceKind.conference_presentation, SourceTier.B),
        # Tier C
        (SourceKind.blog_post, SourceTier.C),
        (SourceKind.discussion, SourceTier.C),
        (SourceKind.forum_thread, SourceTier.C),
        (SourceKind.reddit, SourceTier.C),
        (SourceKind.informal_explanation, SourceTier.C),
    ],
)
def test_source_kind_tier_resolution(kind: SourceKind, expected_tier: SourceTier, tmp_path: Path):
    """Verify each SourceKind resolves to its PRD §9 tier via TIER_MAP and admit()."""
    assert TIER_MAP[kind] == expected_tier

    paths = init_run(RunPaths(tmp_path, f"run-{kind.value}"))
    index = SourceIndex(paths)
    candidate = SourceCandidate(url=f"https://example.com/{kind.value}", title=f"Source {kind.value}")

    admitted = admit(index, candidate, kind, declared_by="scout", reason="Testing tier")
    assert admitted.tier == expected_tier
    assert admitted.kind == kind
    assert admitted.record.tier == expected_tier


# ---------------------------------------------------------------------------
# URL Normalization & Deterministic IDs (I9)
# ---------------------------------------------------------------------------


def test_slugify():
    """Verify slug generation: lowercased, non-alnum-collapsed-to-hyphen, trimmed."""
    assert slugify("Attention Is All You Need!") == "attention-is-all-you-need"
    assert slugify("arXiv.org") == "arxiv-org"
    assert slugify("  Multiple   Spaces  ") == "multiple-spaces"
    assert slugify("Symbols---and___underscores") == "symbols-and-underscores"
    assert slugify("123 Numbers 456") == "123-numbers-456"
    assert slugify("---") == ""


def test_url_normalization_variants_yield_one_id():
    """Verify normalization variants all collapse to identical URL and source id.

    Variants differ by:
    - scheme case (HTTP vs http)
    - host case (EXAMPLE.COM vs example.com)
    - default port (:80 for http, :443 for https)
    - fragment (#frag)
    - trailing slash (/path/test/ vs /path/test)
    - tracking query params (utm_*, ref, fbclid)
    - query parameter ordering (b=2&a=1 vs a=1&b=2)
    """
    variants = [
        "HTTP://Example.COM:80/path/test/?utm_source=twitter&b=2&a=1&ref=123#frag",
        "http://example.com/path/test?a=1&b=2",
        "http://example.com:80/path/test?b=2&a=1&fbclid=abc",
        "http://EXAMPLE.COM/path/test/?b=2&a=1&utm_medium=cpc&utm_campaign=launch",
        "HTTP://example.com/path/test?utm_term=ai&b=2&ref=newsletter&a=1#section2",
    ]

    normalized_urls = [normalize_url(u) for u in variants]
    assert len(set(normalized_urls)) == 1
    expected_url = "http://example.com/path/test?a=1&b=2"
    assert normalized_urls[0] == expected_url

    ids = [derive_source_id(SourceCandidate(url=u, title="Shared Title")) for u in variants]
    assert len(set(ids)) == 1

    # Without title, slug comes from host
    ids_no_title = [derive_source_id(SourceCandidate(url=u)) for u in variants]
    assert len(set(ids_no_title)) == 1
    assert ids_no_title[0].startswith("example-com-")


def test_url_normalization_root_path_and_ports():
    """Verify trailing slash preserved for root path, dropped for non-root paths."""
    # Root path preserves single slash
    assert normalize_url("https://example.com:443/") == "https://example.com/"
    assert normalize_url("https://example.com:443") == "https://example.com/"
    assert normalize_url("https://EXAMPLE.COM") == "https://example.com/"
    assert normalize_url("https://example.com/#overview") == "https://example.com/"
    assert normalize_url("https://example.com/?utm_source=x") == "https://example.com/"

    # Non-default ports are preserved
    assert normalize_url("http://example.com:8080/path/") == "http://example.com:8080/path"
    assert normalize_url("https://example.com:8443/path/") == "https://example.com:8443/path"
    assert normalize_url("http://example.com:443/path/") == "http://example.com:443/path"
    assert normalize_url("https://example.com:80/path/") == "https://example.com:80/path"

    # Retain non-tracking query parameters
    norm = normalize_url("http://example.com/search?q=agents&reference=true&utm_source=feed")
    assert norm == "http://example.com/search?q=agents&reference=true"


def test_id_derivation_for_url_file_and_text(tmp_path: Path):
    """Verify deterministic id derivation across all three candidate modalities (I9)."""
    # 1. URL candidate
    url_cand_with_title = SourceCandidate(url="https://arxiv.org/abs/2401.00001", title="Hierarchical Memory")
    url_id_1 = derive_source_id(url_cand_with_title)
    assert url_id_1.startswith("hierarchical-memory-")

    url_cand_no_title = SourceCandidate(url="https://arxiv.org/abs/2401.00001")
    url_id_2 = derive_source_id(url_cand_no_title)
    assert url_id_2.startswith("arxiv-org-")
    # Same URL content produces same 8-hex digest suffix
    assert url_id_1.split("-")[-1] == url_id_2.split("-")[-1]

    # 2. Local file candidate
    test_file = tmp_path / "sample_report.txt"
    test_file.write_bytes(b"Benchmark results on agent memory systems 2026.")

    file_cand_with_title = SourceCandidate(path=str(test_file), title="Memory Benchmark Report")
    file_id_1 = derive_source_id(file_cand_with_title)
    assert file_id_1.startswith("memory-benchmark-report-")

    file_cand_no_title = SourceCandidate(path=str(test_file))
    file_id_2 = derive_source_id(file_cand_no_title)
    assert file_id_2.startswith("sample-report-")
    assert file_id_1.split("-")[-1] == file_id_2.split("-")[-1]

    # Modifying file content changes the hash digest
    test_file.write_bytes(b"Modified content.")
    file_id_3 = derive_source_id(file_cand_no_title)
    assert file_id_3.split("-")[-1] != file_id_2.split("-")[-1]

    # Non-existent file raises FileNotFoundError
    missing_cand = SourceCandidate(path=str(tmp_path / "non_existent.pdf"))
    with pytest.raises(FileNotFoundError, match="Local source file not found"):
        derive_source_id(missing_cand)

    # 3. Pasted text candidate
    text_cand_with_title = SourceCandidate(text="Pasted transcript excerpt...", title="Interview Notes")
    text_id_1 = derive_source_id(text_cand_with_title)
    assert text_id_1.startswith("interview-notes-")

    text_cand_no_title = SourceCandidate(text="Pasted transcript excerpt...")
    text_id_2 = derive_source_id(text_cand_no_title)
    assert text_id_2.startswith("text-")
    assert text_id_1.split("-")[-1] == text_id_2.split("-")[-1]


# ---------------------------------------------------------------------------
# SourceCandidate Validation
# ---------------------------------------------------------------------------


def test_source_candidate_requires_exactly_one_identifier():
    """Verify SourceCandidate enforces exactly one of url, path, or text."""
    # 0 provided
    with pytest.raises(ValueError, match="requires exactly one of url, path, or text; got \\[\\]"):
        SourceCandidate()

    # 2 provided
    with pytest.raises(ValueError, match="requires exactly one of url, path, or text; got \\['url', 'path'\\]"):
        SourceCandidate(url="https://example.com", path="/path/to/file")

    # 3 provided
    with pytest.raises(ValueError, match="requires exactly one of url, path, or text"):
        SourceCandidate(url="https://example.com", path="/path/to/file", text="hello")

    # Empty string identifier
    with pytest.raises(ValueError, match="field 'url' must be a non-empty string"):
        SourceCandidate(url="   ")

    # Non-string title
    with pytest.raises(TypeError, match="title must be a string"):
        SourceCandidate(url="https://example.com", title=123)  # type: ignore[arg-type]


def test_source_candidate_converts_path_instance(tmp_path: Path):
    """Verify Path objects passed to candidate.path are converted to str."""
    p = tmp_path / "test.txt"
    cand = SourceCandidate(path=p)
    assert isinstance(cand.path, str)
    assert cand.path == str(p)


# ---------------------------------------------------------------------------
# AdmittedSource Contract
# ---------------------------------------------------------------------------


def test_admitted_source_construction_guard():
    """Verify AdmittedSource cannot be constructed directly without admit()."""
    rec = SourceRecord(
        id="src-1",
        kind=SourceKind.paper,
        tier=SourceTier.A,
        url="https://example.com",
        path=None,
        title="Title",
        admitted=True,
        reason="Good",
        declared_by="scout",
        decided_at="2026-09-16T10:00:00Z",
        corpus_ref={},
    )

    with pytest.raises(TypeError, match="AdmittedSource cannot be constructed directly; use admit\\(\\)"):
        AdmittedSource(rec)

    with pytest.raises(TypeError, match="AdmittedSource cannot be constructed directly; use admit\\(\\)"):
        AdmittedSource(rec, _guard="fake-guard")


def test_admitted_source_properties(tmp_path: Path):
    """Verify AdmittedSource exposes id, url, path, title, tier, kind, and record."""
    paths = init_run(RunPaths(tmp_path, "run-properties"))
    index = SourceIndex(paths)
    cand = SourceCandidate(url="https://example.com/spec", title="Official Spec")

    admitted = admit(index, cand, SourceKind.official_specification, declared_by="scout", reason="Valid spec")
    assert admitted.id == derive_source_id(cand)
    assert admitted.url == "https://example.com/spec"
    assert admitted.path is None
    assert admitted.title == "Official Spec"
    assert admitted.tier == SourceTier.A
    assert admitted.kind == SourceKind.official_specification
    assert isinstance(admitted.record, SourceRecord)
    assert admitted.record.admitted is True


# ---------------------------------------------------------------------------
# Rejection Recording
# ---------------------------------------------------------------------------


def test_rejection_recorded_with_reason(tmp_path: Path):
    """Verify reject() writes a SourceRecord(admitted=False, tier=None) with reason."""
    paths = init_run(RunPaths(tmp_path, "run-reject"))
    index = SourceIndex(paths)
    candidate = SourceCandidate(url="https://reddit.com/r/localllama/test", title="Reddit Speculation")

    record = reject(
        index,
        candidate,
        declared_by="scout",
        reason="Unsubstantiated forum post lacking evaluation",
        kind=SourceKind.reddit,
    )

    assert record.admitted is False
    assert record.tier is None
    assert record.kind == SourceKind.reddit
    assert record.reason == "Unsubstantiated forum post lacking evaluation"
    assert record.declared_by == "scout"
    assert record.decided_at

    # Verify persisted in index
    loaded = load_source_index(paths)
    assert len(loaded) == 1
    assert loaded[0] == record


def test_rejection_refuses_empty_reason(tmp_path: Path):
    """Verify reject() refuses empty or whitespace-only reason."""
    paths = init_run(RunPaths(tmp_path, "run-reject-empty"))
    index = SourceIndex(paths)
    candidate = SourceCandidate(url="https://example.com/junk")

    with pytest.raises(ValueError, match="Rejection reason is required and must be non-empty"):
        reject(index, candidate, declared_by="scout", reason="")

    with pytest.raises(ValueError, match="Rejection reason is required and must be non-empty"):
        reject(index, candidate, declared_by="scout", reason="   ")


# ---------------------------------------------------------------------------
# Idempotent Re-admission
# ---------------------------------------------------------------------------


def test_readmission_is_idempotent(tmp_path: Path):
    """Verify admitting an already admitted source returns identical handle without rewriting."""
    paths = init_run(RunPaths(tmp_path, "run-idempotent"))
    index = SourceIndex(paths)
    candidate = SourceCandidate(url="https://arxiv.org/abs/2401.00001", title="Memory Paper")

    handle1 = admit(index, candidate, SourceKind.paper, declared_by="scout", reason="Primary paper")
    assert handle1.record.admitted is True

    # Record timestamp and content of source-index.json
    content_before = paths.source_index.read_text(encoding="utf-8")
    mtime_before = paths.source_index.stat().st_mtime_ns

    # Ensure system clock would tick if a write happened
    time.sleep(0.01)

    # Re-admit same candidate
    handle2 = admit(index, candidate, SourceKind.paper, declared_by="researcher", reason="Second pass")

    # Must return unchanged handle and unchanged file
    assert handle1 == handle2
    assert handle1.record == handle2.record
    assert handle1.id == handle2.id
    assert paths.source_index.read_text(encoding="utf-8") == content_before
    assert paths.source_index.stat().st_mtime_ns == mtime_before


def test_readmission_ignores_differing_tier_or_kind(tmp_path: Path):
    """Verify re-admission of an admitted id does not change tier or kind (PLAN §4.5)."""
    paths = init_run(RunPaths(tmp_path, "run-immutable-tier"))
    index = SourceIndex(paths)
    candidate = SourceCandidate(url="https://example.com/report", title="Report")

    handle1 = admit(index, candidate, SourceKind.engineering_report, declared_by="scout")
    assert handle1.tier == SourceTier.B

    # Re-admit claiming Tier A
    handle2 = admit(index, candidate, SourceKind.paper, declared_by="scout")
    assert handle2.tier == SourceTier.B
    assert handle2.kind == SourceKind.engineering_report
    assert handle1 == handle2


def test_admitting_previously_rejected_source_updates_record(tmp_path: Path):
    """Verify admitting a source that was previously rejected transitions it to admitted=True."""
    paths = init_run(RunPaths(tmp_path, "run-reject-then-admit"))
    index = SourceIndex(paths)
    candidate = SourceCandidate(url="https://example.com/draft", title="Draft")

    rej = reject(index, candidate, declared_by="scout", reason="Not yet verified")
    assert rej.admitted is False

    admitted = admit(index, candidate, SourceKind.engineering_report, declared_by="senior-scout", reason="Now verified")
    assert admitted.record.admitted is True
    assert admitted.tier == SourceTier.B

    loaded = load_source_index(paths)
    assert len(loaded) == 1
    assert loaded[0].admitted is True
    assert loaded[0].id == rej.id


# ---------------------------------------------------------------------------
# Round-Trip Persistence
# ---------------------------------------------------------------------------


def test_source_index_round_trip(tmp_path: Path):
    """Verify source-index.json round-trips cleanly through load_source_index."""
    paths = init_run(RunPaths(tmp_path, "run-round-trip"))
    index = SourceIndex(paths)

    cand_a = SourceCandidate(url="https://arxiv.org/abs/2401.00001", title="Paper A")
    cand_b = SourceCandidate(url="https://example.com/benchmark", title="Benchmark B")
    cand_c = SourceCandidate(url="https://reddit.com/r/ml/spec", title="Post C")

    adm_a = admit(index, cand_a, SourceKind.paper, declared_by="scout", reason="Solid paper")
    adm_b = admit(index, cand_b, SourceKind.official_benchmark, declared_by="benchmark-scout", reason="Standard harness")
    rej_c = reject(index, cand_c, declared_by="scout", reason="Unverified rumor", kind=SourceKind.reddit)

    # Load via read-only artifacts loader
    loaded = load_source_index(paths)
    assert len(loaded) == 3

    assert loaded[0] == adm_a.record
    assert loaded[1] == adm_b.record
    assert loaded[2] == rej_c

    # Re-instantiate SourceIndex and verify get()
    index2 = SourceIndex(paths)
    assert index2.get(adm_a.id) == adm_a.record
    assert index2.get(adm_b.id) == adm_b.record
    assert index2.get(rej_c.id) == rej_c
    assert index2.get("non-existent-id") is None


# ---------------------------------------------------------------------------
# Research State Rejection (I6)
# ---------------------------------------------------------------------------


def test_research_state_rejected_as_candidate(tmp_path: Path):
    """Verify research-state.md candidates are refused by admit() with ValueError (I6)."""
    paths = init_run(RunPaths(tmp_path, "run-state-rejection"))
    index = SourceIndex(paths)

    # Path candidate matching research-state.md
    cand_path = SourceCandidate(path="research-state.md")
    with pytest.raises(ValueError, match="research-state\\.md is a synthetic summary and can never be admitted"):
        admit(index, cand_path, SourceKind.engineering_report, declared_by="scout")

    # Path candidate with directories
    cand_nested_path = SourceCandidate(path="/Users/user/research/run-1/research-state.md")
    with pytest.raises(ValueError, match="research-state\\.md is a synthetic summary"):
        admit(index, cand_nested_path, SourceKind.engineering_report, declared_by="scout")

    # URL candidate pointing to research-state.md
    cand_url = SourceCandidate(url="https://github.com/org/repo/blob/main/research-state.md")
    with pytest.raises(ValueError, match="research-state\\.md is a synthetic summary"):
        admit(index, cand_url, SourceKind.official_documentation, declared_by="scout")

    # URL candidate with query/fragments
    cand_url_query = SourceCandidate(url="https://example.com/docs/research-state.md?raw=true#section")
    with pytest.raises(ValueError, match="research-state\\.md is a synthetic summary"):
        admit(index, cand_url_query, SourceKind.official_documentation, declared_by="scout")

    # Case-insensitive check
    cand_case = SourceCandidate(path="RESEARCH-STATE.MD")
    with pytest.raises(ValueError, match="research-state\\.md is a synthetic summary"):
        admit(index, cand_case, SourceKind.engineering_report, declared_by="scout")

    # Source index remains empty
    assert len(load_source_index(paths)) == 0


# ---------------------------------------------------------------------------
# Corpus Object Preservation
# ---------------------------------------------------------------------------


def test_source_index_preserves_corpus_object(tmp_path: Path):
    """Verify pre-existing corpus configuration is preserved across admit and reject writes."""
    paths = init_run(RunPaths(tmp_path, "run-preserve-corpus"))

    # Seed source-index.json with custom corpus metadata
    custom_corpus = {
        "notebooklm": {
            "notebook_id": "nb-seeded-preserve-1234",
            "profile": "research-team",
        }
    }
    index_payload = {
        "sources": [],
        "corpus": custom_corpus,
    }
    paths.source_index.write_text(json.dumps(index_payload, indent=2) + "\n", encoding="utf-8")

    index = SourceIndex(paths)

    # 1. Admit a source
    cand_a = SourceCandidate(url="https://example.com/paper", title="Paper A")
    admit(index, cand_a, SourceKind.paper, declared_by="scout", reason="Good paper")

    raw_after_admit = json.loads(paths.source_index.read_text(encoding="utf-8"))
    assert raw_after_admit["corpus"] == custom_corpus
    assert len(raw_after_admit["sources"]) == 1

    # 2. Reject a source
    cand_b = SourceCandidate(url="https://example.com/bad", title="Bad source")
    reject(index, cand_b, declared_by="scout", reason="Low quality")

    raw_after_reject = json.loads(paths.source_index.read_text(encoding="utf-8"))
    assert raw_after_reject["corpus"] == custom_corpus
    assert len(raw_after_reject["sources"]) == 2


def test_source_index_update_corpus(tmp_path: Path):
    """Verify update_corpus updates corpus configuration while preserving sources."""
    paths = init_run(RunPaths(tmp_path, "run-update-corpus"))
    index = SourceIndex(paths)

    cand = SourceCandidate(url="https://example.com/doc", title="Doc")
    admit(index, cand, SourceKind.official_documentation, declared_by="scout")

    index.update_corpus({"notebooklm": {"notebook_id": "nb-new-456"}})

    raw = json.loads(paths.source_index.read_text(encoding="utf-8"))
    assert raw["corpus"] == {"notebooklm": {"notebook_id": "nb-new-456"}}
    assert len(raw["sources"]) == 1
    assert raw["sources"][0]["id"] == derive_source_id(cand)


# ---------------------------------------------------------------------------
# SourceIndex Edge Cases
# ---------------------------------------------------------------------------


def test_source_index_type_checking():
    """Verify SourceIndex rejects non-RunPaths input."""
    with pytest.raises(TypeError, match="Expected RunPaths"):
        SourceIndex("invalid-path")  # type: ignore[arg-type]


def test_source_index_uninitialized_directory(tmp_path: Path):
    """Verify SourceIndex handles a missing source-index.json gracefully."""
    paths = RunPaths(tmp_path, "uninit-run")
    index = SourceIndex(paths)
    assert index.load() == []
    assert index.get("anything") is None
    assert index.read_corpus() == {}
