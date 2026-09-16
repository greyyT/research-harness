# Autonomous Research Harness

An autonomous, multi-agent research harness that conducts grounded technical research using Claude Code and Google NotebookLM.

The system decomposes complex research briefs, admits and classifies evidence into rigorous tiers, persists structured findings in an append-only Evidence Ledger, and synthesizes traceable recommendations.

## Documentation

Full documentation on architecture, Evidence Ledger schemas, source admission, corpus configuration, and operational boundaries is available in [docs/research-corpus.md](docs/research-corpus.md).

## Installation & Usage

Install the package and optional NotebookLM integration via `uv`:

```bash
uv pip install -e ".[notebooklm]"
```

Run the research corpus CLI:

```bash
uv run research-corpus --root research --run example-memory-systems ledger query
```
