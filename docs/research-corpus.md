# Research Corpus & Evidence Ledger

The `research_corpus` package provides persistent structured evidence tracking and grounded corpus query capabilities for the Autonomous Research System (Milestone 1). It enables deterministic source admission, grounding via NotebookLM (or a local deterministic stub), structured claim recording with verifiable source citations, and indexed multi-dimensional querying.

---

## 1. Source-of-Truth Rules

- **Canonical store**: The filesystem Evidence Ledger (`evidence/claims.jsonl` and `evidence/contradictions.jsonl`) is the canonical, append-only source of truth (D2, PRD §4.2).
- **Corpus and context boundaries**: NotebookLM, LLM context windows, and generated markdown summaries are **never** canonical stores of research findings or evidence. NotebookLM functions strictly as a shared, queryable document store for grounding.
- **Strict traceability (I7)**: Every claim with status `supported`, `tentative`, `contradicted`, or `source_too_weak` must reference at least one admitted source (`admitted=True` in `sources/source-index.json`). Citing unknown source IDs or rejected sources is refused on append. Claims with empty evidence are allowed only when explicitly registering an unevidenced or rejected claim (`needs_more_evidence`, `unresolved`, `rejected`).
- **Non-evidence synthetic summaries**: `research-state.md` is an ephemeral synthetic summary skeleton. It is labeled with a mandatory leading warning banner and cannot be admitted as a source by any path (I6).

---

## 2. Artifact Tree & Record Schemas

### Directory Layout

A research run is stored under `research/<topic-slug>/` (PRD §26):

```text
research/<topic-slug>/
├── brief.md                     # Research scope and initial brief
├── research-state.md            # Synthetic summary skeleton (NOT evidence)
├── sources/
│   ├── source-index.json        # Admitted and rejected sources metadata + corpus binding
│   └── stub-corpus/             # Ingested text files when using LocalStubCorpus
├── evidence/
│   ├── claims.jsonl             # Canonical append-only ClaimRecord store
│   ├── contradictions.jsonl     # Canonical append-only ContradictionRecord store
│   └── index.sqlite*            # Rebuildable query index (git-ignored; do NOT commit)
├── tasks/                       # Specialist task manifests (Milestone 2)
├── reviews/                     # Critic reviews (Milestone 3)
├── council/                     # Council deliberation transcripts (Milestone 3)
└── reports/                     # Final generated reports (Milestone 3)
```

### Record Schemas

All records are serialized as deterministic single-line JSON (`json.dumps(..., sort_keys=True, ensure_ascii=False)`) in JSONL files.

#### EvidenceRef

```python
@dataclass(frozen=True)
class EvidenceRef:
    source_id: str          # Must match an admitted=True record in source-index.json
    evidence_type: str      # Free vocabulary: "paper", "benchmark", "engineering_report", etc.
    locator: str            # Line number, page number, or character offset (e.g. "chars 533-1707")
    excerpt: str            # Verbatim text quote; never empty
```

#### ClaimRecord

```python
@dataclass(frozen=True)
class ClaimRecord:
    id: str                 # Unique claim identifier (e.g. "claim-01-latency-reduction")
    claim: str              # Concrete assertion statement
    type: ClaimType         # fact, source_claim, inference, hypothesis, open_question
    confidence: Confidence  # high, medium, low
    sources: list[str]      # List of source IDs cited by evidence refs
    caveats: list[str]      # Scope limitations, assumptions, or benchmark boundaries
    conflicts: list[str]    # Conflicting claim IDs
    status: ClaimStatus     # Exactly seven valid statuses (see below)
    topic: str              # Research topic category (I1)
    researcher: str         # Originating researcher ID or specialist role (I1)
    evidence: list[EvidenceRef]  # Inspectable citation references (I1)
    created_at: str         # ISO-8601 UTC timestamp
```

#### The Seven Claim Statuses (PRD §14)

1. `supported` — Backed by sufficient admitted evidence.
2. `tentative` — Supported by preliminary or incomplete evidence.
3. `contradicted` — Directly challenged by opposing admitted evidence.
4. `needs_more_evidence` — Hypothesis or claim requiring additional investigation.
5. `source_too_weak` — Evidence is informal or low reliability (e.g. Tier C alone).
6. `unresolved` — Open disagreement across sources without resolution.
7. `rejected` — Explicitly refuted by evidence or rejected by policy.

