"""Source admission, classification tier mapping, and source index."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import urllib.parse

from research_corpus.artifacts import RunPaths, load_source_index
from research_corpus.records import SourceKind, SourceRecord, SourceTier, to_json

# Tier mapping covering all SourceKind members per PRD §9 and PLAN §4.5
TIER_MAP: dict[SourceKind, SourceTier] = {
    # Tier A
    SourceKind.paper: SourceTier.A,
    SourceKind.official_benchmark: SourceTier.A,
    SourceKind.official_repository: SourceTier.A,
    SourceKind.official_specification: SourceTier.A,
    SourceKind.official_documentation: SourceTier.A,
    SourceKind.original_dataset: SourceTier.A,
    # Tier B
    SourceKind.engineering_report: SourceTier.B,
    SourceKind.independent_reproduction: SourceTier.B,
    SourceKind.technical_analysis: SourceTier.B,
    SourceKind.conference_presentation: SourceTier.B,
    # Tier C
    SourceKind.blog_post: SourceTier.C,
    SourceKind.discussion: SourceTier.C,
    SourceKind.forum_thread: SourceTier.C,
    SourceKind.reddit: SourceTier.C,
    SourceKind.informal_explanation: SourceTier.C,
}

assert set(TIER_MAP.keys()) == set(SourceKind), "TIER_MAP must cover every SourceKind member"


def slugify(text: str) -> str:
    """Derive a lowercased, non-alnum-collapsed-to-hyphen, trimmed token."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def normalize_url(url: str) -> str:
    """Normalize a URL deterministically (I9).

    - Lowercase scheme and host.
    - Drop default port (80 for http, 443 for https).
    - Drop fragment.
    - Drop query parameters named utm_* (case-insensitive), ref, fbclid.
    - Sort remaining query parameters.
    - Strip trailing slash except for the root path.
    """
    url = url.strip()
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    port = parsed.port
    is_default = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host
    if port is not None and not is_default:
        netloc = f"{host}:{port}"
    if parsed.username is not None:
        userinfo = parsed.username + (f":{parsed.password}" if parsed.password else "") + "@"
        netloc = f"{userinfo}{netloc}"
    elif not host and parsed.netloc:
        netloc = parsed.netloc.lower()

    path = parsed.path
    if path:
        stripped = path.rstrip("/")
        path = stripped if stripped else "/"
    elif netloc:
        path = "/"

    if parsed.query:
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        filtered = [
            (k, v)
            for k, v in pairs
            if not (k.lower().startswith("utm_") or k.lower() in ("ref", "fbclid"))
        ]
        filtered.sort(key=lambda kv: (kv[0], kv[1]))
        query = urllib.parse.urlencode(filtered)
    else:
        query = ""

    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


@dataclass(frozen=True)
class SourceCandidate:
    """An incoming source candidate for admission to the research corpus.

    Exactly one of `url`, `path`, or `text` identifies the source.
    `title` is optional and used for slug derivation and display.
    """

    url: str | None = None
    path: str | None = None
    text: str | None = None
    title: str = ""

    def __post_init__(self) -> None:
        if self.path is not None and isinstance(self.path, Path):
            object.__setattr__(self, "path", str(self.path))

        identifiers = [
            ("url", self.url),
            ("path", self.path),
            ("text", self.text),
        ]
        provided = [name for name, val in identifiers if val is not None]
        if len(provided) != 1:
            raise ValueError(
                f"SourceCandidate requires exactly one of url, path, or text; got {provided!r}"
            )
        name, val = [(n, v) for n, v in identifiers if v is not None][0]
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"SourceCandidate field '{name}' must be a non-empty string")
        if not isinstance(self.title, str):
            raise TypeError(f"SourceCandidate title must be a string, got {type(self.title)}")


_CONSTRUCTOR_GUARD = object()


