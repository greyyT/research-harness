"""Research Corpus package - persistence, schemas, and artifact tree."""

from research_corpus.artifacts import (
    RESEARCH_STATE_SKELETON,
    SYNTHETIC_SUMMARY_BANNER,
    RunPaths,
    init_run,
    load_source_index,
)
from research_corpus.index import LedgerIndex
from research_corpus.ledger import (
    ClaimQuery,
    Ledger,
    LedgerCorruptError,
)
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

__all__ = [
    "RESEARCH_STATE_SKELETON",
    "SYNTHETIC_SUMMARY_BANNER",
    "AdmissionDecision",
    "ClaimQuery",
    "ClaimRecord",
    "ClaimStatus",
    "ClaimType",
    "Confidence",
    "ContradictionRecord",
    "EvidenceRef",
    "Ledger",
    "LedgerCorruptError",
    "LedgerIndex",
    "RunPaths",
    "SourceKind",
    "SourceRecord",
    "SourceTier",
    "decode_source_record",
    "from_json",
    "init_run",
    "load_source_index",
    "to_json",
]
