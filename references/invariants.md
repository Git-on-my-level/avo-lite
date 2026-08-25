# AVO-lite invariants

AVO-lite remains useful because it enforces a small set of transitions rather than becoming a full
agent framework.

## 1. Correctness and objective are separate

The scorer returns a Boolean gate and a higher-is-better scalar. When correctness fails, the
objective is ignored and may be null. This prevents a fast or aesthetically pleasing wrong candidate
from entering the lineage and avoids wasting expensive benchmark work on known-broken candidates.

## 2. Candidate work is transactional

Each tick creates a detached Git worktree under `.avo/runs/<tick>/worktree`. The agent, scorer, and
verifier operate there. Rejection is deletion, not `git reset` in the user's working checkout.

This is isolation for reliability, not a hostile-code sandbox.

## 3. Accepted lineage is monotone

In rank mode, only above-noise improvement enters the canonical branch. In discover mode, every
correct resolved item may enter because progress can be orthogonal rather than scalar.

## 4. Raw evidence is append-only

The ledger and per-run artifacts retain accepts, rejects, errors, scores, verifier verdicts, and
prompts. Curated memory may change; raw evidence does not. Reports may be redacted, but the source of
truth must remain intact.

## 5. Stored history is fed forward

The next driver sees recent accepted and rejected attempts, curated memory, human pins, and any
supervisor redirect. A Git log that is never placed in model context is not evolutionary memory.

## 6. The agent owns its inner loop

AVO requests one coherent candidate, not one edit or one model turn. The agent may inspect, test,
diagnose, and revise repeatedly before the scorer sees the result.

## 7. Verification challenges would-be winners

An adversarial verifier is optional and expensive, so it runs only after correctness and ratchet
checks indicate acceptance. A falsified result is a normal reject; verifier execution failure is an
infrastructure error.

## 8. Stagnation is deterministic; redirection is rare

Ledger statistics detect stalls. The supervisor reads the meaningful trajectory, including failures,
and emits a one-shot redirect. Redirects and human resumes start fresh detection windows. Repeated
failure eventually halts paid ticks for human review.

## 9. Human pins outrank generated strategy

Pins are durable Markdown written through explicit human commands. Driver and supervisor prompts
receive them before ordinary memory and trajectory context.

## 10. Domain features remain outside the kernel

Planner agents, hypothesis registries, monitoring sentinels, resource dashboards, and research
profiles should integrate through commands, prompts, and files. They are not prerequisites for a
simple rank-mode loop.
