"""Deterministic local stub corpus (PLAN §4.7)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
from typing import Sequence

from research_corpus.admission import AdmittedSource, SourceIndex
from research_corpus.artifacts import RunPaths
from research_corpus.corpus import (
    Citation,
    CorpusAnswer,
    CorpusSourceRef,
    CorpusStatus,
    CorpusUnavailable,
)

# Deterministic stopword set covering common English function words.
# Simple and small to leave domain-relevant search tokens intact.
DEFAULT_STOPWORDS: frozenset[str] = frozenset({
    "a", "about", "an", "and", "are", "as", "at", "be", "by", "for",
    "from", "how", "in", "is", "it", "of", "on", "or", "that", "the",
    "this", "to", "was", "what", "when", "where", "which", "who", "why", "with",
})


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase alphanumeric words."""
    return re.findall(r"[a-z0-9]+", text.lower())


class LocalStubCorpus:
    """Deterministic local filesystem-backed stub corpus for gating acceptance (PLAN §4.7).

    Stores ingested source text under `sources/stub-corpus/<source_id>.txt`.
    Queries tokenize the question, score candidate lines by matched-token count,
    and return per source its best line (score > 0) ordered by (score desc, source_id asc).
    """

    provider: str = "stub"

    def __init__(self, paths: RunPaths, *, available: bool = True) -> None:
        if not isinstance(paths, RunPaths):
            raise TypeError(f"Expected RunPaths, got {type(paths).__name__}")
        self._paths = paths
        self._available = bool(available)

    def _check_available(self) -> None:
        if not self._available:
            raise CorpusUnavailable("stub marked unavailable")

    def status(self) -> CorpusStatus:
        """Probe provider availability without raising (I11)."""
        if not self._available:
            return CorpusStatus(
                available=False,
                provider=self.provider,
                detail="stub marked unavailable",
            )
        return CorpusStatus(
            available=True,
            provider=self.provider,
            detail="local stub corpus ready",
        )

    def has_source(self, source_id: str) -> bool:
        """Return True if <source_id>.txt exists in the stub corpus directory."""
        self._check_available()
        if not isinstance(source_id, str):
            raise TypeError(f"source_id must be a string, got {type(source_id).__name__}")
        target_file = self._paths.stub_corpus_dir / f"{source_id}.txt"
        return target_file.is_file()

    def ingest(
        self,
        source: AdmittedSource,
        *,
        content: str | Path | None = None,
    ) -> CorpusSourceRef:
        """Ingest an admitted source into the stub corpus.

        Only accepts AdmittedSource (enforced by type).
        Re-ingestion returns reused=True without rewriting the file.
        Content is required for URL-only sources without local files.
        """
        self._check_available()
        if not isinstance(source, AdmittedSource):
            raise TypeError(f"ingest requires AdmittedSource, got {type(source).__name__}")

        source_id = source.id
        provider_ref = source_id

        # Dedup check
        if self.has_source(source_id):
            return CorpusSourceRef(
                source_id=source_id,
                provider=self.provider,
                provider_ref=provider_ref,
                reused=True,
            )

        # Resolve content to write
        if content is not None:
            if isinstance(content, Path):
                text_to_write = content.read_text(encoding="utf-8")
            elif isinstance(content, str):
                text_to_write = content
            else:
                raise TypeError(f"content must be str, Path, or None, got {type(content).__name__}")
        else:
            if source.path is not None:
                source_path = Path(source.path)
                if not source_path.is_file():
                    raise FileNotFoundError(f"Source file not found: {source.path}")
                text_to_write = source_path.read_text(encoding="utf-8")
            else:
                raise ValueError(
                    f"Stub corpus requires content for source '{source_id}' without a local file"
                )

        # Record provider_ref into source-index.json before writing the file
        # so any index write failure propagates before half-state is saved (PLAN §4.6)
        if self._paths.source_index.exists():
            index = SourceIndex(self._paths)
            rec = index.get(source_id)
            if rec is not None and rec.corpus_ref.get(self.provider) != provider_ref:
                new_corpus_ref = dict(rec.corpus_ref)
                new_corpus_ref[self.provider] = provider_ref
                updated_rec = replace(rec, corpus_ref=new_corpus_ref)
                index.write_record(updated_rec)

        # Write to sources/stub-corpus/<source_id>.txt
        self._paths.stub_corpus_dir.mkdir(parents=True, exist_ok=True)
        target_file = self._paths.stub_corpus_dir / f"{source_id}.txt"
        target_file.write_text(text_to_write, encoding="utf-8")

        return CorpusSourceRef(
            source_id=source_id,
            provider=self.provider,
            provider_ref=provider_ref,
            reused=False,
        )

    def query(
        self,
        question: str,
        *,
        source_ids: Sequence[str] | None = None,
    ) -> CorpusAnswer:
        """Query the stub corpus, returning scored line citations and formatted answer.

        Deterministic scoring:
        - Question is tokenized into lowercase alphanumeric tokens with stopwords dropped.
        - Lines in each in-scope source are scored by count of matched question tokens.
        - Best line (score > 0) per source returned with 1-indexed locator.
        - Results ordered by (score desc, source_id asc).
        - When source_ids is provided, raises ValueError if any requested source_id is not ingested.
        """
        self._check_available()
        if not isinstance(question, str):
            raise TypeError(f"question must be a str, got {type(question).__name__}")

        if source_ids is not None:
            unknown = [sid for sid in source_ids if not self.has_source(sid)]
            if unknown:
                raise ValueError(f"Source ID(s) not ingested in corpus: {unknown}")
            target_sids = sorted(set(source_ids))
        else:
            if not self._paths.stub_corpus_dir.exists():
                return CorpusAnswer(answer="", citations=[], provider=self.provider)
            target_sids = sorted(p.stem for p in self._paths.stub_corpus_dir.glob("*.txt"))

        if not target_sids:
            return CorpusAnswer(answer="", citations=[], provider=self.provider)

        raw_tokens = _tokenize(question)
        q_tokens = [tok for tok in raw_tokens if tok not in DEFAULT_STOPWORDS]
        if not q_tokens:
            q_tokens = raw_tokens
        q_terms = set(q_tokens)

        candidates: list[tuple[int, str, str, int]] = []
        for sid in target_sids:
            source_file = self._paths.stub_corpus_dir / f"{sid}.txt"
            if not source_file.is_file():
                continue
            content = source_file.read_text(encoding="utf-8")
            lines = content.splitlines()

            best_score = 0
            best_line = ""
            best_line_num = 0

            for line_num, line in enumerate(lines, start=1):
                line_terms = set(_tokenize(line))
                score = len(q_terms & line_terms)
                if score > best_score:
                    best_score = score
                    best_line = line.strip()
                    best_line_num = line_num

            if best_score > 0:
                candidates.append((best_score, sid, best_line, best_line_num))

        # Order results by (score desc, source_id asc)
        candidates.sort(key=lambda item: (-item[0], item[1]))

        citations = [
            Citation(
                source_id=sid,
                excerpt=line,
                locator=f"line {line_num}",
                provider_ref=sid,
            )
            for _, sid, line, line_num in candidates
        ]
        answer = "\n".join(f"[{i}] {c.excerpt}" for i, c in enumerate(citations, 1))

        return CorpusAnswer(
            answer=answer,
            citations=citations,
            provider=self.provider,
        )