@dataclass(frozen=True)
class AdmittedSource:
    """A typed handle wrapping an admitted SourceRecord.

    Only `admit()` may construct this class. Enforced via constructor guard.
    Exposes key fields of the underlying record as attributes/properties.
    """

    record: SourceRecord
    _guard: object = None

    def __post_init__(self) -> None:
        if self._guard is not _CONSTRUCTOR_GUARD:
            raise TypeError("AdmittedSource cannot be constructed directly; use admit()")
        if not isinstance(self.record, SourceRecord):
            raise TypeError(f"AdmittedSource requires a SourceRecord, got {type(self.record)}")
        if not self.record.admitted:
            raise ValueError(f"AdmittedSource record must have admitted=True, got {self.record.admitted}")

    @property
    def id(self) -> str:
        return self.record.id

    @property
    def url(self) -> str | None:
        return self.record.url

    @property
    def path(self) -> str | None:
        return self.record.path

    @property
    def title(self) -> str:
        return self.record.title

    @property
    def tier(self) -> SourceTier | None:
        return self.record.tier

    @property
    def kind(self) -> SourceKind:
        return self.record.kind


def derive_source_id(candidate: SourceCandidate) -> str:
    """Derive deterministic source id: <slug>-<8 hex of sha256(...)> (I9)."""
    if candidate.url is not None:
        norm_url = normalize_url(candidate.url)
        digest = hashlib.sha256(norm_url.encode("utf-8")).hexdigest()[:8]
        if candidate.title and candidate.title.strip():
            slug = slugify(candidate.title)
        else:
            host = urllib.parse.urlsplit(norm_url).hostname or ""
            slug = slugify(host)
        if not slug:
            slug = "source"
        return f"{slug}-{digest}"

    if candidate.path is not None:
        file_path = Path(candidate.path)
        if not file_path.is_file():
            raise FileNotFoundError(f"Local source file not found: {candidate.path}")
        file_bytes = file_path.read_bytes()
        digest = hashlib.sha256(file_bytes).hexdigest()[:8]
        if candidate.title and candidate.title.strip():
            slug = slugify(candidate.title)
        else:
            slug = slugify(file_path.stem)
        if not slug:
            slug = "file"
        return f"{slug}-{digest}"

    if candidate.text is not None:
        digest = hashlib.sha256(candidate.text.encode("utf-8")).hexdigest()[:8]
        if candidate.title and candidate.title.strip():
            slug = slugify(candidate.title)
        else:
            slug = "text"
        return f"{slug}-{digest}"

    raise ValueError("Invalid candidate: no identifier provided")


def _is_research_state(candidate: SourceCandidate) -> bool:
    """Check if candidate's URL or path basename is research-state.md."""
    if candidate.url:
        parsed_path = urllib.parse.urlsplit(candidate.url).path
        if Path(parsed_path).name.lower() == "research-state.md":
            return True
    if candidate.path:
        if Path(candidate.path).name.lower() == "research-state.md":
            return True
    return False


class SourceIndex:
    """Owns all writes to sources/source-index.json for a research run."""

    def __init__(self, paths: RunPaths) -> None:
        if not isinstance(paths, RunPaths):
            raise TypeError(f"Expected RunPaths, got {type(paths)}")
        self._paths = paths

    @property
    def paths(self) -> RunPaths:
        return self._paths

    def load(self) -> list[SourceRecord]:
        """Read existing records via artifacts.load_source_index."""
        if not self._paths.source_index.exists():
            return []
        return load_source_index(self._paths)

    def get(self, source_id: str) -> SourceRecord | None:
        """Find a SourceRecord by id, returning None if absent."""
        for rec in self.load():
            if rec.id == source_id:
                return rec
        return None

    def read_corpus(self) -> dict[str, Any]:
        """Read existing corpus object from source-index.json."""
        if not self._paths.source_index.exists():
            return {}
        try:
            content = self._paths.source_index.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict):
                corpus = data.get("corpus")
                if isinstance(corpus, dict):
                    return corpus
        except Exception:
            pass
        return {}

    def write_record(self, record: SourceRecord) -> None:
        """Add or update a SourceRecord in source-index.json, preserving corpus."""
        records = self.load()
        updated = False
        new_records: list[SourceRecord] = []
        for r in records:
            if r.id == record.id:
                new_records.append(record)
                updated = True
            else:
                new_records.append(r)
        if not updated:
            new_records.append(record)

        self._save(new_records, corpus=self.read_corpus())

    def update_corpus(self, corpus: dict[str, Any]) -> None:
        """Update the corpus object in source-index.json, preserving sources."""
        self._save(self.load(), corpus=corpus)

    def _save(self, records: list[SourceRecord], corpus: dict[str, Any] | None = None) -> None:
        if corpus is None:
            corpus = self.read_corpus()

        self._paths.sources.mkdir(parents=True, exist_ok=True)
        payload = {
            "sources": [json.loads(to_json(r)) for r in records],
            "corpus": corpus,
        }
        content = json.dumps(payload, indent=2) + "\n"
        self._paths.source_index.write_text(content, encoding="utf-8")


