"""Research Corpus package - persistence, schemas, and artifact tree."""

from research_corpus.admission import (
    TIER_MAP,
    AdmittedSource,
    SourceCandidate,
    SourceIndex,
    admit,
    reject,
)
from research_corpus.artifacts import (
    RESEARCH_STATE_SKELETON,
    SYNTHETIC_SUMMARY_BANNER,
    RunPaths,
    init_run,
    load_source_index,
)
from research_corpus.corpus import (
    Citation,
    Corpus,
    CorpusAnswer,
    CorpusIngestError,
    CorpusSourceRef,
    CorpusStatus,
    CorpusUnavailable,
)
from research_corpus.corpus.stub import LocalStubCorpus
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
    "TIER_MAP",
    "AdmissionDecision",
    "AdmittedSource",
    "Citation",
    "ClaimQuery",
    "ClaimRecord",
    "ClaimStatus",
    "ClaimType",
    "Confidence",
    "ContradictionRecord",
    "Corpus",
    "CorpusAnswer",
    "CorpusIngestError",
    "CorpusSourceRef",
    "CorpusStatus",
    "CorpusUnavailable",
    "EvidenceRef",
    "Ledger",
    "LedgerCorruptError",
    "LedgerIndex",
    "LocalStubCorpus",
    "RunPaths",
    "SourceCandidate",
    "SourceIndex",
    "SourceKind",
    "SourceRecord",
    "SourceTier",
    "admit",
    "decode_source_record",
    "from_json",
    "init_run",
    "load_source_index",
    "reject",
    "to_json",
]
