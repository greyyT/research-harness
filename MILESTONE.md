# Milestones: Autonomous Research System

Three feature-sized tasks, dependency-ordered. Mapping follows the PRD's own role separation (§30): what is stored/known → whether it's trustworthy → what it means and what to recommend. Each milestone delivers standalone value and carries its own owner-consequential decision worth a `TARGET_DESIGN.md` gate.

## 1. research-corpus

The persistence layer — the only heavy engineering. Everything else stands on it.

- Evidence Ledger: schema + statuses + query by topic/confidence/researcher/source/evidence-type/contradiction/status (§14)
- Source admission + tiering A/B/C (§9)
- NotebookLM integration as shared grounded corpus + query-first flow (§10) — no official API, real design work
- Source reuse / dedup so researchers don't rediscover material (§28)
- Artifact tree + `research-state.md` (§11, §26)

**Value after:** a queryable corpus + ledger usable manually.
**Key decision:** NotebookLM integration mechanism; ledger store/index.

## 2. research-engine

Produce-and-verify claims end to end — "turn a brief into a verified ledger."

- Research brief (§6) + Landscape Scout (§7)
- Specialist researchers, scoped + parallel (§12) with output contract (§13) → ledger merge
- Gap + contradiction analysis (§15) → targeted research loop (§16)
- Global Critic + issue statuses (§17); review policy / audit sampling (§18)

**Depends on:** 1.
**Value after:** an autonomous verified-evidence run.
**Key decision:** parallelism / researcher spawn model.

## 3. deliberate-and-report

Interpret-and-decide — "turn a verified ledger into a recommendation."

- Research Council: 3-round protocol, roles, outcome types (§19–22)
- Judge: evidence-weighted synthesis, not majority vote (§23)
- Final report organized around questions/conclusions (§24)
- Top-level pipeline orchestration (§5) + iteration limits / budgets / stop conditions (§25)

**Depends on:** 2 (stable ledger).
**Value after:** the full end-to-end pipeline.
**Key decision:** council/judge structure; budget + termination policy.
