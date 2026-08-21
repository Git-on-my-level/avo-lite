# AVO (NVIDIA, arXiv 2603.24517v1, 25 Mar 2026) — condensed

"Agentic Variation Operators for Autonomous Evolutionary Search" — Chen/Ye/Xu et al., NVIDIA.
Full text extracted to `../paper/avo-nvidia.txt` (12 pages); PDF in `../paper/`.

## The one-sentence idea
Classical LLM-evolution (FunSearch, AlphaEvolve, LoongFlow, TTT-Discover) decomposes
`Vary(P_t) = Generate(Sample(P_t))` and confines the LLM to `Generate` — one single-turn output per
invocation inside a framework-owned pipeline. AVO replaces the whole operator with one autonomous
agent run: `Vary(P_t) = Agent(P_t, K, f)`. The agent decides what to consult, what to edit, when to
test, and when to commit.

## The five components (this is the transferable part)
1. **P_t — lineage.** Full history of (solution, score) pairs. Persisted as **git commits, one per
   committed version**, score attached. State continuity across the whole run comes for free.
2. **K — knowledge base.** Domain docs + reference implementations the agent may consult at will
   (CUDA/PTX docs, Blackwell arch specs, FA4 source). Not injected — *available for retrieval*.
3. **f — scoring function.** Two-part and explicitly vector-valued:
   - correctness vs a reference implementation — **hard gate: fail ⇒ score 0 regardless of speed**
   - a performance vector `f(x) = (f_1..f_n)`, one entry per test configuration (4 seq-lens × causal/
     non-causal), aggregated by geomean for "running best".
4. **The agent loop (one variation step).** plan → consult K and prior versions in P_t → edit →
   evaluate with f → diagnose failure → revise. Repeats internally until it has something worth
   committing. Many internal attempts per commit.
5. **Supervisor.** A second, cheap agent that watches for two named failure modes —
   **stall** (line of exploration exhausted) and **unproductive cycles** (repeated non-improving edits) —
   and on trigger *reviews the whole trajectory and proposes several fresh candidate directions*.
   Conditional intervention only; it does not run the search.

## Commit policy (the ratchet)
Persist a new version **only if** it (a) passes correctness and (b) matches or beats the best
committed score so far. Failed attempts stay in the agent's internal trajectory but never enter the
lineage. Single-lineage in this paper (no islands/MAP-Elites archive) deliberately, to isolate the
operator. Paper notes AVO is orthogonal to population structure.

## Scale / results
- 7 days continuous, **no human intervention**; ~500 optimization directions explored internally →
  **40 committed versions**.
- Beat cuDNN by up to 3.5%, FlashAttention-4 by up to 10.5% on B200 MHA; 1668 TFLOPS peak.
- Transfer: adapting the evolved MHA kernel to GQA took **30 min of autonomous effort** → +7.0% vs
  cuDNN, +9.3% vs FA4. Optimizations generalized off the evolved benchmark configs.
- Agent was an **off-the-shelf general coding agent, no task-specific modification**. Only K and f
  were supplied. This is the strongest argument that the method is harness-portable.

## Trajectory shape (what a dashboard should expect)
- **Discrete jumps, not gradual.** 5 big architectural inflections (v8, v13, v20, v30, v33); the rest
  are plateaus of micro-refinement.
- **Diminishing returns.** v1–v20 large absolute gains; v21–v40 small, compounding.
- Best single win (+8.1% non-causal) was *branchless accumulator rescaling* — removing a branch let it
  also swap a blocking fence for a lighter one. Others: correction/MMA pipeline overlap (+1.1%),
  register rebalancing 192/80/48 → 184/88/56 (+2.1%). All required joint reasoning across subsystems,
  driven by **profiler feedback**, not parameter tuning.

## What is actually load-bearing vs incidental
Load-bearing (portable to any task): a cheap machine-checkable `f` with a hard correctness gate;
a persisted scored lineage the agent can read; a retrievable knowledge base; the agent owning its own
inner loop; a commit ratchet; a stagnation detector with trajectory-level redirect.
Incidental (GPU-specific): CUDA/PTX, TFLOPS, B200, profiler-as-feedback-channel, 7-day budget,
single-lineage choice.

## Preconditions the paper quietly assumes
- `f` is **fast, deterministic-ish, and fully automated** (compile + correctness + timed benchmark),
  runnable hundreds of times unattended.
- The environment is **cheap to reset** and failures are non-destructive.
- There is a **reference implementation** for correctness and **baselines** for "is this good".
- The metric has real headroom and is **not saturated by noise** (they average 10 runs, report stddev).
