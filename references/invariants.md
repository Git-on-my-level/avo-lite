# The seven AVO-lite invariants (why each is load-bearing)

The smallest set that makes an agent loop *evolutionary* rather than *a cron that calls an LLM*.
If you build nothing else, build 2, 3, and 6 — those are the ones most hand-built loops skip.

1. **Split gate.**
   Correctness is a **hard boolean, evaluated first and separately**; a failing candidate is rejected
   outright and its quality metric is never inspected. Quality is a *vector* plus one scalar `objective`.
   *Why:* it stops the agent from trading correctness for score — the failure mode every naive fitness
   loop hits. In the AVO paper a kernel that fails numerical correctness scores 0 regardless of TFLOPS.

2. **Scored, immutable lineage that is read back as context.**
   Persisted *and retrieved* — the prior scored versions and their notes are fed into the next agent run.
   *Why:* this is the move that turns a cron into evolution. Persisting history you never read back
   (a git log nobody feeds forward) buys you nothing; the agent starts cold every time.

3. **Commit ratchet.**
   A candidate enters the lineage only if it passes correctness and is not worse than the best so far.
   The agent's failed internal attempts never pollute the lineage.
   *Why:* keeps the search monotone without needing a population/archive. In `discover` mode the ratchet
   relaxes to append-only (every correct find is kept) because reward is sparse and orthogonal.

4. **Agent owns its inner loop.**
   Many internal attempts (edit → test → diagnose → revise) per *one* committed version. The scaffold
   gates commits; it does not micromanage the agent's turns.
   *Why:* this is exactly what separates AVO from framework-owned `Generate(Sample(P))` — the agent
   decides what to consult, what to edit, and when to evaluate.

5. **Retrievable K (knowledge base).**
   Domain docs and reference implementations the agent *may consult at will* — a directory plus
   `K/INDEX.md`, opened with the agent's own tools. Not embeddings, not prompt-stuffing.
   *Why:* cheap, portable, and it lets the agent pull the *relevant* reference at the *relevant* step.

6. **Stagnation detector + trajectory-level redirect.**
   A cheap, no-LLM detector watches the ledger for stall / unproductive cycles / repeat edits; on
   trigger a rare, expensive agent reviews the *whole trajectory* and proposes several fresh directions.
   Conditional intervention — it does not run the search.
   *Why:* this is why NVIDIA's run went 7 days unattended. Circuit-breakers (`max_retries`) only make a
   loop *give up*; this makes it *re-aim*.

7. **Cheap-frequent / expensive-rare model split.**
   Driver ticks run on a cheap model; the supervisor runs on Opus/Fable.
   *Why:* the driver fires hundreds of times, the supervisor a handful; spend accordingly.

## What is GPU-incidental in the paper (safe to drop)

CUDA/PTX/TFLOPS/B200, the profiler-as-feedback channel, the 7-day budget, and the single-lineage choice
are all domain-specific. So is the *reward shape*: kernel optimization has a fast deterministic `f`, a
real reference oracle, dense headroom, and cheap resets — an near-ideal AVO substrate. Most real tasks
have a noisier or sparser `f`; that is why AVO-lite has two modes and an optional verify gate.

## Field lessons the paper does not cover (don't regress these)

Drawn from production autonomous loops this scaffold was distilled from:

- **Adversarial falsification before trust** (`--verify`) — stronger than a correctness gate for domains
  with no reference oracle.
- **Epistemic self-correction** — redacting the source of truth caused false findings; keep the ledger raw.
- **Multi-provider tiering, silent-on-success, verdict-with-evidence-paths** — operational maturity the
  paper doesn't cover. AVO-lite grafts the paper's *loop structure* onto these *epistemics*.
