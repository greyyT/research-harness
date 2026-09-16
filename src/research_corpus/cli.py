"""CLI entry point for research-corpus (PLAN §4.9)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Sequence
import urllib.parse

from research_corpus.admission import (
    SourceCandidate,
    SourceIndex,
    admit,
    admitted_source_from_index,
    reject,
)
from research_corpus.artifacts import RunPaths, init_run
from research_corpus.corpus import (
    CorpusIngestError,
    CorpusUnavailable,
)
from research_corpus.index import LedgerIndex
from research_corpus.ledger import (
    ClaimQuery,
    Ledger,
    LedgerCorruptError,
)
from research_corpus.records import (
    ClaimRecord,
    ClaimStatus,
    ClaimType,
    Confidence,
    ContradictionRecord,
    EvidenceRef,
    SourceKind,
    from_json,
    to_json,
)


def _make_candidate(target: str, title: str = "") -> SourceCandidate:
    """Build a SourceCandidate from target string.

    Treated as a URL if urlsplit reports a scheme in ('http', 'https', 'file', 'ftp'),
    otherwise treated as a local filesystem path.
    """
    target = target.strip()
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme.lower() in ("http", "https", "file", "ftp"):
        return SourceCandidate(url=target, title=title)
    return SourceCandidate(path=target, title=title)


def _build_corpus(args: argparse.Namespace, paths: RunPaths):
    """Build the configured Corpus instance, importing lazily."""
    if args.corpus == "stub":
        from research_corpus.corpus.stub import LocalStubCorpus

        return LocalStubCorpus(paths)
    elif args.corpus == "notebooklm":
        from research_corpus.corpus.notebooklm import NotebookLMCorpus

        return NotebookLMCorpus(
            paths,
            profile=args.profile,
            notebook_id=args.notebook_id,
        )
    raise ValueError(f"Unknown corpus provider: {args.corpus}")


def _handle_init(args: argparse.Namespace, paths: RunPaths) -> int:
    init_run(paths)
    print(f"Initialized run '{paths.slug}' at {paths.run_dir}")
    return 0


def _handle_source_admit(args: argparse.Namespace, paths: RunPaths) -> int:
    index = SourceIndex(paths)
    candidate = _make_candidate(args.target, title=args.title)
    admitted = admit(
        index,
        candidate,
        kind=SourceKind(args.kind),
        declared_by=args.by,
        reason=args.reason,
    )
    tier_str = admitted.tier.value if admitted.tier else "unknown"
    print(f"{admitted.id} ({tier_str})")
    return 0


def _handle_source_reject(args: argparse.Namespace, paths: RunPaths) -> int:
    index = SourceIndex(paths)
    candidate = _make_candidate(args.target, title="")
    rec = reject(
        index,
        candidate,
        declared_by=args.by,
        reason=args.reason,
    )
    print(f"{rec.id} (rejected)")
    return 0


def _handle_source_list(args: argparse.Namespace, paths: RunPaths) -> int:
    index = SourceIndex(paths)
    records = index.load()
    if args.json:
        payload = [json.loads(to_json(r)) for r in records]
        print(json.dumps(payload, indent=2))
    else:
        for r in records:
            tier_val = r.tier.value if r.tier else "-"
            status_val = "admitted" if r.admitted else "rejected"
            target_val = r.url or r.path or ""
            print(f"{r.id}\t{r.kind.value}\t{tier_val}\t{status_val}\t{target_val}")
    return 0


def _handle_corpus_status(args: argparse.Namespace, paths: RunPaths) -> int:
    corpus = _build_corpus(args, paths)
    try:
        st = corpus.status()
        print(f"provider: {st.provider}")
        print(f"available: {st.available}")
        if st.detail:
            print(f"detail: {st.detail}")
        if not st.available:
            return 3
        return 0
    finally:
        if hasattr(corpus, "close") and callable(corpus.close):
            corpus.close()


def _handle_corpus_ingest(args: argparse.Namespace, paths: RunPaths) -> int:
    content_text: str | None = None
    if args.content:
        content_path = Path(args.content)
        if not content_path.is_file():
            raise ValueError(f"Content file not found: {args.content}")
        content_text = content_path.read_text(encoding="utf-8")

    index = SourceIndex(paths)
    corpus = _build_corpus(args, paths)
    try:
        for sid in args.source_ids:
            source = admitted_source_from_index(index, sid)
            ref = corpus.ingest(source, content=content_text)
            print(f"{ref.source_id}: provider={ref.provider} ref={ref.provider_ref} reused={ref.reused}")
        return 0
    finally:
        if hasattr(corpus, "close") and callable(corpus.close):
            corpus.close()


def _handle_corpus_ask(args: argparse.Namespace, paths: RunPaths) -> int:
    corpus = _build_corpus(args, paths)
    try:
        ans = corpus.query(args.question, source_ids=args.source_ids)
        print(ans.answer)
        for cit in ans.citations:
            source_id_str = cit.source_id or "unknown"
            print(f"[{source_id_str}] ({cit.locator}): {cit.excerpt}")
        return 0
    finally:
        if hasattr(corpus, "close") and callable(corpus.close):
            corpus.close()


def _handle_claim_add(args: argparse.Namespace, paths: RunPaths) -> int:
    ledger = Ledger(paths)
    if args.json:
        json_path = Path(args.json)
        if not json_path.is_file():
            raise ValueError(f"Claim JSON file not found: {args.json}")
        data = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Claim JSON must be an object")
        if not data.get("created_at"):
            data["created_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        claim_rec = from_json(ClaimRecord, json.dumps(data))
    else:
        missing = [
            name
            for name, val in [
                ("--id", args.id),
                ("--claim", args.claim),
                ("--type", args.type),
                ("--confidence", args.confidence),
                ("--status", args.status),
                ("--topic", args.topic),
                ("--researcher", args.researcher),
            ]
            if not val
        ]
        if missing:
            raise ValueError(f"Missing required claim options: {', '.join(missing)}")

        evidence_refs: list[EvidenceRef] = []
        for ev_str in args.evidence:
            try:
                ev_data = json.loads(ev_str)
            except Exception as exc:
                raise ValueError(f"Invalid JSON in --evidence: {exc}") from exc
            evidence_refs.append(from_json(EvidenceRef, json.dumps(ev_data)))

        derived_sources = list(dict.fromkeys(e.source_id for e in evidence_refs))
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        claim_rec = ClaimRecord(
            id=args.id,
            claim=args.claim,
            type=ClaimType(args.type),
            confidence=Confidence(args.confidence),
            sources=derived_sources,
            caveats=list(args.caveat),
            conflicts=list(args.conflict),
            status=ClaimStatus(args.status),
            topic=args.topic,
            researcher=args.researcher,
            evidence=evidence_refs,
            created_at=created_at,
        )

    ledger.append_claim(claim_rec)
    print(f"Added claim {claim_rec.id}")
    return 0


def _handle_contradiction_add(args: argparse.Namespace, paths: RunPaths) -> int:
    ledger = Ledger(paths)
    if args.json:
        json_path = Path(args.json)
        if not json_path.is_file():
            raise ValueError(f"Contradiction JSON file not found: {args.json}")
        data = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Contradiction JSON must be an object")
        if not data.get("created_at"):
            data["created_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rec = from_json(ContradictionRecord, json.dumps(data))
    else:
        missing = [
            name
            for name, val in [
                ("--id", args.id),
                ("--description", args.description),
                ("--status", args.status),
            ]
            if not val
        ]
        if not args.claim_id:
            missing.append("--claim-id")
        if missing:
            raise ValueError(f"Missing required contradiction options: {', '.join(missing)}")

        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rec = ContradictionRecord(
            id=args.id,
            claim_ids=list(args.claim_id),
            description=args.description,
            status=ClaimStatus(args.status),
            created_at=created_at,
        )

    ledger.append_contradiction(rec)
    print(f"Added contradiction {rec.id}")
    return 0


def _handle_ledger_query(args: argparse.Namespace, paths: RunPaths) -> int:
    ledger = Ledger(paths)
    query = ClaimQuery(
        topic=args.topic,
        confidence=Confidence(args.confidence) if args.confidence else None,
        researcher=args.researcher,
        source_id=args.source,
        evidence_type=args.evidence_type,
        status=ClaimStatus(args.status) if args.status else None,
        contradiction=args.contradiction,
    )
    claims = ledger.query(query)
    if args.json:
        payload = [json.loads(to_json(c)) for c in claims]
        print(json.dumps(payload, indent=2))
    else:
        for c in claims:
            print(c.id)
    return 0


def _handle_index_rebuild(args: argparse.Namespace, paths: RunPaths) -> int:
    idx = LedgerIndex(paths)
    idx.rebuild()
    print(f"Rebuilt index for run '{paths.slug}'")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research-corpus",
        description="Research Harness corpus and evidence CLI",
    )
    parser.add_argument(
        "--root",
        default="research",
        help="Root directory for research runs (default: research)",
    )
    parser.add_argument(
        "--run",
        required=True,
        help="Run slug identifier",
    )
    parser.add_argument(
        "--corpus",
        choices=["stub", "notebooklm"],
        default="stub",
        help="Corpus provider (default: stub)",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="NotebookLM profile name",
    )
    parser.add_argument(
        "--notebook-id",
        default=None,
        help="NotebookLM notebook ID",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    subparsers.add_parser("init", help="Initialize research run directory tree")

    # source
    source_p = subparsers.add_parser("source", help="Source operations")
    source_sub = source_p.add_subparsers(dest="subcommand", required=True)

    admit_p = source_sub.add_parser("admit", help="Admit a source candidate")
    admit_p.add_argument("target", help="URL or local file path")
    admit_p.add_argument(
        "--kind",
        required=True,
        choices=[k.value for k in SourceKind],
        help="Source kind",
    )
    admit_p.add_argument("--title", default="", help="Optional source title")
    admit_p.add_argument("--by", default="", help="Researcher name")
    admit_p.add_argument("--reason", default="", help="Admission reason")

    reject_p = source_sub.add_parser("reject", help="Reject a source candidate")
    reject_p.add_argument("target", help="URL or local file path")
    reject_p.add_argument("--reason", required=True, help="Rejection reason")
    reject_p.add_argument("--by", default="", help="Researcher name")

    list_p = source_sub.add_parser("list", help="List sources in index")
    list_p.add_argument("--json", action="store_true", help="Output JSON array")

    # corpus
    corpus_p = subparsers.add_parser("corpus", help="Corpus operations")
    corpus_sub = corpus_p.add_subparsers(dest="subcommand", required=True)

    corpus_sub.add_parser("status", help="Probe corpus provider status")

    ingest_p = corpus_sub.add_parser("ingest", help="Ingest admitted source(s)")
    ingest_p.add_argument("source_ids", nargs="+", help="Admitted source ID(s) to ingest")
    ingest_p.add_argument("--content", default=None, help="File containing content text")

    ask_p = corpus_sub.add_parser("ask", help="Ask question grounded in corpus")
    ask_p.add_argument("question", help="Question text")
    ask_p.add_argument(
        "--source",
        action="append",
        dest="source_ids",
        default=None,
        help="Restrict query to source ID(s)",
    )

    # claim
    claim_p = subparsers.add_parser("claim", help="Claim operations")
    claim_sub = claim_p.add_subparsers(dest="subcommand", required=True)

    claim_add_p = claim_sub.add_parser("add", help="Add a claim to the ledger")
    claim_add_p.add_argument("--id", default=None, help="Claim ID")
    claim_add_p.add_argument("--claim", default=None, help="Claim text")
    claim_add_p.add_argument(
        "--type",
        choices=[t.value for t in ClaimType],
        default=None,
        help="Claim type",
    )
    claim_add_p.add_argument(
        "--confidence",
        choices=[c.value for c in Confidence],
        default=None,
        help="Confidence level",
    )
    claim_add_p.add_argument(
        "--status",
        choices=[s.value for s in ClaimStatus],
        default=None,
        help="Claim status",
    )
    claim_add_p.add_argument("--topic", default=None, help="Topic")
    claim_add_p.add_argument("--researcher", default=None, help="Researcher")
    claim_add_p.add_argument(
        "--caveat",
        action="append",
        default=[],
        help="Caveat string (repeatable)",
    )
    claim_add_p.add_argument(
        "--conflict",
        action="append",
        default=[],
        help="Conflicting claim ID (repeatable)",
    )
    claim_add_p.add_argument(
        "--evidence",
        action="append",
        default=[],
        help="EvidenceRef JSON object string (repeatable)",
    )
    claim_add_p.add_argument(
        "--json",
        default=None,
        help="Path to JSON file with complete claim record",
    )

    # contradiction
    contra_p = subparsers.add_parser("contradiction", help="Contradiction operations")
    contra_sub = contra_p.add_subparsers(dest="subcommand", required=True)

    contra_add_p = contra_sub.add_parser("add", help="Add a contradiction to the ledger")
    contra_add_p.add_argument("--id", default=None, help="Contradiction ID")
    contra_add_p.add_argument(
        "--claim-id",
        action="append",
        default=[],
        help="Conflicting claim ID (repeatable)",
    )
    contra_add_p.add_argument("--description", default=None, help="Description")
    contra_add_p.add_argument(
        "--status",
        choices=[s.value for s in ClaimStatus],
        default=None,
        help="Claim status",
    )
    contra_add_p.add_argument(
        "--json",
        default=None,
        help="Path to JSON file with complete contradiction record",
    )

    # ledger
    ledger_p = subparsers.add_parser("ledger", help="Ledger operations")
    ledger_sub = ledger_p.add_subparsers(dest="subcommand", required=True)

    ledger_q_p = ledger_sub.add_parser("query", help="Query claims in the ledger")
    ledger_q_p.add_argument("--topic", default=None, help="Filter by topic")
    ledger_q_p.add_argument(
        "--confidence",
        choices=[c.value for c in Confidence],
        default=None,
        help="Filter by confidence",
    )
    ledger_q_p.add_argument("--researcher", default=None, help="Filter by researcher")
    ledger_q_p.add_argument("--source", default=None, help="Filter by source ID")
    ledger_q_p.add_argument("--evidence-type", default=None, help="Filter by evidence type")
    ledger_q_p.add_argument(
        "--status",
        choices=[s.value for s in ClaimStatus],
        default=None,
        help="Filter by claim status",
    )
    ledger_q_p.add_argument("--contradiction", default=None, help="Filter by contradiction state")
    ledger_q_p.add_argument(
        "--json",
        action="store_true",
        help="Output JSON array of full claim records",
    )

    # index
    index_p = subparsers.add_parser("index", help="Index operations")
    index_sub = index_p.add_subparsers(dest="subcommand", required=True)
    index_sub.add_parser("rebuild", help="Rebuild SQLite ledger index")

    return parser


def _print_error(exc: Exception) -> None:
    msg = str(exc).strip()
    if "\n" in msg:
        msg = msg.splitlines()[0]
    print(msg, file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        paths = RunPaths(args.root, args.run)

        if args.command != "init":
            if not (paths.run_dir.exists() and paths.source_index.exists()):
                raise ValueError("run not initialized")

        if args.command == "init":
            return _handle_init(args, paths)
        elif args.command == "source":
            if args.subcommand == "admit":
                return _handle_source_admit(args, paths)
            elif args.subcommand == "reject":
                return _handle_source_reject(args, paths)
            elif args.subcommand == "list":
                return _handle_source_list(args, paths)
        elif args.command == "corpus":
            if args.subcommand == "status":
                return _handle_corpus_status(args, paths)
            elif args.subcommand == "ingest":
                return _handle_corpus_ingest(args, paths)
            elif args.subcommand == "ask":
                return _handle_corpus_ask(args, paths)
        elif args.command == "claim":
            if args.subcommand == "add":
                return _handle_claim_add(args, paths)
        elif args.command == "contradiction":
            if args.subcommand == "add":
                return _handle_contradiction_add(args, paths)
        elif args.command == "ledger":
            if args.subcommand == "query":
                return _handle_ledger_query(args, paths)
        elif args.command == "index":
            if args.subcommand == "rebuild":
                return _handle_index_rebuild(args, paths)

        raise ValueError(f"Unknown command: {args.command}")

    except CorpusUnavailable as exc:
        _print_error(exc)
        return 3
    except CorpusIngestError as exc:
        _print_error(exc)
        return 4
    except (ValueError, TypeError, FileNotFoundError, LedgerCorruptError) as exc:
        _print_error(exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
