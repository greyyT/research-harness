"""Record enums and schemas for the research corpus layer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from typing import Any, TypeVar

T = TypeVar("T")


class ClaimStatus(str, Enum):
    """Claim statuses defined in PRD §14."""

    supported = "supported"
    tentative = "tentative"
    contradicted = "contradicted"
    needs_more_evidence = "needs_more_evidence"
    source_too_weak = "source_too_weak"
    unresolved = "unresolved"
    rejected = "rejected"


class ClaimType(str, Enum):
    """Claim types defined in PRD §4.1."""

    fact = "fact"
    source_claim = "source_claim"
    inference = "inference"
    hypothesis = "hypothesis"
    open_question = "open_question"


class Confidence(str, Enum):
    """Confidence levels."""

    high = "high"
    medium = "medium"
    low = "low"


class SourceTier(str, Enum):
    """Source tiers defined in PRD §9."""

    A = "A"
    B = "B"
    C = "C"


class SourceKind(str, Enum):
    """Source kinds mapped to tiers per PRD §9."""

    # Tier A
    paper = "paper"
    official_benchmark = "official_benchmark"
    official_repository = "official_repository"
    official_specification = "official_specification"
    official_documentation = "official_documentation"
    original_dataset = "original_dataset"

    # Tier B
    engineering_report = "engineering_report"
    independent_reproduction = "independent_reproduction"
    technical_analysis = "technical_analysis"
    conference_presentation = "conference_presentation"

    # Tier C
    blog_post = "blog_post"
    discussion = "discussion"
    forum_thread = "forum_thread"
    reddit = "reddit"
    informal_explanation = "informal_explanation"


class AdmissionDecision(str, Enum):
    """Admission decision outcomes."""

    admitted = "admitted"
    rejected = "rejected"


def _require_non_empty_str(val: Any, field_name: str) -> str:
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"Field '{field_name}' must be a non-empty string, got {val!r}")
    return val


def _require_str(val: Any, field_name: str) -> str:
    if not isinstance(val, str):
        raise ValueError(f"Field '{field_name}' must be a string, got {val!r}")
    return val


def _require_list_of_str(val: Any, field_name: str) -> list[str]:
    if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
        raise ValueError(f"Field '{field_name}' must be a list of strings, got {val!r}")
    return list(val)


@dataclass(frozen=True)
class EvidenceRef:
    """Reference to verbatim source evidence for a claim."""

    source_id: str
    evidence_type: str
    locator: str
    excerpt: str


@dataclass(frozen=True)
class ClaimRecord:
    """A claim record in the Evidence Ledger (PRD §14 + I1)."""

    id: str
    claim: str
    type: ClaimType
    confidence: Confidence
    sources: list[str]
    caveats: list[str]
    conflicts: list[str]
    status: ClaimStatus
    topic: str
    researcher: str
    evidence: list[EvidenceRef]
    created_at: str


@dataclass(frozen=True)
class ContradictionRecord:
    """A contradiction record in the Evidence Ledger."""

    id: str
    claim_ids: list[str]
    description: str
    status: ClaimStatus
    created_at: str


@dataclass(frozen=True)
class SourceRecord:
    """A source record in source-index.json."""

    id: str
    kind: SourceKind
    tier: SourceTier | None
    url: str | None
    path: str | None
    title: str
    admitted: bool
    reason: str
    declared_by: str
    decided_at: str
    corpus_ref: dict[str, str]


def _decode_evidence_ref(data: Any) -> EvidenceRef:
    if not isinstance(data, dict):
        raise ValueError(f"EvidenceRef data must be a dict, got {type(data)}")
    source_id = _require_non_empty_str(data.get("source_id"), "source_id")
    evidence_type = _require_str(data.get("evidence_type", ""), "evidence_type")
    locator = _require_str(data.get("locator", ""), "locator")
    excerpt = _require_non_empty_str(data.get("excerpt"), "excerpt")
    return EvidenceRef(
        source_id=source_id,
        evidence_type=evidence_type,
        locator=locator,
        excerpt=excerpt,
    )


def _decode_claim_record(data: Any) -> ClaimRecord:
    if not isinstance(data, dict):
        raise ValueError(f"ClaimRecord data must be a dict, got {type(data)}")

    id_ = _require_non_empty_str(data.get("id"), "id")
    claim = _require_str(data.get("claim"), "claim")

    type_raw = data.get("type")
    if not type_raw:
        raise ValueError("Field 'type' is required")
    try:
        type_ = ClaimType(type_raw)
    except ValueError as exc:
        raise ValueError(f"Invalid ClaimType: {type_raw!r}") from exc

    conf_raw = data.get("confidence")
    if not conf_raw:
        raise ValueError("Field 'confidence' is required")
    try:
        confidence = Confidence(conf_raw)
    except ValueError as exc:
        raise ValueError(f"Invalid Confidence: {conf_raw!r}") from exc

    status_raw = data.get("status")
    if not status_raw:
        raise ValueError("Field 'status' is required")
    try:
        status = ClaimStatus(status_raw)
    except ValueError as exc:
        raise ValueError(f"Invalid ClaimStatus: {status_raw!r}") from exc

    topic = _require_str(data.get("topic", ""), "topic")
    researcher = _require_str(data.get("researcher", ""), "researcher")
    created_at = _require_non_empty_str(data.get("created_at"), "created_at")

    raw_evidence = data.get("evidence", [])
    if not isinstance(raw_evidence, list):
        raise ValueError(f"Field 'evidence' must be a list, got {type(raw_evidence)}")
    evidence = [_decode_evidence_ref(item) for item in raw_evidence]

    derived_sources = list(dict.fromkeys(e.source_id for e in evidence))
    raw_sources = data.get("sources")
    if raw_sources is None:
        sources = derived_sources
    else:
        supplied_sources = _require_list_of_str(raw_sources, "sources")
        if supplied_sources != derived_sources:
            raise ValueError(
                f"Supplied sources {supplied_sources!r} does not match evidence source ids in order of appearance {derived_sources!r}"
            )
        sources = supplied_sources

    caveats = _require_list_of_str(data.get("caveats", []), "caveats")
    conflicts = _require_list_of_str(data.get("conflicts", []), "conflicts")

    return ClaimRecord(
        id=id_,
        claim=claim,
        type=type_,
        confidence=confidence,
        sources=sources,
        caveats=caveats,
        conflicts=conflicts,
        status=status,
        topic=topic,
        researcher=researcher,
        evidence=evidence,
        created_at=created_at,
    )


def _decode_contradiction_record(data: Any) -> ContradictionRecord:
    if not isinstance(data, dict):
        raise ValueError(f"ContradictionRecord data must be a dict, got {type(data)}")

    id_ = _require_non_empty_str(data.get("id"), "id")
    claim_ids = _require_list_of_str(data.get("claim_ids", []), "claim_ids")
    description = _require_str(data.get("description", ""), "description")

    status_raw = data.get("status")
    if not status_raw:
        raise ValueError("Field 'status' is required")
    try:
        status = ClaimStatus(status_raw)
    except ValueError as exc:
        raise ValueError(f"Invalid ClaimStatus: {status_raw!r}") from exc

    created_at = _require_non_empty_str(data.get("created_at"), "created_at")
    return ContradictionRecord(
        id=id_,
        claim_ids=claim_ids,
        description=description,
        status=status,
        created_at=created_at,
    )


def decode_source_record(data: Any) -> SourceRecord:
    """Decode and validate a dictionary to a SourceRecord instance."""
    if not isinstance(data, dict):
        raise ValueError(f"SourceRecord data must be a dict, got {type(data)}")

    id_ = _require_non_empty_str(data.get("id"), "id")

    kind_raw = data.get("kind")
    if not kind_raw:
        raise ValueError("Field 'kind' is required")
    try:
        kind = SourceKind(kind_raw)
    except ValueError as exc:
        raise ValueError(f"Invalid SourceKind: {kind_raw!r}") from exc

    tier_raw = data.get("tier")
    if tier_raw is not None:
        try:
            tier: SourceTier | None = SourceTier(tier_raw)
        except ValueError as exc:
            raise ValueError(f"Invalid SourceTier: {tier_raw!r}") from exc
    else:
        tier = None

    admitted = data.get("admitted")
    if not isinstance(admitted, bool):
        raise ValueError(f"Field 'admitted' must be a bool, got {admitted!r}")

    url = data.get("url")
    if url is not None and not isinstance(url, str):
        raise ValueError(f"Field 'url' must be string or None, got {type(url)}")

    path = data.get("path")
    if path is not None and not isinstance(path, str):
        raise ValueError(f"Field 'path' must be string or None, got {type(path)}")

    title = _require_str(data.get("title", ""), "title")
    reason = _require_str(data.get("reason", ""), "reason")
    declared_by = _require_str(data.get("declared_by", ""), "declared_by")
    decided_at = _require_non_empty_str(data.get("decided_at"), "decided_at")

    corpus_ref_raw = data.get("corpus_ref", {})
    if not isinstance(corpus_ref_raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in corpus_ref_raw.items()
    ):
        raise ValueError("Field 'corpus_ref' must be dict[str, str]")
    corpus_ref = dict(corpus_ref_raw)

    return SourceRecord(
        id=id_,
        kind=kind,
        tier=tier,
        url=url,
        path=path,
        title=title,
        admitted=admitted,
        reason=reason,
        declared_by=declared_by,
        decided_at=decided_at,
        corpus_ref=corpus_ref,
    )


def _record_to_dict(record: Any) -> dict[str, Any]:
    if isinstance(record, EvidenceRef):
        return {
            "source_id": record.source_id,
            "evidence_type": record.evidence_type,
            "locator": record.locator,
            "excerpt": record.excerpt,
        }
    if isinstance(record, ClaimRecord):
        return {
            "id": record.id,
            "claim": record.claim,
            "type": record.type.value,
            "confidence": record.confidence.value,
            "sources": list(record.sources),
            "caveats": list(record.caveats),
            "conflicts": list(record.conflicts),
            "status": record.status.value,
            "topic": record.topic,
            "researcher": record.researcher,
            "evidence": [_record_to_dict(e) for e in record.evidence],
            "created_at": record.created_at,
        }
    if isinstance(record, ContradictionRecord):
        return {
            "id": record.id,
            "claim_ids": list(record.claim_ids),
            "description": record.description,
            "status": record.status.value,
            "created_at": record.created_at,
        }
    if isinstance(record, SourceRecord):
        return {
            "id": record.id,
            "kind": record.kind.value,
            "tier": record.tier.value if record.tier is not None else None,
            "url": record.url,
            "path": record.path,
            "title": record.title,
            "admitted": record.admitted,
            "reason": record.reason,
            "declared_by": record.declared_by,
            "decided_at": record.decided_at,
            "corpus_ref": dict(record.corpus_ref),
        }
    raise TypeError(f"Unsupported record type: {type(record)}")


def to_json(record: Any) -> str:
    """Encode record as deterministic, single-line JSON string (sort_keys=True)."""
    payload = _record_to_dict(record)
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def from_json(cls: type[T], s: str) -> T:
    """Decode and validate JSON string to the given record type."""
    if not isinstance(s, str):
        raise TypeError(f"Expected str, got {type(s)}")
    try:
        data = json.loads(s)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON string: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data)}")

    if cls is EvidenceRef:
        return _decode_evidence_ref(data)  # type: ignore[return-value]
    if cls is ClaimRecord:
        return _decode_claim_record(data)  # type: ignore[return-value]
    if cls is ContradictionRecord:
        return _decode_contradiction_record(data)  # type: ignore[return-value]
    if cls is SourceRecord:
        return decode_source_record(data)  # type: ignore[return-value]

    raise TypeError(f"Unsupported record type: {cls}")


__all__ = [
    "AdmissionDecision",
    "ClaimRecord",
    "ClaimStatus",
    "ClaimType",
    "Confidence",
    "ContradictionRecord",
    "EvidenceRef",
    "SourceKind",
    "SourceRecord",
    "SourceTier",
    "decode_source_record",
    "from_json",
    "to_json",
]
