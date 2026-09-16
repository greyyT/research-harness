"""Artifact tree, run path resolution, and run initialization."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Union

from research_corpus.records import SourceRecord, decode_source_record

SYNTHETIC_SUMMARY_BANNER = "> SYNTHETIC SUMMARY — not evidence. Never cite this file as a source."

RESEARCH_STATE_SKELETON = f"""{SYNTHETIC_SUMMARY_BANNER}

# Research Goal

# Current Taxonomy

# Established Findings

# Tentative Findings

# Contradictions

# Unresolved Questions

# Research Gaps

# Current Recommendations

# Next Investigations
"""


@dataclass(frozen=True)
class RunPaths:
    """Resolved file and directory paths for a research topic run per PRD §26."""

    root: Path
    slug: str

    def __init__(self, root: Union[Path, str], slug: str) -> None:
        object.__setattr__(self, "root", Path(root))
        object.__setattr__(self, "slug", slug)

    @property
    def run_dir(self) -> Path:
        return self.root / self.slug

    @property
    def brief(self) -> Path:
        return self.run_dir / "brief.md"

    @property
    def research_state(self) -> Path:
        return self.run_dir / "research-state.md"

    @property
    def sources(self) -> Path:
        return self.run_dir / "sources"

    @property
    def source_index(self) -> Path:
        return self.sources / "source-index.json"

    @property
    def stub_corpus_dir(self) -> Path:
        return self.sources / "stub-corpus"

    @property
    def evidence(self) -> Path:
        return self.run_dir / "evidence"

    @property
    def claims(self) -> Path:
        return self.evidence / "claims.jsonl"

    @property
    def contradictions(self) -> Path:
        return self.evidence / "contradictions.jsonl"

    @property
    def index_sqlite(self) -> Path:
        return self.evidence / "index.sqlite"

    @property
    def tasks(self) -> Path:
        return self.run_dir / "tasks"

    @property
    def tasks_scout(self) -> Path:
        return self.tasks / "scout"

    @property
    def tasks_researchers(self) -> Path:
        return self.tasks / "researchers"

    @property
    def tasks_targeted(self) -> Path:
        return self.tasks / "targeted"

    @property
    def reviews(self) -> Path:
        return self.run_dir / "reviews"

    @property
    def reviews_critic(self) -> Path:
        return self.reviews / "critic"

    @property
    def reviews_selective(self) -> Path:
        return self.reviews / "selective"

    @property
    def council(self) -> Path:
        return self.run_dir / "council"

    @property
    def council_positions(self) -> Path:
        return self.council / "positions"

    @property
    def council_challenges(self) -> Path:
        return self.council / "challenges"

    @property
    def council_revisions(self) -> Path:
        return self.council / "revisions"

    @property
    def council_result(self) -> Path:
        return self.council / "result.json"

    @property
    def reports(self) -> Path:
        return self.run_dir / "reports"

    @property
    def reports_final(self) -> Path:
        return self.reports / "final.md"


def init_run(paths: RunPaths) -> RunPaths:
    """Initialize the research run directory tree idempotently without truncating."""
    if not isinstance(paths, RunPaths):
        raise TypeError(f"Expected RunPaths, got {type(paths)}")

    dirs_to_create = [
        paths.run_dir,
        paths.sources,
        paths.stub_corpus_dir,
        paths.evidence,
        paths.tasks,
        paths.tasks_scout,
        paths.tasks_researchers,
        paths.tasks_targeted,
        paths.reviews,
        paths.reviews_critic,
        paths.reviews_selective,
        paths.council,
        paths.council_positions,
        paths.council_challenges,
        paths.council_revisions,
        paths.reports,
    ]
    for d in dirs_to_create:
        d.mkdir(parents=True, exist_ok=True)

    if not paths.claims.exists():
        paths.claims.touch()

    if not paths.contradictions.exists():
        paths.contradictions.touch()

    if not paths.source_index.exists():
        initial_source_index = {"sources": [], "corpus": {}}
        paths.source_index.write_text(
            json.dumps(initial_source_index, indent=2) + "\n",
            encoding="utf-8",
        )

    if not paths.research_state.exists():
        paths.research_state.write_text(RESEARCH_STATE_SKELETON, encoding="utf-8")

    return paths


def load_source_index(paths: RunPaths) -> list[SourceRecord]:
    """Read-only loader for source-index.json, returning list[SourceRecord]."""
    if not isinstance(paths, RunPaths):
        raise TypeError(f"Expected RunPaths, got {type(paths)}")

    index_file = paths.source_index
    if not index_file.exists():
        raise FileNotFoundError(f"Source index file not found: {index_file}")

    content = index_file.read_text(encoding="utf-8")
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Corrupt JSON in {index_file}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Root of {index_file} must be an object, got {type(data)}")

    raw_sources = data.get("sources")
    if not isinstance(raw_sources, list):
        raise ValueError(f"'sources' key in {index_file} must be a list, got {type(raw_sources)}")

    records: list[SourceRecord] = []
    for idx, item in enumerate(raw_sources):
        if not isinstance(item, dict):
            raise ValueError(f"Source entry at index {idx} in {index_file} must be an object")
        try:
            records.append(decode_source_record(item))
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"Malformed source record at index {idx} in {index_file}: {exc}") from exc

    return records


__all__ = [
    "RESEARCH_STATE_SKELETON",
    "SYNTHETIC_SUMMARY_BANNER",
    "RunPaths",
    "init_run",
    "load_source_index",
]
