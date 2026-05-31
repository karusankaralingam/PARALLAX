# Deconstruction: "Precise exceptions in relaxed architectures" (ISCA '25)

## The "No-BS" Summary

This paper tackles a problem that has been hiding in plain sight for decades: **what does "precise exception" actually mean when your processor is doing out-of-order execution with observable relaxed memory behavior?**

The classic definition from IBM System/360 era says an exception is precise if the processor state "looks like" you executed instructions sequentially up to the faulting point. But on modern Arm-A processors, loads and stores can be reordered, writes can be forwarded across exception boundaries, and the whole notion of "sequential execution up to a point" is a polite fiction. The authors don't fully *solve* this definitional problem—they explicitly call it an "open problem"—but they do three concrete things:

1. Characterize what relaxed behaviors actually happen across exception entry/exit on Arm-A (via litmus tests and hardware experiments)
2. Build an axiomatic concurrency model extension that captures these behaviors
3. Sketch how software-generated interrupts (SGIs) interact with relaxed memory, which matters for real systems like Linux RCU

The scope is Arm-A with precise synchronous exceptions. They explicitly punt on imprecise exceptions, full GIC modeling, and several corner cases.

---

## The Core Mechanism: A Whiteboard Explanation

**The Setup Problem:**

Imagine you're running code and a page fault happens. The OS handler fixes the page table and returns. For this to work, you need to know *exactly* where you were—which instructions "happened" and which didn't. On a simple in-order processor, this is trivial. On a modern out-of-order core? Chaos.

Here's the crux: Arm-A allows loads and stores to be reordered. So when an exception fires, some "later" memory operations might have already executed (speculatively), and some "earlier" ones might not have propagated yet. The question is: **what can the exception handler (and other threads) observe?**

**The Key Insight:**

The authors identify that **context synchronization** is the mechanism that saves us. On Arm-A, exception entry and exit are (usually) *context-synchronizing events*—they act like an `ISB` (Instruction Synchronization Barrier). This means:

- Instructions *after* the exception boundary cannot be *fetched/decoded/executed* until the exception is taken
- But crucially, this doesn't mean all *memory effects* from before the boundary are visible

Think of it like this: the pipeline gets flushed at exception boundaries (conceptually), but the memory system has its own timeline. A store from before the exception might still be sitting in a store buffer, not yet visible to other cores.

**The Litmus Test Methodology:**

The authors use "litmus tests"—tiny concurrent programs designed to expose specific reorderings. For example, their `MP+svceret+addr` test (Figure 4) shows:

```
Thread 0:                    Thread 1:
STR X0,[X1]  // write x=1    (in handler after SVC)
SVC #0                       LDR X0,[X1]  // read y
(handler)                    EOR X4,X0,X0
STR X2,[X3]  // write y=1    LDR X2,[X3,X4] // read x
ERET
```

Can Thread 1 see `y=1` but `x=0`? **Yes**, because the write to `x` and the write to `y` can be reordered even though there's an exception entry/exit between them. The `SVC`/`ERET` don't act as memory barriers—only as context synchronization.

**The Model Extension:**

They extend the existing Arm-A axiomatic model (the "cat" format used by herdtools) with:

- New events: `TE` (take exception), `ERET`, `MRS`/`MSR` (system register access)
- A `speculative` relation capturing what can't happen until control flow is resolved
- A `ctxob` (context-ordered-before) relation capturing that context-synchronizing events order everything after them

The key axiom is: `acyclic ob` (ordered-before must be acyclic), where `ob` now includes the exception-related orderings.

---

## The Critique: Strengths & Weaknesses

### Why This Got Into ISCA

1. **Foundational Gap Identified**: This is genuinely unexplored territory. The relaxed-memory community has spent 15+ years on "user" concurrency (loads, stores, fences), but exceptions were hand-waved. The authors correctly identify that the 60-year-old definition of "precise" is inadequate.

2. **Real Collaboration with Arm**: They explicitly mention discussions with Arm's Chief Architect and GIC experts. This isn't armchair theorizing—they're trying to capture actual architectural intent.

3. **Executable Semantics**: The Isla-based tooling means you can actually *run* the model as a test oracle. This is the gold standard for architecture semantics work (following the lineage of the POPL'16/POPL'18 Arm papers).

4. **Practical Relevance**: The SGI section (§7) connects to real systems code. Linux RCU and Verona's asymmetric locks depend on exactly these semantics. This isn't just academic navel-gazing.

5. **Synchronous External Aborts Kill Load-Buffering**: The observation in §4 that SEAs (synchronous external aborts) forbid LB-style reorderings is genuinely important. This has implications for programming language memory models—if your hardware can't do LB, you can use simpler compiler-side models and avoid the "out-of-thin-air" problem.

### Where It's Weak

1. **The Central Question Remains Open**: They explicitly admit they don't have a proper definition of precision for relaxed architectures. Section 6 is titled "Challenges in defining precision" and ends with "a general definition... would have to capture assumptions about the exception handler and its concurrent context." This is honest, but it means the paper is more "here's the problem and some constraints" than "here's the solution."

