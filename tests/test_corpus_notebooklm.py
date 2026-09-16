"""Tests for NotebookLMCorpus adapter (PLAN §4.8, §5 S6)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
import pytest

from notebooklm import (
    AuthError,
    NetworkError,
    NotebookNotFoundError,
    RateLimitError,
    RPCError,
    SourceProcessingError,
    SourceTimeoutError,
)

from research_corpus.admission import (
    AdmittedSource,
    SourceCandidate,
    SourceIndex,
    admit,
)
from research_corpus.artifacts import RunPaths, init_run, load_source_index
from research_corpus.corpus import (
    Corpus,
    CorpusAnswer,
    CorpusIngestError,
    CorpusSourceRef,
    CorpusStatus,
    CorpusUnavailable,
)
from research_corpus.corpus.notebooklm import NotebookLMCorpus
from research_corpus.records import SourceKind


# ---------------------------------------------------------------------------
# Test Fakes & Helpers
# ---------------------------------------------------------------------------


class FakeNotebooksAPI:
    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []
        self.listed = False
        self.list_count = 0
        self.raise_on_list: Exception | None = None
        self.raise_on_create: Exception | None = None

    async def list(self) -> list[Any]:
        self.list_count += 1
        if self.raise_on_list:
            raise self.raise_on_list
        self.listed = True
        return [SimpleNamespace(id="fake-nb-id", title="Fake NB")]

    async def create(self, title: str) -> Any:
        if self.raise_on_create:
            raise self.raise_on_create
        nb_id = f"nb-created-{len(self.created) + 1}"
        self.created.append((title, nb_id))
        return SimpleNamespace(id=nb_id, title=title)


class FakeSourcesAPI:
    def __init__(self) -> None:
        self.sources: list[Any] = []
        self.added_urls: list[dict[str, Any]] = []
        self.added_files: list[dict[str, Any]] = []
        self.added_texts: list[dict[str, Any]] = []
        self.list_count = 0
        self.raise_on_list: Exception | None = None
        self.raise_on_add_url: Exception | None = None
        self.raise_on_add_file: Exception | None = None
        self.raise_on_add_text: Exception | None = None

    async def list(self, notebook_id: str, **kwargs: Any) -> list[Any]:
        self.list_count += 1
        if self.raise_on_list:
            raise self.raise_on_list
        return list(self.sources)

    async def add_url(
        self,
        notebook_id: str,
        url: str,
        *,
        wait: bool = True,
        wait_timeout: float = 120.0,
        **kwargs: Any,
    ) -> Any:
        if self.raise_on_add_url:
            raise self.raise_on_add_url
        sid = f"prov-url-{len(self.added_urls) + 1}"
        s = SimpleNamespace(id=sid, title=url, url=url, status="ready")
        self.added_urls.append(
            {"nb_id": notebook_id, "url": url, "wait": wait, "wait_timeout": wait_timeout}
        )
        self.sources.append(s)
        return s

    async def add_file(
        self,
        notebook_id: str,
        file_path: Any,
        *,
        wait: bool = True,
        title: str | None = None,
        **kwargs: Any,
    ) -> Any:
        if self.raise_on_add_file:
            raise self.raise_on_add_file
        sid = f"prov-file-{len(self.added_files) + 1}"
        s = SimpleNamespace(id=sid, title=title, url=None, status="ready")
        self.added_files.append(
            {"nb_id": notebook_id, "file_path": file_path, "wait": wait, "title": title}
        )
        self.sources.append(s)
        return s

    async def add_text(
        self,
        notebook_id: str,
        title: str,
        content: str,
        *,
        wait: bool = True,
        **kwargs: Any,
    ) -> Any:
        if self.raise_on_add_text:
            raise self.raise_on_add_text
        sid = f"prov-text-{len(self.added_texts) + 1}"
        s = SimpleNamespace(id=sid, title=title, url=None, status="ready")
        self.added_texts.append(
            {"nb_id": notebook_id, "title": title, "content": content, "wait": wait}
        )
        self.sources.append(s)
        return s


class FakeChatAPI:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.canned_result: Any = None
        self.raise_on_ask: Exception | None = None

    async def ask(
        self,
        notebook_id: str,
        question: str,
        *,
        source_ids: list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        if self.raise_on_ask:
            raise self.raise_on_ask
        self.calls.append(
            {"nb_id": notebook_id, "question": question, "source_ids": source_ids}
        )
        if self.canned_result is not None:
            return self.canned_result
        return SimpleNamespace(answer="Default answer", references=[])


class FakeClient:
    def __init__(self) -> None:
        self.notebooks = FakeNotebooksAPI()
        self.sources = FakeSourcesAPI()
        self.chat = FakeChatAPI()
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.closed = True


def _admit_url_source(paths: RunPaths, url: str, title: str) -> AdmittedSource:
    index = SourceIndex(paths)
    candidate = SourceCandidate(url=url, title=title)
    return admit(index, candidate, SourceKind.paper, declared_by="researcher", reason="test")


def _admit_file_source(paths: RunPaths, file_path: Path, title: str) -> AdmittedSource:
    index = SourceIndex(paths)
    candidate = SourceCandidate(path=str(file_path), title=title)
    return admit(
        index,
        candidate,
        SourceKind.engineering_report,
        declared_by="researcher",
        reason="test",
    )


def _admit_text_source(paths: RunPaths, text: str, title: str) -> AdmittedSource:
    index = SourceIndex(paths)
    candidate = SourceCandidate(text=text, title=title)
    return admit(
        index,
        candidate,
        SourceKind.informal_explanation,
        declared_by="researcher",
        reason="test",
    )


# ---------------------------------------------------------------------------
# Protocol Satisfaction & Basics
# ---------------------------------------------------------------------------


def test_notebooklm_satisfies_corpus_protocol(tmp_path: Path):
    """Verify NotebookLMCorpus satisfies Corpus runtime-checkable protocol."""
    paths = init_run(RunPaths(tmp_path, "run-proto"))
    fake = FakeClient()
    with NotebookLMCorpus(paths, client_factory=lambda *args, **kwargs: fake) as corpus:
        assert isinstance(corpus, Corpus)
        assert corpus.provider == "notebooklm"


# ---------------------------------------------------------------------------
# Notebook Binding & Persistence (§5 S6)
# ---------------------------------------------------------------------------


def test_notebook_id_creation_persistence_and_reuse(tmp_path: Path):
    """Verify notebook id is created on first ingest, persisted, and reused across instances."""
    paths = init_run(RunPaths(tmp_path, "run-binding"))
    fake = FakeClient()
    source1 = _admit_url_source(paths, "https://example.com/one", "Source One")
    source2 = _admit_url_source(paths, "https://example.com/two", "Source Two")

    # First instance: no notebook_id provided or persisted yet
    with NotebookLMCorpus(paths, client_factory=lambda *args, **kwargs: fake) as c1:
        ref1 = c1.ingest(source1)
        assert ref1.reused is False
        assert c1._notebook_id == "nb-created-1"

    # Notebook creation recorded
    assert fake.notebooks.created == [("research-harness/run-binding", "nb-created-1")]

    # Check persistence into source-index.json corpus object
    index = SourceIndex(paths)
    corpus_meta = index.read_corpus()
    assert corpus_meta["notebooklm"]["notebook_id"] == "nb-created-1"

    # Second instance: should read notebook_id from source-index.json without creating a new one
    with NotebookLMCorpus(paths, client_factory=lambda *args, **kwargs: fake) as c2:
        assert c2._resolve_notebook_id() == "nb-created-1"
        ref2 = c2.ingest(source2)
        assert ref2.reused is False

    # Still only one notebook created across both runs
    assert len(fake.notebooks.created) == 1


def test_explicit_notebook_id_arg_takes_precedence(tmp_path: Path):
    """Verify explicit notebook_id argument is honored over index."""
    paths = init_run(RunPaths(tmp_path, "run-explicit-nb"))
    fake = FakeClient()
    source = _admit_url_source(paths, "https://example.com/test", "Test")

    with NotebookLMCorpus(
        paths, notebook_id="explicit-nb-99", client_factory=lambda *args, **kwargs: fake
    ) as corpus:
        corpus.ingest(source)

    assert len(fake.notebooks.created) == 0
    assert fake.sources.added_urls[0]["nb_id"] == "explicit-nb-99"


# ---------------------------------------------------------------------------
# Deduplication: Local & Provider-Side Reconciliation (§5 S6, U1, I9)
# ---------------------------------------------------------------------------


def test_dedup_via_existing_local_corpus_ref(tmp_path: Path):
    """Verify local corpus_ref presence returns reused=True with no provider write."""
    paths = init_run(RunPaths(tmp_path, "run-local-dedup"))
    fake = FakeClient()
    source = _admit_url_source(paths, "https://example.com/paper", "Paper")

    with NotebookLMCorpus(paths, client_factory=lambda *args, **kwargs: fake) as corpus:
        # First ingest writes to provider
        ref1 = corpus.ingest(source)
        assert ref1.reused is False
        assert len(fake.sources.added_urls) == 1

        # Second ingest uses local corpus_ref, no provider write
        ref2 = corpus.ingest(source)
        assert ref2.reused is True
        assert ref2.provider_ref == ref1.provider_ref
        assert len(fake.sources.added_urls) == 1  # No additional write!


def test_dedup_via_sources_list_normalized_url_reconciliation(tmp_path: Path):
    """Verify provider-side reconciliation matches by normalized URL (I9)."""
    paths = init_run(RunPaths(tmp_path, "run-url-reconcile"))
    fake = FakeClient()

    # Pre-populate provider with an existing source having slightly different formatting
    existing_remote_source = SimpleNamespace(
        id="prov-existing-url-123",
        title="Remote Paper",
        url="https://EXAMPLE.com/docs/paper?b=2&a=1",
        status="ready",
    )
    fake.sources.sources.append(existing_remote_source)

    # Local source URL has tracking query params and uppercase host
    source = _admit_url_source(
        paths,
        "https://example.com/docs/paper/?b=2&a=1&utm_source=twitter&ref=abc",
        "Local Paper",
    )
    # Ensure local record does NOT have corpus_ref
    index = SourceIndex(paths)
    rec = index.get(source.id)
    assert rec is not None
    assert "notebooklm" not in rec.corpus_ref

    with NotebookLMCorpus(
        paths, notebook_id="nb-fixed", client_factory=lambda *args, **kwargs: fake
    ) as corpus:
        ref = corpus.ingest(source)
        assert ref.reused is True
        assert ref.provider_ref == "prov-existing-url-123"

        # Verify no write call was made
        assert len(fake.sources.added_urls) == 0

        # Verify adopted provider id was persisted to source-index.json
        updated_rec = index.get(source.id)
        assert updated_rec is not None
        assert updated_rec.corpus_ref["notebooklm"] == "prov-existing-url-123"


def test_dedup_via_exact_title_reconciliation_for_file_and_text(tmp_path: Path):
    """Verify provider-side reconciliation matches file/text sources by exact title (U1)."""
    paths = init_run(RunPaths(tmp_path, "run-title-reconcile"))
    fake = FakeClient()

    title = "System Architecture Report 2026"
    existing_remote_source = SimpleNamespace(
        id="prov-existing-title-456",
        title=title,
        url=None,
        status="ready",
    )
    fake.sources.sources.append(existing_remote_source)

    # Local text source with matching title
    source = _admit_text_source(paths, "Some architecture text", title)

    with NotebookLMCorpus(
        paths, notebook_id="nb-fixed", client_factory=lambda *args, **kwargs: fake
    ) as corpus:
        ref = corpus.ingest(source, content="Some architecture text")
        assert ref.reused is True
        assert ref.provider_ref == "prov-existing-title-456"

        # No text was written to provider
        assert len(fake.sources.added_texts) == 0

        # Persisted in source-index.json
        rec = SourceIndex(paths).get(source.id)
        assert rec is not None
        assert rec.corpus_ref["notebooklm"] == "prov-existing-title-456"


# ---------------------------------------------------------------------------
# Ingest Formats (File, String, Path)
# ---------------------------------------------------------------------------


def test_ingest_local_file_source(tmp_path: Path):
    """Verify ingest of local file candidate calls add_file with source.path."""
    paths = init_run(RunPaths(tmp_path, "run-file-ingest"))
    fake = FakeClient()

    local_file = tmp_path / "paper.pdf"
    local_file.write_text("dummy binary content", encoding="utf-8")
    source = _admit_file_source(paths, local_file, "Local PDF")

    with NotebookLMCorpus(paths, client_factory=lambda *args, **kwargs: fake) as corpus:
        ref = corpus.ingest(source)
        assert ref.reused is False
        assert len(fake.sources.added_files) == 1
        call = fake.sources.added_files[0]
        assert call["file_path"] == str(local_file)
        assert call["title"] == "Local PDF"


def test_ingest_with_path_object_content(tmp_path: Path):
    """Verify ingest with content as a Path object calls add_file."""
    paths = init_run(RunPaths(tmp_path, "run-path-content"))
    fake = FakeClient()

    source = _admit_text_source(paths, "Placeholder text", "External File Source")
    ext_file = tmp_path / "ext_doc.txt"
    ext_file.write_text("external content", encoding="utf-8")

    with NotebookLMCorpus(paths, client_factory=lambda *args, **kwargs: fake) as corpus:
        ref = corpus.ingest(source, content=ext_file)
        assert ref.reused is False
        assert len(fake.sources.added_files) == 1
        assert fake.sources.added_files[0]["file_path"] == ext_file


def test_ingest_non_admitted_source_raises_type_error(tmp_path: Path):
    """Verify non-AdmittedSource passed to ingest raises TypeError."""
    paths = init_run(RunPaths(tmp_path, "run-typecheck"))
    fake = FakeClient()
    with NotebookLMCorpus(paths, client_factory=lambda *args, **kwargs: fake) as corpus:
        with pytest.raises(TypeError, match="AdmittedSource"):
            corpus.ingest("not-a-source")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Query & Citation Mapping (§5 S6)
# ---------------------------------------------------------------------------


def test_chat_reference_to_citation_mapping(tmp_path: Path):
    """Verify ChatReference mapping to Citation including char ranges, citation numbers, and unknown provider ids."""
    paths = init_run(RunPaths(tmp_path, "run-chat-refs"))
    fake = FakeClient()

    s1 = _admit_url_source(paths, "https://example.com/one", "Source One")
    s2 = _admit_url_source(paths, "https://example.com/two", "Source Two")

    with NotebookLMCorpus(paths, client_factory=lambda *args, **kwargs: fake) as corpus:
        ref1 = corpus.ingest(s1)
        ref2 = corpus.ingest(s2)

        # Canned Chat references
        fake.chat.canned_result = SimpleNamespace(
            answer="Quantum computing is fast [1]. Classical is stable [2]. Unknown observation [3].",
            references=[
                # Ref 1: has char offsets
                SimpleNamespace(
                    source_id=ref1.provider_ref,
                    cited_text="Quantum computing is fast",
                    start_char=100,
                    end_char=124,
                    citation_number=1,
                ),
                # Ref 2: no char offsets, has citation_number
                SimpleNamespace(
                    source_id=ref2.provider_ref,
                    cited_text="Classical is stable",
                    start_char=None,
                    end_char=None,
                    citation_number=2,
                ),
                # Ref 3: unknown provider_ref -> source_id=None kept
                SimpleNamespace(
                    source_id="unregistered-prov-id-999",
                    cited_text="Unknown observation",
                    start_char=50,
                    end_char=69,
                    citation_number=3,
                ),
            ],
        )

        ans = corpus.query("Tell me about computing")
        assert isinstance(ans, CorpusAnswer)
        assert ans.provider == "notebooklm"
        assert ans.answer == fake.chat.canned_result.answer
        assert len(ans.citations) == 3

        # Citation 1 checks
        c1 = ans.citations[0]
        assert c1.source_id == s1.id
        assert c1.excerpt == "Quantum computing is fast"
        assert c1.locator == "chars 100-124"
        assert c1.provider_ref == ref1.provider_ref

        # Citation 2 checks
        c2 = ans.citations[1]
        assert c2.source_id == s2.id
        assert c2.excerpt == "Classical is stable"
        assert c2.locator == "citation 2"
        assert c2.provider_ref == ref2.provider_ref

        # Citation 3 checks: unknown provider id -> source_id is None, citation KEPT
        c3 = ans.citations[2]
        assert c3.source_id is None
        assert c3.excerpt == "Unknown observation"
        assert c3.locator == "chars 50-69"
        assert c3.provider_ref == "unregistered-prov-id-999"


def test_query_source_ids_scoping_and_validation(tmp_path: Path):
    """Verify scoped query resolves locally, issues zero extra list RPCs, and raises ValueError for unknown ids."""
    paths = init_run(RunPaths(tmp_path, "run-query-scope"))
    fake = FakeClient()

    s1 = _admit_url_source(paths, "https://example.com/one", "Source One")
    s2 = _admit_url_source(paths, "https://example.com/two", "Source Two")
    # s3 is admitted in index but NOT ingested into notebooklm
    s3 = _admit_url_source(paths, "https://example.com/three", "Source Three")

    with NotebookLMCorpus(paths, client_factory=lambda *args, **kwargs: fake) as corpus:
        ref1 = corpus.ingest(s1)
        ref2 = corpus.ingest(s2)

        # Baseline RPC counts before scoped query
        nb_list_count_before = fake.notebooks.list_count
        src_list_count_before = fake.sources.list_count

        # Scoped K-source query
        corpus.query("test question", source_ids=[s1.id, s2.id])
        assert len(fake.chat.calls) == 1
        assert fake.chat.calls[0]["source_ids"] == [ref1.provider_ref, ref2.provider_ref]

        # Verify NO extra notebooks.list() or sources.list() RPCs were issued
        assert fake.notebooks.list_count == nb_list_count_before
        assert fake.sources.list_count == src_list_count_before

        # Query with s3 (admitted in index but not ingested for notebooklm provider) raises ValueError
        with pytest.raises(ValueError, match="Source ID\\(s\\) not ingested in corpus") as exc_info:
            corpus.query("test question", source_ids=[s1.id, s3.id])
        assert s3.id in str(exc_info.value)

        # Query with completely unadmitted source id raises ValueError
        with pytest.raises(ValueError, match="Source ID\\(s\\) not ingested in corpus") as exc_info2:
            corpus.query("test question", source_ids=[s1.id, "nonexistent-id-999"])
        assert "nonexistent-id-999" in str(exc_info2.value)

        # Still no extra list RPCs
        assert fake.notebooks.list_count == nb_list_count_before
        assert fake.sources.list_count == src_list_count_before


def test_query_before_any_ingest_raises_corpus_unavailable(tmp_path: Path):
    """Verify query raises CorpusUnavailable if no notebook is bound."""
    paths = init_run(RunPaths(tmp_path, "run-no-nb-query"))
    fake = FakeClient()
    with NotebookLMCorpus(paths, client_factory=lambda *args, **kwargs: fake) as corpus:
        with pytest.raises(CorpusUnavailable, match="No notebook bound"):
            corpus.query("test question")


# ---------------------------------------------------------------------------
# has_source Behavior & Reconciliation
# ---------------------------------------------------------------------------


def test_has_source_reporting_and_reconciliation(tmp_path: Path):
    """Verify has_source accurately checks local ref, reconciles remote source, and probes provider."""
    paths = init_run(RunPaths(tmp_path, "run-has-source"))
    fake = FakeClient()
    source = _admit_url_source(paths, "https://example.com/item", "Item")

    with NotebookLMCorpus(
        paths, notebook_id="nb-1", client_factory=lambda *args, **kwargs: fake
    ) as corpus:
        # Initially False
        assert corpus.has_source(source.id) is False

        # Ingested -> True
        corpus.ingest(source)
        assert corpus.has_source(source.id) is True

    # When provider is down, has_source raises CorpusUnavailable rather than returning False
    fake.notebooks.raise_on_list = NetworkError("network timeout")
    with NotebookLMCorpus(
        paths, notebook_id="nb-1", client_factory=lambda *args, **kwargs: fake
    ) as corpus_down:
        with pytest.raises(CorpusUnavailable, match="network timeout"):
            corpus_down.has_source(source.id)


# ---------------------------------------------------------------------------
# Error Mapping & Exceptions (§5 S6, I11)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_instance, expected_type",
    [
        (AuthError("auth expired"), CorpusUnavailable),
        (NetworkError("connection refused"), CorpusUnavailable),
        (RateLimitError("too many requests"), CorpusUnavailable),
        (RPCError("generic rpc error"), CorpusUnavailable),
        (NotebookNotFoundError("notebook missing"), CorpusUnavailable),
        (SourceProcessingError("src-1", message="processing failed"), CorpusIngestError),
        (SourceTimeoutError("src-1", 120.0), CorpusIngestError),
        (FileNotFoundError("auth storage state file not found"), CorpusUnavailable),
    ],
)
def test_exception_mapping_to_corpus_errors(
    tmp_path: Path, exc_instance: Exception, expected_type: type[Exception]
):
    """Verify each provider exception class maps to the exact Corpus error preserving __cause__."""
    paths = init_run(RunPaths(tmp_path, f"run-err-{exc_instance.__class__.__name__}"))
    fake = FakeClient()
    fake.sources.raise_on_add_url = exc_instance
    source = _admit_url_source(paths, "https://example.com/err", "Error Source")

    with NotebookLMCorpus(
        paths, notebook_id="nb-1", client_factory=lambda *args, **kwargs: fake
    ) as corpus:
        with pytest.raises(expected_type) as exc_info:
            corpus.ingest(source)
        # Verify __cause__ preserved
        assert exc_info.value.__cause__ is exc_instance


def test_import_error_mapped_to_corpus_unavailable(tmp_path: Path):
    """Verify client_factory raising ImportError maps to CorpusUnavailable with install advice."""
    paths = init_run(RunPaths(tmp_path, "run-import-error"))

    def missing_factory(*args: Any, **kwargs: Any) -> Any:
        raise ImportError("No module named 'notebooklm'")

    with NotebookLMCorpus(paths, client_factory=missing_factory) as corpus:
        # status() reports available=False without raising
        status = corpus.status()
        assert isinstance(status, CorpusStatus)
        assert status.available is False
        assert status.provider == "notebooklm"
        assert status.detail == "install research-corpus[notebooklm]"

        # raising methods raise CorpusUnavailable
        with pytest.raises(
            CorpusUnavailable, match="install research-corpus\\[notebooklm\\]"
        ) as exc_info:
            corpus.has_source("some-id")
        assert isinstance(exc_info.value.__cause__, ImportError)

        source = _admit_url_source(paths, "https://example.com/test", "Test")
        with pytest.raises(CorpusUnavailable, match="install research-corpus\\[notebooklm\\]"):
            corpus.ingest(source)

        with pytest.raises(CorpusUnavailable, match="install research-corpus\\[notebooklm\\]"):
            corpus.query("question")


def test_status_missing_auth_returns_false_without_raising(tmp_path: Path):
    """Verify missing auth in status() returns available=False without raising."""
    paths = init_run(RunPaths(tmp_path, "run-auth-status"))
    fake = FakeClient()
    fake.notebooks.raise_on_list = AuthError("Session expired or invalid credentials")

    with NotebookLMCorpus(paths, client_factory=lambda *args, **kwargs: fake) as corpus:
        status = corpus.status()
        assert isinstance(status, CorpusStatus)
        assert status.available is False
        assert status.provider == "notebooklm"
        assert "Session expired or invalid credentials" in status.detail


def test_status_success(tmp_path: Path):
    """Verify status() returns available=True when provider responds."""
    paths = init_run(RunPaths(tmp_path, "run-status-ok"))
    fake = FakeClient()
    with NotebookLMCorpus(paths, client_factory=lambda *args, **kwargs: fake) as corpus:
        status = corpus.status()
        assert isinstance(status, CorpusStatus)
        assert status.available is True
        assert status.provider == "notebooklm"
        assert "ready" in status.detail.lower()


def test_corrupt_storage_file_status_false_and_has_source_raises(tmp_path: Path):
    """Verify real default factory with corrupt storage file makes status() available=False and has_source raise CorpusUnavailable."""
    paths = init_run(RunPaths(tmp_path, "run-corrupt-storage"))
    corrupt_file = tmp_path / "corrupt_storage.json"
    corrupt_file.write_text("{bad json", encoding="utf-8")

    with NotebookLMCorpus(paths, storage_path=corrupt_file) as corpus:
        # status() probe must return available=False without raising
        status = corpus.status()
        assert isinstance(status, CorpusStatus)
        assert status.available is False
        assert status.provider == "notebooklm"

        # has_source must raise CorpusUnavailable with __cause__ preserved
        with pytest.raises(CorpusUnavailable) as exc_info:
            corpus.has_source("any-id")
        assert exc_info.value.__cause__ is not None


def test_incomplete_storage_file_status_false_and_has_source_raises(tmp_path: Path):
    """Verify real default factory with incomplete ({}) storage file makes status() available=False and has_source raise CorpusUnavailable."""
    paths = init_run(RunPaths(tmp_path, "run-incomplete-storage"))
    incomplete_file = tmp_path / "incomplete_storage.json"
    incomplete_file.write_text("{}", encoding="utf-8")

    with NotebookLMCorpus(paths, storage_path=incomplete_file) as corpus:
        # status() probe must return available=False without raising
        status = corpus.status()
        assert isinstance(status, CorpusStatus)
        assert status.available is False
        assert status.provider == "notebooklm"

        # has_source must raise CorpusUnavailable with __cause__ preserved
        with pytest.raises(CorpusUnavailable) as exc_info:
            corpus.has_source("any-id")
        assert exc_info.value.__cause__ is not None


def test_factory_type_error_maps_to_corpus_unavailable(tmp_path: Path):
    """Verify TypeError raised inside factory propagates to CorpusUnavailable without retrying."""
    paths = init_run(RunPaths(tmp_path, "run-factory-type-error"))
    call_count = 0

    def bad_factory(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        raise TypeError("upstream kwarg drift or invalid signature")

    with NotebookLMCorpus(paths, client_factory=bad_factory) as corpus:
        # status() probe reports available=False without raising
        status = corpus.status()
        assert status.available is False
        assert "upstream kwarg drift" in status.detail

        # has_source raises CorpusUnavailable
        with pytest.raises(CorpusUnavailable, match="upstream kwarg drift") as exc_info:
            corpus.has_source("some-id")
        assert isinstance(exc_info.value.__cause__, TypeError)

    # Exactly one call made per operation, no retries with dropped args
    assert call_count == 2


# ---------------------------------------------------------------------------
# Rate Limit Flag & Kwarg Forwarding (§5 S6, U3)
# ---------------------------------------------------------------------------


def test_default_factory_invoked_with_rate_limit_max_retries_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify default client factory is invoked with rate_limit_max_retries=0 and forwards storage_path/profile."""
    paths = init_run(RunPaths(tmp_path, "run-rate-limit-spy"))
    captured_args: tuple[Any, ...] = ()
    captured_kwargs: dict[str, Any] = {}
    fake = FakeClient()

    def spy_from_storage(*args: Any, **kwargs: Any) -> Any:
        nonlocal captured_args
        captured_args = args
        captured_kwargs.update(kwargs)
        return fake

    monkeypatch.setattr("notebooklm.NotebookLMClient.from_storage", spy_from_storage)

    custom_storage = tmp_path / "custom_storage.json"
    custom_profile = "test-profile"
    with NotebookLMCorpus(paths, storage_path=custom_storage, profile=custom_profile) as corpus:
        status = corpus.status()
        assert status.available is True

    # Assert storage_path and profile forwarded unchanged without silent drop
    assert captured_args == (custom_storage,)
    assert captured_kwargs.get("profile") == custom_profile
    assert captured_kwargs.get("rate_limit_max_retries") == 0


