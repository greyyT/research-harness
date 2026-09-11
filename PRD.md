# Product Requirements Document: Autonomous Research System

## 1. Overview

Build an autonomous research system using Claude Code as the primary execution harness.

The system should research complex topics by combining source discovery, source-grounded analysis, parallel specialist researchers, structured evidence collection, iterative verification, multi-agent deliberation, and final recommendation generation.

The system is intended for research tasks such as:

- evaluating agent memory architectures;
- comparing technical approaches;
- understanding emerging research areas;
- making architecture or tooling decisions;
- investigating conflicting academic or engineering evidence;
- producing evidence-backed technical reports.

The system must optimize for:

1. evidence quality;
2. research coverage;
3. traceability;
4. explicit uncertainty;
5. efficient reuse of previously acquired sources;
6. resistance to unsupported conclusions.

---

# 2. Goals

The system should:

- autonomously decompose a broad research problem;
- discover high-quality sources;
- maintain a persistent research corpus in NotebookLM;
- reuse collected knowledge across researchers;
- run multiple specialized research tasks in parallel where appropriate;
- maintain a structured evidence ledger separate from prose summaries;
- identify contradictions and missing evidence;
- iteratively resolve important research gaps;
- critically review the combined evidence;
- deliberate across competing interpretations;
- produce a final evidence-backed synthesis and recommendation.

The final output should answer the research question rather than merely summarize individual sources.

---

# 3. Non-Goals

The system should not:

- treat every web page as trustworthy evidence;
- ingest every discovered source into NotebookLM;
- rely solely on long Claude contexts as persistent research memory;
- use browser automation for ordinary web search when simpler tools work;
- create one reviewer for every researcher by default;
- force consensus where evidence is insufficient;
- allow deliberation agents to invent new evidence;
- treat generated summaries as equivalent to primary sources;
- repeatedly rediscover sources already present in the research corpus.

---

# 4. Core Principles

## 4.1 Evidence before conclusions

All important factual conclusions must be traceable to underlying evidence.

Researchers should separate:

- fact;
- source-author claim;
- researcher inference;
- hypothesis;
- unresolved question.

---

## 4.2 Persistent research corpus

NotebookLM acts as the shared source-grounded research corpus.

It should contain accepted research materials such as:

- papers;
- benchmark reports;
- technical documentation;
- official repositories;
- engineering reports;
- relevant datasets;
- high-quality secondary analysis when necessary.

NotebookLM is not the canonical structured evidence store.

The Evidence Ledger remains the canonical representation of individual research claims.

---

## 4.3 Disposable researchers, persistent knowledge

Specialist researcher agents should be treated as temporary workers.

Their conversations do not need to persist indefinitely.

Persistent knowledge should instead live in:

- NotebookLM;
- Evidence Ledger;
- research metadata;
- unresolved-question records;
- research-state documents.

---

## 4.4 Broad first, narrow later

Research begins with landscape discovery.

Only after terminology, major approaches, benchmarks, and important disagreements are understood should the system create narrowly scoped specialist research tasks.

---

## 4.5 Verify evidence, not every agent

The system should not automatically pair every researcher with another full reviewer.

Verification should focus on:

- evidence quality;
- citation correctness;
- contradictions;
- high-impact claims;
- low-confidence claims;
- surprising findings;
- weakly supported conclusions.

---

# 5. High-Level Workflow

```text
Research Goal
    ↓
Research Brief
    ↓
Landscape Scout
    ↓
Source Discovery
    ↓
Source Admission
    ↓
NotebookLM Corpus
    ↓
Research Decomposition
    ↓
Parallel Specialist Researchers
    ↓
Evidence Ledger
    ↓
Gap + Contradiction Analysis
    ↓
Targeted Research
    ↓
Global Critic
    ↓
Resolve Blocking Issues
    ↓
Council Deliberation
    ↓
Judge
    ↓
Final Research Report
```

Several stages may repeat when new evidence, contradictions, or research gaps appear.

---

# 6. Research Brief

Every research project begins with a structured research brief.

Required fields:

```yaml
topic:
goal:
decision_to_support:

questions:
  - ...

time_scope:

source_priorities:
  - primary research
  - official implementation
  - benchmark
  - engineering report
  - secondary analysis

constraints:

expected_output:
```

Example:

```yaml
topic: LLM agent memory systems

goal: Understand the current state of agent memory and recommend
  an architecture for a long-running coding/personal agent.

questions:
  - What memory architectures currently exist?
  - How are memories written and retrieved?
  - How are stale memories updated or forgotten?
  - Which benchmarks meaningfully measure memory quality?
  - What approaches perform well under which workloads?
  - What major failure modes remain?

time_scope: Prioritize 2025-2026 while including foundational earlier work.

expected_output:
  - taxonomy
  - architecture comparison
  - benchmark comparison
  - unresolved questions
  - implementation recommendation
```

