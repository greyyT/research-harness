"""Subprocess tests for the research-corpus CLI (PLAN §4.9, §5 S7)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def _run_cli(
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke research-corpus CLI as a subprocess."""
    sub_env = os.environ.copy()
    sub_env["PYTHONPATH"] = "src"
    # Ensure no ambient NotebookLM credentials interfere with tests
    sub_env.pop("NOTEBOOKLM_AUTH_JSON", None)
    sub_env.pop("NOTEBOOKLM_PROFILE", None)
    if env:
        sub_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "research_corpus.cli", *args],
        capture_output=True,
        text=True,
        env=sub_env,
    )


def test_cli_uninitialized_run(tmp_path: Path) -> None:
    """A non-init command on an uninitialized run exits 2 with 'run not initialized'."""
    res = _run_cli("--root", str(tmp_path), "--run", "nonexistent", "ledger", "query")
    assert res.returncode == 2
    assert res.stdout == ""
    assert res.stderr.strip() == "run not initialized"
    assert res.stderr.strip().count("\n") == 0


def test_cli_stub_e2e_workflow(tmp_path: Path) -> None:
    """Full workflow covering PLAN §5 S7:

    init -> source admit x3 (A/B/C) -> corpus ingest (stub) -> claim add x2 ->
    ledger query --status supported --json -> re-ingest (reused) -> corpus ask with citations.
    """
    root = str(tmp_path)
    run = "e2e-demo"

    # 1. init
    res = _run_cli("--root", root, "--run", run, "init")
    assert res.returncode == 0
    assert "Initialized run" in res.stdout

    # 2. source admit x3 for Tier-A, Tier-B, Tier-C kinds
    # Tier A (URL): official_specification
    res_a = _run_cli(
        "--root", root, "--run", run,
        "source", "admit", "https://example.com/spec",
        "--kind", "official_specification",
        "--title", "Formal Spec",
        "--by", "alice",
        "--reason", "Foundational specification",
    )
    assert res_a.returncode == 0
    assert "(A)" in res_a.stdout
    id_a = res_a.stdout.strip().split()[0]

    # Tier B (Local path): technical_analysis
    report_file = tmp_path / "report.txt"
    report_file.write_text("Detailed engineering analysis of architecture memory guarantees.\n", encoding="utf-8")
    res_b = _run_cli(
        "--root", root, "--run", run,
        "source", "admit", str(report_file),
        "--kind", "technical_analysis",
        "--title", "Tech Analysis",
        "--by", "bob",
        "--reason", "Reproduction report",
    )
    assert res_b.returncode == 0
    assert "(B)" in res_b.stdout
    id_b = res_b.stdout.strip().split()[0]

    # Tier C (URL): blog_post
    res_c = _run_cli(
        "--root", root, "--run", run,
        "source", "admit", "https://example.com/blog/notes",
        "--kind", "blog_post",
        "--title", "Blog Notes",
        "--by", "carol",
        "--reason", "Informal discussion",
    )
    assert res_c.returncode == 0
    assert "(C)" in res_c.stdout
    id_c = res_c.stdout.strip().split()[0]

    # Check source list (human and JSON)
    res_list = _run_cli("--root", root, "--run", run, "source", "list")
    assert res_list.returncode == 0
    assert id_a in res_list.stdout
    assert id_b in res_list.stdout
    assert id_c in res_list.stdout

    res_list_json = _run_cli("--root", root, "--run", run, "source", "list", "--json")
    assert res_list_json.returncode == 0
    listed_sources = json.loads(res_list_json.stdout)
    assert len(listed_sources) == 3
    assert {s["id"] for s in listed_sources} == {id_a, id_b, id_c}

    # 3. corpus ingest (stub)
    # URL source requires --content
    content_file = tmp_path / "spec_content.txt"
    content_file.write_text("Memory models require sequential consistency for safety guarantees.\n", encoding="utf-8")
    res_ingest_a = _run_cli(
        "--root", root, "--run", run,
        "corpus", "ingest", id_a,
        "--content", str(content_file),
    )
    assert res_ingest_a.returncode == 0
    assert "reused=False" in res_ingest_a.stdout

    # File source uses existing file
    res_ingest_b = _run_cli(
        "--root", root, "--run", run,
        "corpus", "ingest", id_b,
    )
    assert res_ingest_b.returncode == 0
    assert "reused=False" in res_ingest_b.stdout

    # Re-ingest must report reused
    res_reingest = _run_cli(
        "--root", root, "--run", run,
        "corpus", "ingest", id_a,
    )
    assert res_reingest.returncode == 0
    assert "reused=True" in res_reingest.stdout
    assert "reused" in res_reingest.stdout

    # 4. corpus ask prints citations WITH source ids
    res_ask = _run_cli(
        "--root", root, "--run", run,
        "corpus", "ask", "sequential consistency memory models",
        "--source", id_a,
    )
    assert res_ask.returncode == 0
    assert f"[{id_a}]" in res_ask.stdout
    assert "sequential consistency" in res_ask.stdout

    # 5. claim add x2 (evidence citing admitted source ids)
    ev_a = json.dumps({
        "source_id": id_a,
        "evidence_type": "spec",
        "locator": "line 1",
        "excerpt": "Memory models require sequential consistency for safety guarantees.",
    })
    res_claim_1 = _run_cli(
        "--root", root, "--run", run,
        "claim", "add",
        "--id", "claim-001",
        "--claim", "Sequential consistency is required for safety guarantees",
        "--type", "fact",
        "--confidence", "high",
        "--status", "supported",
        "--topic", "concurrency",
        "--researcher", "alice",
        "--evidence", ev_a,
    )
    assert res_claim_1.returncode == 0
    assert "Added claim claim-001" in res_claim_1.stdout

    ev_b = json.dumps({
        "source_id": id_b,
        "evidence_type": "analysis",
        "locator": "line 1",
        "excerpt": "Detailed engineering analysis of architecture memory guarantees.",
    })
    res_claim_2 = _run_cli(
        "--root", root, "--run", run,
        "claim", "add",
        "--id", "claim-002",
        "--claim", "Architecture guarantees are analyzed in engineering report",
        "--type", "inference",
        "--confidence", "medium",
        "--status", "supported",
        "--topic", "concurrency",
        "--researcher", "bob",
        "--evidence", ev_b,
    )
    assert res_claim_2.returncode == 0
    assert "Added claim claim-002" in res_claim_2.stdout

    # 6. ledger query --status supported --json returns expected claim id
    res_query_json = _run_cli(
        "--root", root, "--run", run,
        "ledger", "query",
        "--status", "supported",
        "--json",
    )
    assert res_query_json.returncode == 0
    claims_out = json.loads(res_query_json.stdout)
    claim_ids = [c["id"] for c in claims_out]
    assert "claim-001" in claim_ids
    assert "claim-002" in claim_ids

    # Query with human output
    res_query_human = _run_cli(
        "--root", root, "--run", run,
        "ledger", "query",
        "--researcher", "alice",
    )
    assert res_query_human.returncode == 0
    assert res_query_human.stdout.strip().splitlines() == ["claim-001"]


