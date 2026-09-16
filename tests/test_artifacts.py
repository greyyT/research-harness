"""Tests for artifact tree, RunPaths, and run initialization (PLAN.md §4.2, S1)."""

import json
from pathlib import Path
import pytest

from research_corpus.artifacts import (
    RESEARCH_STATE_SKELETON,
    SYNTHETIC_SUMMARY_BANNER,
    RunPaths,
    init_run,
    load_source_index,
)
from research_corpus.records import SourceKind, SourceTier


def test_run_paths_structure(tmp_path: Path):
    """Verify RunPaths correctly exposes every PRD §26 path without duplicate aliases."""
    paths = RunPaths(tmp_path, "sample-slug")

    assert paths.root == tmp_path
    assert paths.slug == "sample-slug"
    assert paths.run_dir == tmp_path / "sample-slug"
    assert paths.brief == tmp_path / "sample-slug" / "brief.md"
    assert paths.research_state == tmp_path / "sample-slug" / "research-state.md"

    # sources
    assert paths.sources == tmp_path / "sample-slug" / "sources"
    assert paths.source_index == tmp_path / "sample-slug" / "sources" / "source-index.json"
    assert paths.stub_corpus_dir == tmp_path / "sample-slug" / "sources" / "stub-corpus"

    # evidence
    assert paths.evidence == tmp_path / "sample-slug" / "evidence"
    assert paths.claims == tmp_path / "sample-slug" / "evidence" / "claims.jsonl"
    assert paths.contradictions == tmp_path / "sample-slug" / "evidence" / "contradictions.jsonl"
    assert paths.index_sqlite == tmp_path / "sample-slug" / "evidence" / "index.sqlite"

    # tasks
    assert paths.tasks == tmp_path / "sample-slug" / "tasks"
    assert paths.tasks_scout == tmp_path / "sample-slug" / "tasks" / "scout"
    assert paths.tasks_researchers == tmp_path / "sample-slug" / "tasks" / "researchers"
    assert paths.tasks_targeted == tmp_path / "sample-slug" / "tasks" / "targeted"

    # reviews
    assert paths.reviews == tmp_path / "sample-slug" / "reviews"
    assert paths.reviews_critic == tmp_path / "sample-slug" / "reviews" / "critic"
    assert paths.reviews_selective == tmp_path / "sample-slug" / "reviews" / "selective"

    # council
    assert paths.council == tmp_path / "sample-slug" / "council"
    assert paths.council_positions == tmp_path / "sample-slug" / "council" / "positions"
    assert paths.council_challenges == tmp_path / "sample-slug" / "council" / "challenges"
    assert paths.council_revisions == tmp_path / "sample-slug" / "council" / "revisions"
    assert paths.council_result == tmp_path / "sample-slug" / "council" / "result.json"

    # reports
    assert paths.reports == tmp_path / "sample-slug" / "reports"
    assert paths.reports_final == tmp_path / "sample-slug" / "reports" / "final.md"


def test_init_run_creates_paths_and_banner(tmp_path: Path):
    """Verify init_run creates all directories, empty JSONL files, initial index, and state banner."""
    paths = RunPaths(tmp_path, "new-topic")
    result_paths = init_run(paths)

    assert result_paths == paths

    # Directories created
    assert paths.run_dir.is_dir()
    assert paths.sources.is_dir()
    assert paths.stub_corpus_dir.is_dir()
    assert paths.evidence.is_dir()
    assert paths.tasks.is_dir()
    assert paths.tasks_scout.is_dir()
    assert paths.tasks_researchers.is_dir()
    assert paths.tasks_targeted.is_dir()
    assert paths.reviews.is_dir()
    assert paths.reviews_critic.is_dir()
    assert paths.reviews_selective.is_dir()
    assert paths.council.is_dir()
    assert paths.council_positions.is_dir()
    assert paths.council_challenges.is_dir()
    assert paths.council_revisions.is_dir()
    assert paths.reports.is_dir()

    # JSONL files touched and empty
    assert paths.claims.is_file()
    assert paths.claims.read_text() == ""
    assert paths.contradictions.is_file()
    assert paths.contradictions.read_text() == ""

    # source-index.json initialized
    assert paths.source_index.is_file()
    index_data = json.loads(paths.source_index.read_text())
    assert index_data == {"sources": [], "corpus": {}}

    # research-state.md initialized with banner and PRD §11 headings
    assert paths.research_state.is_file()
    state_content = paths.research_state.read_text()
    assert SYNTHETIC_SUMMARY_BANNER in state_content
    assert state_content.startswith(SYNTHETIC_SUMMARY_BANNER)
    assert "# Research Goal" in state_content
    assert "# Current Taxonomy" in state_content
    assert "# Established Findings" in state_content
    assert "# Tentative Findings" in state_content
    assert "# Contradictions" in state_content
    assert "# Unresolved Questions" in state_content
    assert "# Research Gaps" in state_content
    assert "# Current Recommendations" in state_content
    assert "# Next Investigations" in state_content