---

# 7. Landscape Scout

The Scout performs broad initial discovery.

Its purpose is not to produce the final answer.

It should identify:

- terminology;
- important papers;
- surveys;
- benchmarks;
- implementations;
- repositories;
- major competing approaches;
- recognized failure modes;
- important researchers or organizations;
- potentially missing research dimensions.

Output:

```yaml
major_topics:
major_approaches:
important_sources:
benchmarks:
implementations:
known_disagreements:
suggested_research_tracks:
```

The orchestrator uses this output to determine specialist research tasks.

---

# 8. Source Discovery

## 8.1 Preferred tool order

For external research:

```text
WebSearch
    ↓
WebFetch
    ↓
Browser fallback if required
```

WebSearch is the primary discovery mechanism.

WebFetch is the default reading mechanism.

Browser automation should only be used when necessary.

---

## 8.2 Browser use cases

Use browser automation for cases such as:

- JavaScript-rendered pages;
- interactive benchmark leaderboards;
- authenticated resources the user is authorized to access;
- pagination or expandable content;
- dynamically loaded tables;
- pages that WebFetch cannot retrieve meaningfully;
- visual information requiring inspection.

Browser automation should not be used as the default search interface.

The browser should be treated primarily as a source-acquisition mechanism.

---

# 9. Source Admission

Not every discovered source should enter NotebookLM.

Sources should be evaluated before admission.

Suggested source classes:

### Tier A

Preferred evidence:

- original papers;
- official benchmarks;
- official repositories;
- official specifications;
- official technical documentation;
- original datasets.

### Tier B

Useful supporting evidence:

- high-quality engineering reports;
- independent reproductions;
- serious technical analyses;
- conference presentations.

### Tier C

Discovery and community evidence:

- blog posts;
- discussions;
- forum threads;
- Reddit;
- informal explanations.

Tier C sources should rarely support high-confidence technical conclusions alone.

---

# 10. NotebookLM

NotebookLM acts as the shared grounded research corpus.

Responsibilities:

- store accepted sources;
- allow agents to query multiple sources together;
- support cross-document comparison;
- surface contradictions;
- provide grounded citations;
- reduce repeated source reading;
- make newly discovered sources immediately reusable by later researchers.

Researchers should first query NotebookLM for existing evidence before performing new external research.

Preferred research behavior:

```text
Research question
    ↓
Query NotebookLM
    ↓
Existing evidence sufficient?
   ↙              ↘
 yes              no
 ↓                 ↓
extract        WebSearch
evidence           ↓
                new source
                    ↓
               source admission
                    ↓
                NotebookLM
```

---

# 11. Research State Document

Each project should maintain a persistent document such as:

```text
research-state.md
```

Suggested structure:

```markdown
# Research Goal

# Current Taxonomy

# Established Findings

# Tentative Findings

# Contradictions

# Unresolved Questions

# Research Gaps

# Current Recommendations

# Next Investigations
```

This document is synthetic and must be clearly marked as such.

Synthetic research summaries must never be treated as independent supporting evidence.

---

# 12. Specialist Researchers

After landscape discovery, the system creates narrowly scoped research tasks.

Example tracks for agent-memory research:

- architecture researcher;
- benchmark researcher;
- implementation researcher;
- security/failure-mode researcher;
- systems/performance researcher.

Each researcher should own a clearly defined scope.

Bad task:

> Research agent memory.

Preferred task:

> Investigate only how current agent-memory systems evaluate memory quality. Compare benchmarks, measured capabilities, methodologies, limitations, and major findings.

Researchers should avoid duplicating each other's work unless independent investigation is intentionally requested.

---

# 13. Researcher Output Contract

Researchers must return structured findings rather than only prose.

Example:

```yaml
finding:
  claim: >
    Strong retrieval performance does not necessarily imply
    strong memory-guided task performance.

type: fact

confidence: high

evidence:
  - source_id: memoryarena
    evidence_type: benchmark
    location: section/result
    summary: ...

caveats:
  - evaluated only under specified benchmark settings

conflicts: []

open_questions:
  - Does the result generalize to coding agents?
```

Required fields:

- claim;
- type;
- evidence;
- source;
- confidence;
- caveats;
- conflicts;
- open questions.

---

# 14. Evidence Ledger

The Evidence Ledger is the structured knowledge layer for claims.

Example record:

```json
{
  "id": "claim-042",
  "claim": "Graph memory improves multi-hop retrieval in benchmark X.",
  "type": "fact",
  "confidence": "medium",
  "sources": ["paper-x", "benchmark-y"],
  "caveats": ["Only evaluated on benchmark X"],
  "conflicts": ["claim-061"],
  "status": "supported"
}
```