def admit(
    index: SourceIndex,
    candidate: SourceCandidate,
    kind: SourceKind | str,
    *,
    declared_by: str,
    reason: str = "",
) -> AdmittedSource:
    """Admit a source candidate into the research corpus (PLAN §4.5, I2, I9)."""
    if not isinstance(index, SourceIndex):
        raise TypeError(f"Expected SourceIndex, got {type(index)}")
    if not isinstance(candidate, SourceCandidate):
        raise TypeError(f"Expected SourceCandidate, got {type(candidate)}")
    if _is_research_state(candidate):
        raise ValueError("research-state.md is a synthetic summary and can never be admitted as a source")

    if isinstance(kind, str) and not isinstance(kind, SourceKind):
        kind = SourceKind(kind)
    elif not isinstance(kind, SourceKind):
        raise TypeError(f"Expected SourceKind, got {type(kind)}")

    if not isinstance(declared_by, str):
        raise TypeError(f"declared_by must be a string, got {type(declared_by)}")
    if not isinstance(reason, str):
        raise TypeError(f"reason must be a string, got {type(reason)}")

    tier = TIER_MAP[kind]
    source_id = derive_source_id(candidate)

    existing = index.get(source_id)
    if existing is not None and existing.admitted:
        return AdmittedSource(existing, _guard=_CONSTRUCTOR_GUARD)

    decided_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    norm_url = normalize_url(candidate.url) if candidate.url else None

    record = SourceRecord(
        id=source_id,
        kind=kind,
        tier=tier,
        url=norm_url,
        path=candidate.path,
        title=candidate.title,
        admitted=True,
        reason=reason,
        declared_by=declared_by,
        decided_at=decided_at,
        corpus_ref={},
    )
    index.write_record(record)
    return AdmittedSource(record, _guard=_CONSTRUCTOR_GUARD)


def reject(
    index: SourceIndex,
    candidate: SourceCandidate,
    *,
    declared_by: str = "",
    reason: str,
    kind: SourceKind | str = SourceKind.informal_explanation,
) -> SourceRecord:
    """Record rejection of a source candidate (PLAN §4.5)."""
    if not isinstance(index, SourceIndex):
        raise TypeError(f"Expected SourceIndex, got {type(index)}")
    if not isinstance(candidate, SourceCandidate):
        raise TypeError(f"Expected SourceCandidate, got {type(candidate)}")

    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Rejection reason is required and must be non-empty")
    if not isinstance(declared_by, str):
        raise TypeError(f"declared_by must be a string, got {type(declared_by)}")

    if isinstance(kind, str) and not isinstance(kind, SourceKind):
        kind = SourceKind(kind)
    elif not isinstance(kind, SourceKind):
        raise TypeError(f"Expected SourceKind, got {type(kind)}")

    source_id = derive_source_id(candidate)
    decided_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    norm_url = normalize_url(candidate.url) if candidate.url else None

    record = SourceRecord(
        id=source_id,
        kind=kind,
        tier=None,
        url=norm_url,
        path=candidate.path,
        title=candidate.title,
        admitted=False,
        reason=reason,
        declared_by=declared_by,
        decided_at=decided_at,
        corpus_ref={},
    )
    index.write_record(record)
    return record


def admitted_source_from_index(index: SourceIndex, source_id: str) -> AdmittedSource:
    """Rehydrate an AdmittedSource handle for an admitted source record from the index."""
    if not isinstance(index, SourceIndex):
        raise TypeError(f"Expected SourceIndex, got {type(index)}")
    if not isinstance(source_id, str):
        raise TypeError(f"source_id must be a string, got {type(source_id)}")

    record = index.get(source_id)
    if record is None:
        raise ValueError(f"Unknown source id: {source_id!r}")
    if not record.admitted:
        raise ValueError(f"Source {source_id!r} was rejected, cannot ingest")

    return AdmittedSource(record, _guard=_CONSTRUCTOR_GUARD)


__all__ = [
    "TIER_MAP",
    "AdmittedSource",
    "SourceCandidate",
    "SourceIndex",
    "admit",
    "admitted_source_from_index",
    "derive_source_id",
    "normalize_url",
    "reject",
    "slugify",
]