def test_cli_notebooklm_no_auth(tmp_path: Path) -> None:
    """--corpus notebooklm with no auth:

    corpus status exits 3 and reports available=False.
    corpus ask exits 3 with a one-line CorpusUnavailable message and no traceback.
    """
    root = str(tmp_path / "research")
    run = "nlm-auth-test"

    # Init run first
    res_init = _run_cli("--root", root, "--run", run, "init")
    assert res_init.returncode == 0

    # Isolate HOME to empty directory to guarantee no cached auth exists
    empty_home = tmp_path / "isolated_home"
    empty_home.mkdir(parents=True, exist_ok=True)
    no_auth_env = {"HOME": str(empty_home)}

    # corpus status exits 3 and reports available=False
    res_status = _run_cli(
        "--root", root, "--run", run,
        "--corpus", "notebooklm",
        "corpus", "status",
        env=no_auth_env,
    )
    assert res_status.returncode == 3
    assert "available: False" in res_status.stdout
    assert "Traceback" not in res_status.stderr

    # corpus ask exits 3 with a one-line CorpusUnavailable message and no traceback
    res_ask = _run_cli(
        "--root", root, "--run", run,
        "--corpus", "notebooklm",
        "corpus", "ask", "What are the findings?",
        env=no_auth_env,
    )
    assert res_ask.returncode == 3
    assert "Traceback" not in res_ask.stderr
    assert res_ask.stderr.strip() != ""
    assert res_ask.stderr.strip().count("\n") == 0


def test_cli_source_reject_and_rejection_guard(tmp_path: Path) -> None:
    """source reject records a rejected record; cannot ingest or cite rejected source."""
    root = str(tmp_path)
    run = "reject-demo"
    _run_cli("--root", root, "--run", run, "init")

    # Reject source
    res_reject = _run_cli(
        "--root", root, "--run", run,
        "source", "reject", "https://example.com/untrusted",
        "--reason", "Unverified claims without methodology",
        "--by", "reviewer",
    )
    assert res_reject.returncode == 0
    assert "(rejected)" in res_reject.stdout
    rej_id = res_reject.stdout.strip().split()[0]

    # Attempting corpus ingest on rejected source exits 2
    res_ingest = _run_cli(
        "--root", root, "--run", run,
        "corpus", "ingest", rej_id,
    )
    assert res_ingest.returncode == 2
    assert "rejected" in res_ingest.stderr
    assert "Traceback" not in res_ingest.stderr

    # Attempting claim add citing rejected source exits 2
    ev = json.dumps({
        "source_id": rej_id,
        "evidence_type": "blog",
        "locator": "p. 1",
        "excerpt": "Some unverified text",
    })
    res_claim = _run_cli(
        "--root", root, "--run", run,
        "claim", "add",
        "--id", "claim-bad",
        "--claim", "Bad claim",
        "--type", "fact",
        "--confidence", "low",
        "--status", "supported",
        "--topic", "topic",
        "--researcher", "alice",
        "--evidence", ev,
    )
    assert res_claim.returncode == 2
    assert "rejected" in res_claim.stderr.lower() or "source" in res_claim.stderr.lower()
    assert "Traceback" not in res_claim.stderr