Suggested claim statuses:

```text
supported
tentative
contradicted
needs_more_evidence
source_too_weak
unresolved
rejected
```

The Evidence Ledger should support querying by:

- topic;
- confidence;
- researcher;
- source;
- evidence type;
- contradiction;
- claim status.

---

# 15. Gap and Contradiction Analysis

After specialist findings are merged, analyze the combined evidence.

The system should identify:

- important unanswered questions;
- contradictory findings;
- claims supported by only one source;
- claims based primarily on vendor evidence;
- benchmark incompatibilities;
- methodological differences;
- suspiciously strong conclusions;
- areas where evidence is stale;
- conclusions relying heavily on secondary sources.

This analysis produces targeted research tasks rather than another broad search round.

---

# 16. Targeted Research

Targeted research exists to resolve a specific weakness.

Example:

```yaml
question: Does graph-based memory improve long-horizon agent performance
  outside synthetic retrieval benchmarks?

trigger: conflicting evidence

required_output:
  - relevant evaluations
  - benchmark setup
  - measured performance
  - limitations
```

New high-quality sources discovered here should enter the common corpus after admission.

The Evidence Ledger should then be updated.

---

# 17. Global Critic

The Global Critic reviews the combined research rather than individual researcher performance.

Responsibilities:

- identify unsupported claims;
- challenge excessive confidence;
- verify whether evidence actually supports conclusions;
- identify apples-to-oranges comparisons;
- find contradictory evidence;
- distinguish correlation from causation;
- flag vendor claims lacking independent support;
- identify missing perspectives;
- identify claims requiring stronger evidence.

Possible issue statuses:

```text
PASS
NEEDS_MORE_EVIDENCE
CONTRADICTORY
SOURCE_TOO_WEAK
CLAIM_TOO_STRONG
UNRESOLVABLE
```

Blocking issues should be sent back for targeted resolution.

If repeated investigation cannot resolve an issue, it should remain explicitly marked as unresolved rather than forcing a conclusion.

---

# 18. Review Policy

Do not assign a full reviewer to every researcher by default.

Additional review should be triggered for:

- high-impact conclusions;
- low-confidence findings;
- conflicting sources;
- surprising results;
- architecture decisions with significant implementation cost;
- security-sensitive claims;
- weak or secondary evidence.

Suggested audit strategy:

```text
100% high-impact claims
100% contradictory claims
100% low-confidence claims
100% security-sensitive claims
10-20% random sampling of ordinary claims
```

---

# 19. Research Council

Once the evidence is sufficiently stable, multiple agents should independently interpret the evidence.

Council members must work from the same:

- research brief;
- Evidence Ledger;
- NotebookLM corpus;
- unresolved-question list.

The council is not allowed to create new factual evidence.

If members identify missing evidence, that must become a new research request.

---

# 20. Council Roles

Roles should vary depending on the research question.

Typical roles:

### Technical Architect

Focus:

- architectural coherence;
- implementation choices;
- scalability;
- composability.

### Evidence Skeptic

Focus:

- evidence quality;
- methodological limitations;
- overclaiming;
- missing evidence.

### Systems Engineer

Focus:

- latency;
- cost;
- maintenance;
- operational complexity;
- production practicality.

### Alternative Advocate

Focus:

- strongest competing approach;
- overlooked alternatives;
- reasons the leading conclusion may be wrong.

Additional roles may be generated dynamically.

---

# 21. Council Protocol

Council deliberation should be structured.

## Round 1 — Independent position

Each member produces a recommendation before seeing the others.

Required output:

```yaml
recommendation:
confidence:
supporting_claims:
main_risks:
```

## Round 2 — Challenge

Members inspect other positions.

Each member must identify:

- strongest opposing argument;
- unsupported assumptions;
- overlooked tradeoffs;
- evidence that changes the decision.

## Round 3 — Revision

Each member states:

- updated position;
- what changed;
- remaining disagreement;
- confidence;
- recommendation.

---

# 22. Council Outputs

The council should return one of the following outcome types.

## CONSENSUS

Evidence strongly supports one conclusion.

Action:

Proceed to final judgment.

---

## CONDITIONAL_CONSENSUS

One approach is preferred under defined conditions.

Example:

> Use X when workload A dominates; use Y when latency is the primary constraint.

Action:

Final report should clearly describe the conditions.

---

## TRADEOFF

No universal winner exists.

Action:

Produce a decision framework or scenario matrix.

---

## DISAGREEMENT

Council members reach materially different interpretations.

Action:

Determine whether disagreement comes from:

- missing evidence;
- different assumptions;
- different priorities;
- genuine uncertainty.

Missing evidence should produce targeted research.

Priority differences should be preserved rather than artificially resolved.

---