def test_init_run_requires_run_paths():
    """Verify init_run rejects inputs that are not RunPaths instances (Finding 3)."""
    with pytest.raises(TypeError, match="Expected RunPaths"):
        init_run("/tmp/some-path")  # type: ignore[arg-type]


def test_second_init_run_preserves_content(tmp_path: Path):
    """Verify second init_run is idempotent and preserves existing file content."""
    paths = RunPaths(tmp_path, "preserve-topic")
    init_run(paths)

    # Mutate files
    custom_claim = '{"id": "claim-999", "claim": "Custom"}\n'
    paths.claims.write_text(custom_claim)

    custom_state = f"{SYNTHETIC_SUMMARY_BANNER}\n\n# Research Goal\nCustom goal description\n"
    paths.research_state.write_text(custom_state)

    custom_source_index = {"sources": [{"id": "s1"}], "corpus": {"nb": "123"}}
    paths.source_index.write_text(json.dumps(custom_source_index))

    # Run init_run a second time
    init_run(paths)

    # Content must remain intact (no truncation or overwrite)
    assert paths.claims.read_text() == custom_claim
    assert paths.research_state.read_text() == custom_state
    assert json.loads(paths.source_index.read_text()) == custom_source_index


def test_load_source_index_decodes_admitted_and_rejected(tmp_path: Path):
    """Verify load_source_index decodes admitted and rejected entries."""
    paths = RunPaths(tmp_path, "load-test")
    init_run(paths)

    data = {
        "sources": [
            {
                "id": "src-admitted-1",
                "kind": "paper",
                "tier": "A",
                "url": "https://example.com/paper.pdf",
                "path": None,
                "title": "Admitted Paper",
                "admitted": True,
                "reason": "Peer-reviewed",
                "declared_by": "scout",
                "decided_at": "2026-09-16T10:00:00Z",
                "corpus_ref": {"notebooklm": "uuid-123"},
            },
            {
                "id": "src-rejected-2",
                "kind": "blog_post",
                "tier": None,
                "url": "https://example.com/blog",
                "path": None,
                "title": "Rejected Blog",
                "admitted": False,
                "reason": "Not authoritative",
                "declared_by": "researcher",
                "decided_at": "2026-09-16T10:05:00Z",
                "corpus_ref": {},
            },
        ],
        "corpus": {},
    }
    paths.source_index.write_text(json.dumps(data, indent=2))

    records = load_source_index(paths)
    assert len(records) == 2

    admitted = records[0]
    assert admitted.id == "src-admitted-1"
    assert admitted.admitted is True
    assert admitted.tier == SourceTier.A
    assert admitted.kind == SourceKind.paper
    assert admitted.corpus_ref == {"notebooklm": "uuid-123"}

    rejected = records[1]
    assert rejected.id == "src-rejected-2"
    assert rejected.admitted is False
    assert rejected.tier is None
    assert rejected.kind == SourceKind.blog_post


def test_load_source_index_requires_run_paths():
    """Verify load_source_index rejects inputs that are not RunPaths instances (Finding 3)."""
    with pytest.raises(TypeError, match="Expected RunPaths"):
        load_source_index("/tmp/some-path")  # type: ignore[arg-type]


def test_load_source_index_rejects_malformed_entry(tmp_path: Path):
    """Verify load_source_index raises ValueError on malformed entries."""
    paths = RunPaths(tmp_path, "malformed-test")
    init_run(paths)

    # Malformed entry: missing required 'kind'
    bad_data = {
        "sources": [
            {
                "id": "src-bad",
                "admitted": True,
                "title": "Bad Source",
                "declared_by": "scout",
                "decided_at": "2026-09-16T10:00:00Z",
            }
        ]
    }
    paths.source_index.write_text(json.dumps(bad_data))

    with pytest.raises(ValueError, match="Malformed source record"):
        load_source_index(paths)


def test_load_source_index_errors(tmp_path: Path):
    """Verify load_source_index raises FileNotFoundError for missing file and ValueError for invalid JSON."""
    missing_paths = RunPaths(tmp_path, "non-existent")
    with pytest.raises(FileNotFoundError):
        load_source_index(missing_paths)

    # Corrupt JSON
    paths = RunPaths(tmp_path, "corrupt-json")
    init_run(paths)
    paths.source_index.write_text("not json at all")
    with pytest.raises(ValueError, match="Corrupt JSON"):
        load_source_index(paths)
