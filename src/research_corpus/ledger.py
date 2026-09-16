"""Evidence Ledger store and pure scan query engine."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from research_corpus.artifacts import RunPaths, load_source_index
from research_corpus.records import (
    ClaimRecord,
    ClaimStatus,
    Confidence,
    ContradictionRecord,
    from_json,
    to_json,
)


class LedgerCorruptError(Exception):
    """Raised when a JSONL line in the ledger is malformed or invalid."""

    def __init__(self, path: Path | str, line_number: int, message: str) -> None:
        self.path = Path(path)
        self.line_number = line_number
        self.message = message
        super().__init__(f"Corrupt record in {self.path} at line {line_number}: {message}")


def _read_claims_file(path: Path) -> list[tuple[ClaimRecord, str]]:
    """Read and validate claims from a JSONL file, returning (record, raw_json_line) pairs."""
    if not path.exists():
        return []
    claims: list[tuple[ClaimRecord, str]] = []
    seen_ids: set[str] = set()
    with open(path, mode="r", encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.rstrip("\r\n")
            if not line:
                raise LedgerCorruptError(
                    path=path,
                    line_number=line_no,
                    message="Empty line in JSONL file",
                )
            try:
                record = from_json(ClaimRecord, line)
            except Exception as exc:
                raise LedgerCorruptError(
                    path=path,
                    line_number=line_no,
                    message=str(exc),
                ) from exc
            if record.id in seen_ids:
                raise LedgerCorruptError(
                    path=path,
                    line_number=line_no,
                    message=f"Duplicate claim id: {record.id!r}",
                )
            seen_ids.add(record.id)
            claims.append((record, line))
    return claims


def _read_contradictions_file(path: Path) -> list[tuple[ContradictionRecord, str]]:
    """Read and validate contradictions from a JSONL file, returning (record, raw_json_line) pairs."""
    if not path.exists():
        return []
    contradictions: list[tuple[ContradictionRecord, str]] = []
    seen_ids: set[str] = set()
    with open(path, mode="r", encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.rstrip("\r\n")
            if not line:
                raise LedgerCorruptError(
                    path=path,
                    line_number=line_no,
                    message="Empty line in JSONL file",
                )
            try:
                record = from_json(ContradictionRecord, line)
            except Exception as exc:
                raise LedgerCorruptError(
                    path=path,
                    line_number=line_no,
                    message=str(exc),
                ) from exc
            if record.id in seen_ids:
                raise LedgerCorruptError(
                    path=path,
                    line_number=line_no,
                    message=f"Duplicate contradiction id: {record.id!r}",
                )
            seen_ids.add(record.id)
            contradictions.append((record, line))
    return contradictions


@dataclass(frozen=True)
class ClaimQuery:
    """Query filter parameters for the Evidence Ledger."""

    topic: str | None = None
    confidence: Confidence | None = None
    researcher: str | None = None
    source_id: str | None = None  # claim.sources contains OR any evidence.source_id ==
    evidence_type: str | None = None  # any evidence.evidence_type ==
    status: ClaimStatus | None = None
    contradiction: str | None = None  # "any", "none", "<claim-id>", "contradiction:<id>"

    def __post_init__(self) -> None:
        if self.confidence is not None and not isinstance(self.confidence, Confidence):
            try:
                object.__setattr__(self, "confidence", Confidence(self.confidence))
            except ValueError as exc:
                raise ValueError(f"Invalid Confidence: {self.confidence!r}") from exc

        if self.status is not None and not isinstance(self.status, ClaimStatus):
            try:
                object.__setattr__(self, "status", ClaimStatus(self.status))
            except ValueError as exc:
                raise ValueError(f"Invalid ClaimStatus: {self.status!r}") from exc

        if self.contradiction is not None:
            if not isinstance(self.contradiction, str):
                raise ValueError(f"contradiction filter must be a string, got {type(self.contradiction)}")
            val = self.contradiction.strip()
            if not val:
                raise ValueError("contradiction filter cannot be empty")
            if val.startswith("contradiction:"):
                contra_id = val[len("contradiction:"):].strip()
                if not contra_id:
                    raise ValueError(f"Invalid contradiction filter syntax: missing id in {self.contradiction!r}")


class Ledger:
    """Append-only Evidence Ledger for claims and contradictions."""

    def __init__(self, paths: RunPaths) -> None:
        if not isinstance(paths, RunPaths):
            raise TypeError(f"Expected RunPaths, got {type(paths)}")
        self.paths = paths
        from research_corpus.index import LedgerIndex

        self.index = LedgerIndex(paths)

    def _load_claims(self) -> list[ClaimRecord]:
        return [r for r, _ in _read_claims_file(self.paths.claims)]

    def _load_contradictions(self) -> list[ContradictionRecord]:
        return [r for r, _ in _read_contradictions_file(self.paths.contradictions)]

    def iter_claims(self) -> Iterator[ClaimRecord]:
        """Iterate over all claims in claims.jsonl."""
        return iter(self._load_claims())

    def iter_contradictions(self) -> Iterator[ContradictionRecord]:
        """Iterate over all contradictions in contradictions.jsonl."""
        return iter(self._load_contradictions())

    def __iter__(self) -> Iterator[ClaimRecord]:
        """Iterating over the ledger yields all claims."""
        return self.iter_claims()

    def append_claim(self, claim: ClaimRecord) -> None:
        """Append a ClaimRecord to claims.jsonl enforcing traceability and append-only invariants."""
        if not isinstance(claim, ClaimRecord):
            raise TypeError(f"Expected ClaimRecord, got {type(claim)}")

        # Validate complete record through S1 codec before touching files or checks
        claim = from_json(ClaimRecord, to_json(claim))

        # Validate empty evidence rules per I7
        if not claim.evidence and claim.status not in (
            ClaimStatus.needs_more_evidence,
            ClaimStatus.unresolved,
            ClaimStatus.rejected,
        ):
            raise ValueError(
                f"Claim with status {claim.status.value!r} must have at least one evidence reference"
            )

        if claim.evidence:
            source_records = load_source_index(self.paths)
            sources_by_id = {s.id: s for s in source_records}

            for ref in claim.evidence:
                if ref.source_id not in sources_by_id:
                    raise ValueError(f"Unknown evidence source id: {ref.source_id!r}")

                src = sources_by_id[ref.source_id]
                if not src.admitted:
                    raise ValueError(
                        f"Evidence source id {ref.source_id!r} was rejected and cannot be cited as evidence"
                    )

        # Check duplicate claim id
        existing_claims = self._load_claims()
        if any(c.id == claim.id for c in existing_claims):
            raise ValueError(f"Duplicate claim id: {claim.id!r}")

        self.paths.claims.parent.mkdir(parents=True, exist_ok=True)
        line = to_json(claim) + "\n"
        with open(self.paths.claims, mode="a", encoding="utf-8") as f:
            f.write(line)

    def append_contradiction(self, contradiction: ContradictionRecord) -> None:
        """Append a ContradictionRecord to contradictions.jsonl enforcing integrity invariants."""
        if not isinstance(contradiction, ContradictionRecord):
            raise TypeError(f"Expected ContradictionRecord, got {type(contradiction)}")

        # Validate complete record through S1 codec before touching files or checks
        contradiction = from_json(ContradictionRecord, to_json(contradiction))

        if not contradiction.claim_ids:
            raise ValueError("ContradictionRecord.claim_ids cannot be empty")

        existing_claims = self._load_claims()
        existing_claim_ids = {c.id for c in existing_claims}
        for cid in contradiction.claim_ids:
            if cid not in existing_claim_ids:
                raise ValueError(
                    f"ContradictionRecord references unknown claim id: {cid!r}"
                )

        existing_contradictions = self._load_contradictions()
        if any(c.id == contradiction.id for c in existing_contradictions):
            raise ValueError(f"Duplicate contradiction id: {contradiction.id!r}")

        self.paths.contradictions.parent.mkdir(parents=True, exist_ok=True)
        line = to_json(contradiction) + "\n"
        with open(self.paths.contradictions, mode="a", encoding="utf-8") as f:
            f.write(line)

    def append(self, record: ClaimRecord | ContradictionRecord) -> None:
        """Append either a ClaimRecord or ContradictionRecord."""
        if isinstance(record, ClaimRecord):
            self.append_claim(record)
        elif isinstance(record, ContradictionRecord):
            self.append_contradiction(record)
        else:
            raise TypeError(f"Expected ClaimRecord or ContradictionRecord, got {type(record)}")

    def scan(self, query: ClaimQuery | None = None) -> list[ClaimRecord]:
        """Pure JSONL query engine matching all set filter dimensions with AND."""
        if query is not None and not isinstance(query, ClaimQuery):
            raise TypeError(f"Expected ClaimQuery, got {type(query)}")
        claims = self._load_claims()
        if query is None:
            return sorted(claims, key=lambda c: c.id)

        contra_claim_ids: set[str] | None = None
        if query.contradiction is not None and query.contradiction.startswith("contradiction:"):
            target_contra_id = query.contradiction[len("contradiction:"):].strip()
            contradictions = self._load_contradictions()
            matching_contra = next((c for c in contradictions if c.id == target_contra_id), None)
            contra_claim_ids = set(matching_contra.claim_ids) if matching_contra else set()

        matched: list[ClaimRecord] = []
        for claim in claims:
            if query.topic is not None and claim.topic != query.topic:
                continue

            if query.confidence is not None:
                expected_conf = (
                    Confidence(query.confidence)
                    if isinstance(query.confidence, str)
                    else query.confidence
                )
                if claim.confidence != expected_conf:
                    continue

            if query.researcher is not None and claim.researcher != query.researcher:
                continue

            if query.source_id is not None:
                in_sources = query.source_id in claim.sources
                in_evidence = any(e.source_id == query.source_id for e in claim.evidence)
                if not (in_sources or in_evidence):
                    continue

            if query.evidence_type is not None and not any(
                e.evidence_type == query.evidence_type for e in claim.evidence
            ):
                continue

            if query.status is not None:
                expected_status = (
                    ClaimStatus(query.status)
                    if isinstance(query.status, str)
                    else query.status
                )
                if claim.status != expected_status:
                    continue

            if query.contradiction is not None:
                if query.contradiction == "any":
                    if not claim.conflicts:
                        continue
                elif query.contradiction == "none":
                    if claim.conflicts:
                        continue
                elif query.contradiction.startswith("contradiction:"):
                    assert contra_claim_ids is not None
                    if claim.id not in contra_claim_ids:
                        continue
                elif query.contradiction not in claim.conflicts:
                    continue

            matched.append(claim)

        return sorted(matched, key=lambda c: c.id)

    def query(self, query: ClaimQuery | None = None) -> list[ClaimRecord]:
        """Query claims using the SQLite index when available, falling back to pure scan if SQLite fails."""
        if query is not None and not isinstance(query, ClaimQuery):
            raise TypeError(f"Expected ClaimQuery, got {type(query)}")
        try:
            return self.index.query(query)
        except (sqlite3.Error, OSError):
            return self.scan(query)


__all__ = [
    "ClaimQuery",
    "Ledger",
    "LedgerCorruptError",
]