2. **Limited Hardware Validation**: They tested on 8 devices (AWS instances, ODROID, Raspberry Pis, Apple M2). That's reasonable, but:
   - Many tests show `0/NM` (behavior allowed but never observed)—marked with `U` in Figure 9
   - No Arm Neoverse V2 big-core results for the EL1 tests (see the `0/0` entries)
   - The Apple M2 results are sparse

3. **The GIC Model is a "Draft Sketch"**: Section 7.5 explicitly says "a draft extension." The GIC is 950 pages of specification, and they model a tiny slice. The `interrupt` relation is existentially quantified without clear constraints on when it can fire.

4. **FEAT_ExS Coverage is Theoretical**: They include the feature that disables context synchronization on exception entry/exit, but admit "this configuration is rarely encountered in practice" and they have no hardware validation for it.

5. **No Imprecise Exception Semantics**: They explicitly exclude imprecise exceptions (like asynchronous SErrors). The paper says "models that account for imprecision likely need to expose more of the microarchitectural state than we capture here." This is a significant gap for server-class systems where RAS (Reliability, Availability, Serviceability) matters.

6. **The Test Suite is Small**: 61 hand-written tests. Compare to the thousands of auto-generated tests in the original Arm relaxed-memory work. They acknowledge this: "a much larger corpus would give higher confidence, and ideally could be auto-generated."

7. **System Register Semantics are Punted**: They say "we do not try to precisely model the relaxed behaviour of system registers, but merely sufficient conditions for conservative use cases." This is a significant caveat—system registers are everywhere in exception handling.

---

## Discussion Questions

1. **The Precision Definition Problem**: The authors show that "UNKNOWN" values can leak across exception boundaries (registers, partial multi-copy-atomic writes). They claim the "ultimate architectural intent" is that precision is "sufficient to meaningfully resume execution." But this is circular—what does "meaningfully" mean formally? If an exception handler observes an UNKNOWN value and makes a decision based on it, is that a violation of precision? How would you write a formal specification that captures "the handler won't look at these values"?

2. **Synchronous External Aborts and Real Hardware**: Section 4 claims that SEAs forbid load-buffering, which would be huge for language memory models. But they also say "whether any external abort could be reported synchronously is implementation-defined, with no architected way of identifying the choice." So:
   - How does a compiler/runtime know if it can rely on this?
   - Have they tested on any server-class Arm hardware (Ampere Altra, AWS Graviton 3) that might actually implement SEAs?
   - Is there any way to query this at runtime, or must software assume the weakest model?

3. **The SGI Model and Real RCU**: The `RCU-MP` test in Figure 14 is a simplified version of Linux's actual RCU. In real Linux:
   - The acknowledgment uses a lock-protected counter, not a simple flag
   - There are multiple priority levels and nested interrupt handling
   - The `synchronize_rcu` path has multiple fallbacks depending on kernel configuration
   
   How confident are you that the simplified model captures the actual ordering requirements? What would break if the real implementation has additional memory accesses between the DSB and the SGI generation?

---

## Contextual Fit

This paper sits at the intersection of two research threads:

**Relaxed Memory Semantics**: Following the lineage of Alglave's thesis (2010), the POPL'16 Arm operational model (Flur et al.), and the POPL'18 axiomatic model (Pulte et al.). Those papers established the methodology—litmus tests, cat models, Isla tooling—that this paper extends.

**Systems Semantics**: Following the ESOP'20 instruction-fetch paper (Simner et al.) and the ESOP'22 virtual memory paper (Simner et al.). This is part of a broader project to give precise semantics to the "systems" parts of Arm-A that were previously hand-waved.

The key tension is between **architectural intent** (what Arm wants to guarantee) and **implementation freedom** (what hardware can actually do). The authors are trying to find the tightest specification that:
1. Allows all reasonable hardware implementations
2. Gives programmers enough guarantees to write correct code
3. Is precise enough to be mechanically checkable

This is the same game that the x86-TSO and C11 memory model papers played, but for a much more complex domain (exceptions + relaxed memory + system registers + interrupt controllers).

---

## The Bottom Line

This is solid foundational work that identifies a real problem and makes meaningful progress. The authors are honest about limitations—perhaps too honest, as the "open problem" framing undersells the concrete contributions (the model, the tests, the hardware validation, the SGI sketch).

**For a PhD student**: This paper is a masterclass in how to tackle a messy systems problem. Notice how they:
- Scope carefully (precise exceptions only, specific Arm-A configuration)
- Build on existing methodology (litmus tests, cat models, Isla)
- Validate with hardware *and* architectural discussions
- Explicitly state what they don't cover

The weakness is that the central definitional question remains open. If you're looking for a follow-up project, "what is a proper formal definition of precision for relaxed architectures?" is sitting right there, explicitly flagged as future work.