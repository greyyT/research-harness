"""NotebookLM corpus adapter backed by notebooklm-py (PLAN §4.8, I3, I4, I11).

Synchronous adapter wrapping the asynchronous NotebookLMClient.
Not thread-safe; owns a private asyncio event loop and single client instance (I3).
Imports notebooklm lazily on first use / client construction (I4).
Provider-side dedup reconciliation (U1) is best-effort: URL sources reconcile by
normalized URL (I9); non-URL (file/text) sources reconcile by exact title match.
Rate limits (U3): passes rate_limit_max_retries=0 so HTTP 429 raises RateLimitError
immediately and maps to CorpusUnavailable.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import inspect
from pathlib import Path
from typing import Any, Callable, Generator, Sequence

from research_corpus.admission import AdmittedSource, SourceIndex, normalize_url
from research_corpus.artifacts import RunPaths
from research_corpus.corpus import (
    Citation,
    CorpusAnswer,
    CorpusIngestError,
    CorpusSourceRef,
    CorpusStatus,
    CorpusUnavailable,
)
from research_corpus.records import SourceRecord


def _map_exception(exc: Exception) -> CorpusUnavailable | CorpusIngestError | None:
    """Map provider / runtime exceptions to Corpus error taxonomy (PLAN §4.8, I11).

    Order matters:
    1. CorpusUnavailable / CorpusIngestError are preserved.
    2. ImportError (notebooklm extra missing) -> CorpusUnavailable.
    3. SourceProcessingError / SourceTimeoutError -> CorpusIngestError.
       These descend from SourceError -> NotebookLMError (and SourceTimeoutError is also an
       OSError/TimeoutError), so they MUST be caught before the catch-all branches.
    4. RPCError (AuthError, RateLimitError, ServerError, NotebookNotFoundError),
       NetworkError, any other NotebookLMError, and OSError (including FileNotFoundError) -> CorpusUnavailable.
    """
    if isinstance(exc, (CorpusUnavailable, CorpusIngestError)):
        return exc
    if isinstance(exc, ImportError):
        err = CorpusUnavailable("install research-corpus[notebooklm]")
        err.__cause__ = exc
        return err

    try:
        import notebooklm
    except ImportError:
        if isinstance(exc, OSError):
            err = CorpusUnavailable(str(exc))
            err.__cause__ = exc
            return err
        return None

    if isinstance(exc, (notebooklm.SourceProcessingError, notebooklm.SourceTimeoutError)):
        err = CorpusIngestError(str(exc))
        err.__cause__ = exc
        return err
    if isinstance(exc, (notebooklm.NotebookLMError, OSError)):
        err = CorpusUnavailable(str(exc))
        err.__cause__ = exc
        return err

    return None


@contextmanager
def _map_errors() -> Generator[None, None, None]:
    """Context manager wrapping provider operations to raise mapped Corpus errors."""
    try:
        yield
    except (CorpusUnavailable, CorpusIngestError):
        raise
    except Exception as exc:
        mapped = _map_exception(exc)
        if mapped is not None:
            raise mapped from exc
        raise


def _find_matching_remote_source(
    rec: SourceRecord,
    remote_sources: Sequence[Any],
) -> str | None:
    """Reconcile source against remote provider sources (U1, I9).

    Best-effort:
    - URL sources match by normalized URL (I9).
    - File/text sources match by exact title (U1).
    """
    if rec.url:
        norm_target = normalize_url(rec.url)
        for s in remote_sources:
            s_url = getattr(s, "url", None)
            if s_url and normalize_url(s_url) == norm_target:
                return getattr(s, "id", None) or str(s)
    elif rec.title:
        for s in remote_sources:
            s_title = getattr(s, "title", None)
            if s_title and s_title == rec.title:
                return getattr(s, "id", None) or str(s)
    return None


def _default_client_factory(
    storage_path: str | Path | None = None,
    *,
    profile: str | None = None,
    rate_limit_max_retries: int = 0,
) -> Any:
    """Default client factory building NotebookLMClient.from_storage (PLAN §4.8, U3)."""
    try:
        import notebooklm
    except ImportError as exc:
        raise CorpusUnavailable("install research-corpus[notebooklm]") from exc

    return notebooklm.NotebookLMClient.from_storage(
        storage_path,
        profile=profile,
        rate_limit_max_retries=rate_limit_max_retries,
    )


class NotebookLMCorpus:
    """NotebookLM corpus adapter backed by notebooklm-py (PLAN §4.8).

    Satisfies the Corpus protocol synchronously over an async client.
    Lazy-imports notebooklm on first use.
    """

    provider: str = "notebooklm"

    def __init__(
        self,
        paths: RunPaths,
        *,
        profile: str | None = None,
        storage_path: str | Path | None = None,
        notebook_id: str | None = None,
        wait_timeout: float = 180.0,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not isinstance(paths, RunPaths):
            raise TypeError(f"Expected RunPaths, got {type(paths).__name__}")
        self._paths = paths
        self._profile = profile
        self._storage_path = storage_path
        self._notebook_id = notebook_id
        self._wait_timeout = float(wait_timeout)
        self._client_factory = client_factory
        self._loop = asyncio.new_event_loop()
        self._client: Any = None
        self._client_cm: Any = None
        self._closed = False

    def _run(self, coro: Any) -> Any:
        if self._closed:
            if hasattr(coro, "close") and callable(coro.close):
                coro.close()
            raise CorpusUnavailable("NotebookLMCorpus is closed")
        return self._loop.run_until_complete(coro)

    async def _async_get_client(self) -> Any:
        if self._client is not None:
            return self._client

        factory = self._client_factory or _default_client_factory
        try:
            raw = factory(
                self._storage_path,
                profile=self._profile,
                rate_limit_max_retries=0,
            )

            if hasattr(raw, "__aenter__"):
                self._client_cm = raw
                self._client = await raw.__aenter__()
            else:
                self._client_cm = None
                self._client = raw

            return self._client
        except Exception as exc:
            mapped = _map_exception(exc)
            if mapped is not None:
                raise mapped from exc
            # Any non-NotebookLMError exception surfacing from client establishment
            # (corrupt json, validation errors, OSError, TypeError, etc.)
            # maps to CorpusUnavailable with __cause__ preserved.
            raise CorpusUnavailable(str(exc)) from exc

    def _resolve_notebook_id(self) -> str | None:
        if self._notebook_id:
            return self._notebook_id
        index = SourceIndex(self._paths)
        corpus_data = index.read_corpus()
        nb_id = corpus_data.get("notebooklm", {}).get("notebook_id")
        if nb_id:
            self._notebook_id = nb_id
            return nb_id
        return None

    async def _async_get_or_create_notebook_id(self, client: Any) -> str:
        nb_id = self._resolve_notebook_id()
        if nb_id:
            return nb_id

        # First ingest creates notebook and persists id via SourceIndex.update_corpus
        nb = await client.notebooks.create(f"research-harness/{self._paths.slug}")
        new_id = getattr(nb, "id", None) or str(nb)
        self._notebook_id = new_id

        index = SourceIndex(self._paths)
        corpus_data = index.read_corpus()
        nlm_data = corpus_data.setdefault("notebooklm", {})
        nlm_data["notebook_id"] = new_id
        index.update_corpus(corpus_data)
        return new_id

    def status(self) -> CorpusStatus:
        """Probe provider availability without raising (PLAN §4.6, §4.8, I11)."""
        try:
            self._run(self._async_status())
            return CorpusStatus(
                available=True,
                provider=self.provider,
                detail="NotebookLM ready",
            )
        except Exception as exc:
            mapped = _map_exception(exc)
            detail = str(mapped) if mapped is not None else str(exc)
            return CorpusStatus(
                available=False,
                provider=self.provider,
                detail=detail,
            )

    async def _async_status(self) -> None:
        client = await self._async_get_client()
        await client.notebooks.list()

    def has_source(self, source_id: str) -> bool:
        """Check if source is present in corpus, reconciling if needed (PLAN §4.8, I11)."""
        if not isinstance(source_id, str):
            raise TypeError(f"source_id must be a string, got {type(source_id).__name__}")
        with _map_errors():
            return self._run(self._async_has_source(source_id))

    async def _async_has_source(self, source_id: str) -> bool:
        client = await self._async_get_client()
        index = SourceIndex(self._paths)
        rec = index.get(source_id)

        if rec is not None and rec.corpus_ref.get(self.provider):
            # Local ref present: probe provider to verify availability (I11)
            await client.notebooks.list()
            return True

        nb_id = self._resolve_notebook_id()
        if nb_id is None:
            # No notebook created yet: probe provider to verify availability (I11)
            await client.notebooks.list()
            return False

        remote_sources = await client.sources.list(nb_id)
        if rec is not None:
            matched_id = _find_matching_remote_source(rec, remote_sources)
            if matched_id is not None:
                new_corpus_ref = dict(rec.corpus_ref)
                new_corpus_ref[self.provider] = matched_id
                updated_rec = replace(rec, corpus_ref=new_corpus_ref)
                index.write_record(updated_rec)
                return True

        return False

    def ingest(
        self,
        source: AdmittedSource,
        *,
        content: str | Path | None = None,
    ) -> CorpusSourceRef:
        """Ingest an admitted source into NotebookLM (PLAN §4.8, §28)."""
        if not isinstance(source, AdmittedSource):
            raise TypeError(f"ingest requires AdmittedSource, got {type(source).__name__}")
        with _map_errors():
            return self._run(self._async_ingest(source, content=content))

    async def _async_ingest(
        self,
        source: AdmittedSource,
        *,
        content: str | Path | None = None,
    ) -> CorpusSourceRef:
        source_id = source.id
        index = SourceIndex(self._paths)
        rec = index.get(source_id) or source.record

        # 1. Local dedup check: no provider write if already referenced
        local_ref = rec.corpus_ref.get(self.provider)
        if local_ref:
            return CorpusSourceRef(
                source_id=source_id,
                provider=self.provider,
                provider_ref=local_ref,
                reused=True,
            )

        client = await self._async_get_client()
        nb_id = await self._async_get_or_create_notebook_id(client)

        # 2. Provider-side reconciliation against sources.list(nb)
        remote_sources = await client.sources.list(nb_id)
        matched_id = _find_matching_remote_source(rec, remote_sources)
        if matched_id is not None:
            new_corpus_ref = dict(rec.corpus_ref)
            new_corpus_ref[self.provider] = matched_id
            updated_rec = replace(rec, corpus_ref=new_corpus_ref)
            index.write_record(updated_rec)
            return CorpusSourceRef(
                source_id=source_id,
                provider=self.provider,
                provider_ref=matched_id,
                reused=True,
            )

        # 3. Provider write
        if source.url is not None:
            provider_source = await client.sources.add_url(
                nb_id,
                source.url,
                wait=True,
                wait_timeout=self._wait_timeout,
            )
        elif source.path is not None:
            provider_source = await client.sources.add_file(
                nb_id,
                source.path,
                wait=True,
                title=source.title,
            )
        elif isinstance(content, str):
            provider_source = await client.sources.add_text(
                nb_id,
                source.title,
                content,
                wait=True,
            )
        elif isinstance(content, Path):
            provider_source = await client.sources.add_file(
                nb_id,
                content,
                wait=True,
                title=source.title,
            )
        else:
            raise ValueError(
                f"Content required for source '{source_id}' without url or path"
            )

        provider_ref = getattr(provider_source, "id", None) or str(provider_source)
        new_corpus_ref = dict(rec.corpus_ref)
        new_corpus_ref[self.provider] = provider_ref
        updated_rec = replace(rec, corpus_ref=new_corpus_ref)
        index.write_record(updated_rec)

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
        """Query the corpus, returning answer with grounded citations (PLAN §4.8)."""
        if not isinstance(question, str):
            raise TypeError(f"question must be a str, got {type(question).__name__}")
        with _map_errors():
            return self._run(self._async_query(question, source_ids=source_ids))

    async def _async_query(
        self,
        question: str,
        *,
        source_ids: Sequence[str] | None = None,
    ) -> CorpusAnswer:
        client = await self._async_get_client()
        nb_id = self._resolve_notebook_id()
        if nb_id is None:
            raise CorpusUnavailable("No notebook bound to corpus; ingest sources first")

        index = SourceIndex(self._paths)
        provider_source_ids: list[str] | None = None
        if source_ids is not None:
            unknown: list[str] = []
            pids: list[str] = []
            for sid in source_ids:
                rec = index.get(sid)
                pref = rec.corpus_ref.get(self.provider) if rec is not None else None
                if pref is not None:
                    pids.append(pref)
                else:
                    unknown.append(sid)
            if unknown:
                raise ValueError(f"Source ID(s) not ingested in corpus: {unknown}")
            provider_source_ids = pids

        result = await client.chat.ask(
            nb_id,
            question,
            source_ids=provider_source_ids,
        )

        # Build inverse map from provider_ref -> local source_id
        records = index.load()
        inverse_map: dict[str, str] = {}
        for r in records:
            pref = r.corpus_ref.get(self.provider)
            if pref:
                inverse_map[pref] = r.id

        citations: list[Citation] = []
        raw_refs = getattr(result, "references", None) or []
        for ref in raw_refs:
            provider_ref = getattr(ref, "source_id", None)
            our_source_id = inverse_map.get(provider_ref) if provider_ref else None
            cited_text = getattr(ref, "cited_text", None) or ""

            start_char = getattr(ref, "start_char", None)
            end_char = getattr(ref, "end_char", None)
            citation_number = getattr(ref, "citation_number", None)

            if start_char is not None and end_char is not None:
                locator = f"chars {start_char}-{end_char}"
            elif citation_number is not None:
                locator = f"citation {citation_number}"
            else:
                locator = ""

            citations.append(
                Citation(
                    source_id=our_source_id,
                    excerpt=cited_text,
                    locator=locator,
                    provider_ref=provider_ref,
                )
            )

        answer_text = getattr(result, "answer", "")
        return CorpusAnswer(
            answer=answer_text,
            citations=citations,
            provider=self.provider,
        )

    def close(self) -> None:
        """Exit client context and close the private event loop."""
        if self._closed:
            return
        self._closed = True
        try:
            if self._client_cm is not None:
                self._loop.run_until_complete(
                    self._client_cm.__aexit__(None, None, None)
                )
            elif self._client is not None:
                if hasattr(self._client, "aclose"):
                    self._loop.run_until_complete(self._client.aclose())
                elif hasattr(self._client, "close"):
                    res = self._client.close()
                    if inspect.isawaitable(res):
                        self._loop.run_until_complete(res)
        except Exception:
            pass
        finally:
            self._loop.close()

    def __enter__(self) -> NotebookLMCorpus:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


__all__ = ["NotebookLMCorpus"]