#### ContradictionRecord

```python
@dataclass(frozen=True)
class ContradictionRecord:
    id: str                 # Unique contradiction identifier (e.g. "contra-001")
    claim_ids: list[str]    # Exactly two or more conflicting claim IDs
    description: str        # Summary of the substantive disagreement
    status: ClaimStatus     # Typically 'contradicted' or 'unresolved'
    created_at: str         # ISO-8601 UTC timestamp
```

#### SourceRecord

```python
@dataclass(frozen=True)
class SourceRecord:
    id: str                 # Deterministic slug + sha256 8-hex digest (I9)
    kind: SourceKind        # PRD §9 source kind enum
    tier: SourceTier | None # Resolved tier: A, B, C (or None if rejected)
    url: str | None         # Normalized URL if candidate was a URL
    path: str | None        # Local filesystem path if candidate was a file
    title: str              # Human-readable title
    admitted: bool          # True if admitted; False if rejected
    reason: str             # Decision justification
    declared_by: str        # Agent or researcher declaring the admission/rejection
    decided_at: str         # ISO-8601 UTC timestamp
    corpus_ref: dict[str, str] # Map of corpus provider to provider ID (e.g. {"stub": "...", "notebooklm": "..."})
```

---

## 3. Source Admission & Tier System

Not every discovered document enters the research corpus (PRD §9). Admission is deliberate, deterministic, and immutable.

### Source Kind to Tier Mapping

The admission module maps declared `SourceKind` to evidence tiers:

- **Tier A (Preferred Primary Evidence)**:
  - `paper`, `official_benchmark`, `official_repository`, `official_specification`, `official_documentation`, `original_dataset`.
- **Tier B (Useful Supporting Evidence)**:
  - `engineering_report`, `independent_reproduction`, `technical_analysis`, `conference_presentation`.
- **Tier C (Discovery & Community Evidence)**:
  - `blog_post`, `discussion`, `forum_thread`, `reddit`, `informal_explanation`.
  - Tier C sources should rarely support high-confidence technical conclusions on their own.

### Admission & Rejection Flow

- `admit()` verifies the candidate is not a `research-state.md` file, computes a deterministic source ID, resolves the tier via `TIER_MAP`, and appends an `admitted=True` entry to `source-index.json`. Re-admitting the same candidate is an idempotent no-op.
- `reject()` records an `admitted=False`, `tier=None` entry with a mandatory non-empty reason.
- Both operations write to `sources/source-index.json`.

---

## 4. Corpus Configuration & Providers

The system supports two corpus implementations behind the unified `Corpus` protocol:

```bash
# Default: deterministic local stub corpus
research-corpus --corpus stub ...

# External: Google NotebookLM adapter
research-corpus --corpus notebooklm ...
```

### NotebookLM Authentication Model

NotebookLM integration requires the optional package extra `research-corpus[notebooklm]`. The underlying client (`notebooklm-py`) is lazily imported so stub and ledger operations run with standard Python libraries only.

Authentication resolves in the following order:
1. `NOTEBOOKLM_AUTH_JSON` environment variable (inline raw storage state JSON).
2. Explicit CLI flag: `--profile <profile_name>`.
3. `NOTEBOOKLM_PROFILE` environment variable.
4. Default storage file: `~/.notebooklm/profiles/default/storage_state.json`.

Interactive authentication is established via:
```bash
notebooklm login --profile research-harness
```

> **Dedicated Account Warning**: NotebookLM does not have an official public API; the adapter interfaces with internal endpoints using session tokens. **Always use a dedicated Google account** for automated research runs to prevent personal session disruption, account rate-limiting, or security exposure. Never commit credential files or tokens to repository trees.

---

## 5. Error Classes & CLI Exit Codes

The CLI strictly avoids Python tracebacks for operational and recoverable errors, formatting failures as single stderr lines.