## INSUFFICIENT_EVIDENCE

Available evidence cannot justify a reliable conclusion.

Action:

Either:

- perform further research if valuable; or
- explicitly report uncertainty.

---

## NEW_RESEARCH_GAP

Council discussion identifies an important unanswered question.

Action:

Create a targeted research request, update evidence, perform critique, then reconsider the conclusion.

---

# 23. Judge

The Judge produces the final interpretation after council deliberation.

The Judge should consider:

- research goal;
- evidence quality;
- council positions;
- remaining disagreements;
- user constraints;
- implementation tradeoffs;
- uncertainty.

The Judge must not simply follow majority vote.

Three agents agreeing does not outweigh one agent presenting substantially stronger evidence.

The Judge should reason based on:

```text
evidence quality
×
relevance
×
confidence
×
decision impact
```

---

# 24. Final Report

The final report should be organized around questions and conclusions, not around individual sources.

Bad:

```text
Paper A says...
Paper B says...
Paper C says...
```

Preferred:

```markdown
# Executive Summary

# Current State of the Field

# Key Findings

## How memory should be represented

## How memories should be retrieved

## How stale memories should be handled

# Evidence and Tradeoffs

# Recommended Architecture

# Alternatives Considered

# Risks

# Unresolved Questions

# Confidence

# Sources
```

The report should distinguish:

- established evidence;
- reasonable inference;
- recommendation;
- unresolved uncertainty.

---

# 25. Iteration Limits

Research must not continue indefinitely.

The system should have configurable limits for:

- maximum research rounds;
- maximum gap-resolution attempts;
- maximum council reconsiderations;
- source count;
- token budget;
- cost budget.

Research should stop when one of the following is true:

- all blocking evidence issues are resolved;
- remaining issues are explicitly unresolvable;
- additional searches produce negligible new information;
- research budget is exhausted;
- evidence is sufficient for the requested decision.

---

# 26. Research Artifacts

Suggested project structure:

research/
<research-topic-slug>/
brief.md
research-state.md

```
sources/
  source-index.json

evidence/
  claims.jsonl
  contradictions.jsonl

tasks/
  scout/
  researchers/
  targeted/

reviews/
  critic/
  selective/

council/
  positions/
  challenges/
  revisions/
  result.json

reports/
  final.md
```

NotebookLM remains the external source corpus, while these artifacts provide structured project-level state.

---

# 27. Tool Responsibilities

```text
Claude Code
  orchestration + reasoning

WebSearch
  source discovery

WebFetch
  default source reading

Browser
  difficult source acquisition only

NotebookLM
  shared source-grounded corpus

Filesystem
  persistent structured artifacts

Evidence Ledger
  machine-readable claims

Specialist Agents
  narrow research tasks

Global Critic
  evidence and conclusion verification

Council
  competing interpretation

Judge
  final decision and synthesis
```

---

# 28. Success Criteria

The system is successful when:

- important conclusions can be traced back to evidence;
- researchers do not repeatedly rediscover the same material;
- conflicting findings are surfaced rather than silently merged;
- low-confidence conclusions remain visibly uncertain;
- weak evidence triggers additional investigation;
- research can survive individual agent context loss;
- the final recommendation reflects evidence and tradeoffs rather than agent consensus alone;
- unsupported council claims cannot enter the final report as evidence;
- a user can inspect why the system reached a particular conclusion.

---

# 29. Example End-to-End Flow

Topic:

> What memory architecture should a long-running coding agent use?

Process:

```text
1. Define research brief

2. Scout current field

3. Discover:
   - surveys
   - memory frameworks
   - benchmarks
   - implementation papers
   - production systems

4. Admit useful sources into NotebookLM

5. Create specialist tasks:
   - architecture
   - benchmarks
   - implementations
   - failure modes
   - system cost

6. Researchers query NotebookLM first
   and perform external research only for missing evidence

7. Merge structured findings into Evidence Ledger

8. Analyze contradictions and gaps

9. Run targeted investigations

10. Global Critic reviews evidence

11. Resolve important criticism

12. Council independently evaluates:
    - hybrid memory
    - graph memory
    - simple retrieval memory
    - learned memory approaches

13. Council challenges competing conclusions

14. Judge weighs evidence and constraints

15. Produce final architecture recommendation,
    tradeoffs, confidence and unresolved questions
```

---

# 30. Primary Design Principle

The system should preserve a strict conceptual separation:

```text
Researchers
    determine what is known.

Critic
    determines whether it is trustworthy.

Council
    determines what the evidence means.

Judge
    determines what should be recommended.
```

The resulting research process should be cumulative, evidence-grounded, inspectable, and capable of explicitly saying:

> The available evidence is insufficient to reach a reliable conclusion.

That outcome is preferable to producing an unsupported recommendation.