def test_cli_claim_add_json_file(tmp_path: Path) -> None:
    """claim add --json FILE loads full record into ledger."""
    root = str(tmp_path)
    run = "json-claim-demo"
    _run_cli("--root", root, "--run", run, "init")

    # Admit source first
    res_admit = _run_cli(
        "--root", root, "--run", run,
        "source", "admit", "https://example.com/paper",
        "--kind", "paper",
        "--by", "alice",
    )
    source_id = res_admit.stdout.strip().split()[0]

    claim_file = tmp_path / "claim.json"
    claim_payload = {
        "id": "claim-file-1",
        "claim": "Statement from file",
        "type": "fact",
        "confidence": "high",
        "status": "supported",
        "topic": "testing",
        "researcher": "alice",
        "evidence": [
            {
                "source_id": source_id,
                "evidence_type": "paper",
                "locator": "sec. 3",
                "excerpt": "Verbatim evidence excerpt",
            }
        ],
    }
    claim_file.write_text(json.dumps(claim_payload), encoding="utf-8")

    res_add = _run_cli(
        "--root", root, "--run", run,
        "claim", "add", "--json", str(claim_file),
    )
    assert res_add.returncode == 0
    assert "Added claim claim-file-1" in res_add.stdout

    res_q = _run_cli("--root", root, "--run", run, "ledger", "query", "--topic", "testing")
    assert res_q.returncode == 0
    assert "claim-file-1" in res_q.stdout


def test_cli_contradiction_add_and_index_rebuild(tmp_path: Path) -> None:
    """contradiction add and index rebuild operations."""
    root = str(tmp_path)
    run = "contra-demo"
    _run_cli("--root", root, "--run", run, "init")

    # Admit source and add 2 claims
    res_admit = _run_cli(
        "--root", root, "--run", run,
        "source", "admit", "https://example.com/spec",
        "--kind", "official_specification",
        "--by", "alice",
    )
    sid = res_admit.stdout.strip().split()[0]

    ev = json.dumps({
        "source_id": sid,
        "evidence_type": "spec",
        "locator": "p. 1",
        "excerpt": "Evidence excerpt line",
    })
    _run_cli(
        "--root", root, "--run", run,
        "claim", "add",
        "--id", "c1",
        "--claim", "Statement 1",
        "--type", "fact",
        "--confidence", "high",
        "--status", "supported",
        "--topic", "t1",
        "--researcher", "alice",
        "--evidence", ev,
    )
    _run_cli(
        "--root", root, "--run", run,
        "claim", "add",
        "--id", "c2",
        "--claim", "Statement 2",
        "--type", "fact",
        "--confidence", "high",
        "--status", "supported",
        "--topic", "t1",
        "--researcher", "alice",
        "--evidence", ev,
    )

    # Add contradiction
    res_contra = _run_cli(
        "--root", root, "--run", run,
        "contradiction", "add",
        "--id", "contra-01",
        "--claim-id", "c1",
        "--claim-id", "c2",
        "--description", "Claims 1 and 2 contradict",
        "--status", "contradicted",
    )
    assert res_contra.returncode == 0
    assert "Added contradiction contra-01" in res_contra.stdout

    # Rebuild index
    res_rebuild = _run_cli("--root", root, "--run", run, "index", "rebuild")
    assert res_rebuild.returncode == 0
    assert "Rebuilt index" in res_rebuild.stdout

    # Query using rebuilt index
    res_q = _run_cli("--root", root, "--run", run, "ledger", "query", "--topic", "t1")
    assert res_q.returncode == 0
    assert "c1" in res_q.stdout
    assert "c2" in res_q.stdout


def test_cli_research_state_cannot_be_admitted(tmp_path: Path) -> None:
    """research-state.md can never be admitted as a source."""
    root = str(tmp_path)
    run = "synthetic-check"
    _run_cli("--root", root, "--run", run, "init")

    res = _run_cli(
        "--root", root, "--run", run,
        "source", "admit", "research-state.md",
        "--kind", "paper",
    )
    assert res.returncode == 2
    assert "synthetic summary" in res.stderr
    assert "Traceback" not in res.stderr