def test_custom_factory_receives_rate_limit_max_retries_zero(tmp_path: Path):
    """Verify custom factory with kwargs receives rate_limit_max_retries=0 and args."""
    paths = init_run(RunPaths(tmp_path, "run-custom-spy"))
    captured: dict[str, Any] = {}
    fake = FakeClient()

    def spy_factory(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return fake

    with NotebookLMCorpus(paths, client_factory=spy_factory) as corpus:
        corpus.status()

    assert captured.get("rate_limit_max_retries") == 0


# ---------------------------------------------------------------------------
# Lifecycle & Close
# ---------------------------------------------------------------------------


def test_event_loop_and_close_lifecycle(tmp_path: Path):
    """Verify event loop and client close properly without leakage or unawaited coroutine warnings."""
    paths = init_run(RunPaths(tmp_path, "run-lifecycle"))
    fake = FakeClient()
    source = _admit_url_source(paths, "https://example.com/close-test", "Close Test")

    corpus = NotebookLMCorpus(paths, client_factory=lambda *args, **kwargs: fake)
    corpus.status()
    assert fake.closed is False
    assert corpus._closed is False

    corpus.close()
    assert fake.closed is True
    assert corpus._closed is True

    # Subsequent calls raise CorpusUnavailable and close coroutine without RuntimeWarning
    with pytest.raises(CorpusUnavailable, match="closed"):
        corpus.has_source("some-id")

    with pytest.raises(CorpusUnavailable, match="closed"):
        corpus.ingest(source)

    with pytest.raises(CorpusUnavailable, match="closed"):
        corpus.query("test question")

    # status() returns available=False without raising
    status = corpus.status()
    assert status.available is False
    assert "closed" in status.detail

    # Idempotent close
    corpus.close()