| Exit Code | Classification | Cause & Recovery |
|-----------|----------------|------------------|
| `0` | Success | Normal command completion. |
| `2` | Usage / Validation Error | Missing arguments, schema decode failure, uninitialized run directory, duplicate IDs, or citing rejected sources. |
| `3` | `CorpusUnavailable` | Corpus provider unreachable, missing authentication profile, missing notebook, or optional `research-corpus[notebooklm]` package extra not installed. Also returned by `corpus status` probe when `available=False`. |
| `4` | `CorpusIngestError` | Provider is active, but a specific source failed document processing or timed out during ingestion. |

---

## 6. Provider-Side Deduplication Limits (U1)

To prevent duplicate document ingestion into NotebookLM (PRD §28), the adapter applies a two-stage deduplication check:

1. **Local Persisted Ref (Primary)**: If `SourceRecord.corpus_ref["notebooklm"]` already contains a provider source ID, ingestion is skipped and returns `reused=True`.
2. **Provider Reconciliation (Best-Effort Fallback)**:
   - For **URLs**: Checks provider's source list against normalized URL.
   - For **Files/Text**: Checks provider's source list against exact document `title`. Because the provider does not expose cryptographic content hashes, title matching is best-effort.

---

## 7. Executed Manual Live-Verification Procedure (§6)

Manual verification against live NotebookLM was executed on 2026-09-16.

### Procedure

1. **Authentication Check**: Verify storage state using `notebooklm --profile research-harness auth check --test --json`.
2. **Init & Ingestion**: Initialize run with `--corpus notebooklm`, admit Tier-A paper (arXiv) and Tier-B technical analysis, and ingest both into NotebookLM.
3. **Deduplication Check**: Re-ingest both sources; confirm adapter returns `reused=True` with no duplicate provider sources created.
4. **Citation Retrieval**: Ask a grounded technical query via `corpus ask`; verify citations map back to canonical source IDs with verbatim excerpts and character-offset locators.
5. **Ledger Integration**: Add a claim citing the live citation locator and excerpt; verify retrieval via `ledger query --source <id>`.
6. **Unavailable Probe**: Run `corpus status` and `corpus ask` against an unauthenticated profile; verify exit code 3 with clean one-line messages and zero tracebacks.

### Recorded Live Verification Outcome

> The NotebookLM integration was verified live on 2026-09-16 against a dedicated Google account (profile `research-harness`, authenticated via `notebooklm login`; credentials live only under `~/.notebooklm/…`, never in the repo). Two public sources — an arXiv paper (Tier A) and a public technical explainer (Tier B) — were admitted and ingested; the adapter created and persisted a notebook id in `source-index.json`, returned a provider ref per source, and on re-ingest returned `reused=True` with no new provider source (dedup via persisted `corpus_ref`). A grounded question returned citations that mapped back to the canonical source ids with verbatim excerpts and character-range locators; a claim recorded from one of those live citations was retrievable via `ledger query --source <id>`. Pointing the CLI at an empty auth profile produced `corpus status → available=False` and a one-line `CorpusUnavailable` on `corpus ask`, both exit code 3, with no traceback. The live-check run directory was kept outside the committed tree.

---

## 8. Milestone 2 & 3 Non-Goals (Scope Boundaries)

The `research_corpus` layer implements the persistence, admission, and grounding foundation (Milestone 1). The following capabilities belong to later milestones (PLAN §10, TASK non-goals):

- **No Multi-Agent Orchestration**: Specialist researchers, scout decomposition, and researcher output contract merging belong to Milestone 2 (`research-engine`).
- **No Automated Deliberation**: Global Critic, Research Council, Judge, and final report synthesis belong to Milestone 3 (`deliberate-and-report`).
- **No Autonomous Synthesis**: `research-state.md` is initialized as a structural skeleton; automated synthesis is deferred to Milestone 2/3.
- **No Cross-Run Index**: Each research topic maintains an isolated ledger and SQLite index. Cross-topic aggregation is out of scope.
- **No Browser Automation**: Browser-based web scraping is not part of this layer; candidate URLs are fetched or provided directly.
- **Manual Exerciser Only**: The `research-corpus` CLI is an operational and integration tool, not the autonomous agent harness.
