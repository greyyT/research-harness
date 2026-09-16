"""Corpus interface and stable contract (PLAN §4.6, I3, I11)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

from research_corpus.admission import AdmittedSource


@dataclass(frozen=True)
class CorpusSourceRef:
    """Reference to an ingested source in a corpus provider."""

    source_id: str
    provider: str
    provider_ref: str
    reused: bool = False


@dataclass(frozen=True)
class Citation:
    """A citation grounded in corpus source text."""

    source_id: str | None = None
    excerpt: str = ""
    locator: str = ""
    provider_ref: str | None = None


@dataclass(frozen=True)
class CorpusAnswer:
    """Answer synthesized or retrieved from a corpus provider."""

    answer: str
    citations: list[Citation]
    provider: str


@dataclass(frozen=True)
class CorpusStatus:
    """Availability status of a corpus provider."""

    available: bool
    provider: str
    detail: str = ""


class CorpusUnavailable(Exception):
    """Raised when a corpus provider is unavailable (unreachable, unauthenticated, etc.)."""


class CorpusIngestError(Exception):
    """Raised when a corpus provider is available but failed to ingest a source."""


@runtime_checkable
class Corpus(Protocol):
    """Protocol defining the stable corpus interface (D3, PLAN §4.6)."""

    provider: str

    def status(self) -> CorpusStatus:
        """Probe provider availability without raising."""
        ...

    def has_source(self, source_id: str) -> bool:
        """Check if source is already present in this corpus."""
        ...

    def ingest(
        self,
        source: AdmittedSource,
        *,
        content: str | Path | None = None,
    ) -> CorpusSourceRef:
        """Ingest an admitted source into this corpus."""
        ...

    def query(
        self,
        question: str,
        *,
        source_ids: Sequence[str] | None = None,
    ) -> CorpusAnswer:
        """Query the corpus with optional source scoping."""
        ...


__all__ = [
    "Citation",
    "Corpus",
    "CorpusAnswer",
    "CorpusIngestError",
    "CorpusSourceRef",
    "CorpusStatus",
    "CorpusUnavailable",
]
