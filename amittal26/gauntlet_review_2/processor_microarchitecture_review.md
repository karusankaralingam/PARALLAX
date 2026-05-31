# Deconstruction: "Precise exceptions in relaxed architectures" (ISCA '25)

## The "No-BS" Summary

This paper tackles a problem that's been hiding in plain sight for decades: **what does "precise exception" actually mean when your processor doesn't execute instructions in order?**

The classic definition from the IBM System/360 era says an exception is precise if the processor state looks like you executed everything before the exception and nothing after. Beautiful. Simple. **Completely inadequate for modern out-of-order machines with relaxed memory models.**

The authors (a Cambridge/Edinburgh/Aarhus collaboration with Arm's blessing) do three things:
1. Catalog the actual relaxed behaviors that can occur across exception boundaries on Arm-A (loads/stores reordering across `SVC`/`ERET`)
2. Build a formal axiomatic model extending the existing Arm memory model to handle exceptions
3. Identify that **nobody has actually defined what "precise" means in this context**, and the problem is genuinely hard

This isn't a performance paper. There's no "47% IPC improvement." It's a **semantics paper** that says: "Hey, the contract between hardware and software for exceptions is underspecified, and here's what we found when we actually looked."

---

## The Core Mechanism: A Whiteboard Explanation

### The Problem Setup

Imagine you're a kernel developer handling a page fault. The textbook says: "When the exception fires, the processor state is exactly as if you executed instructions 1, 2, 3... up to the faulting instruction, and nothing after." You fix the page table, return, and the faulting instruction re-executes. Clean.

**But modern processors lie to you.**

Here's what actually happens on an out-of-order core:

```
Time →
                    [Instruction 5 load starts]
[Instruction 1]     [Instruction 3 store commits]
    [Instruction 2]     [Instruction 4 - EXCEPTION!]
                            [Instruction 6 load completes speculatively]
```

The processor is juggling dozens of instructions simultaneously. When instruction 4 faults, what's the "state"? Instruction 5's load might have already read from memory. Instruction 3's store might not have propagated to all cores yet.

### The Key Insight: Context Synchronization

The paper's central observation is that **exception entry/exit on Arm acts like an ISB (Instruction Synchronization Barrier)** by default. This is called "context synchronization."

What does this mean practically?

```
Before exception:  [Store X=1] [Load Y] [SVC #0]
                        ↓         ↓        ↓
                    can reorder with each other
                    
After exception:   [SVC #0] → [Handler code]
                       ↑
                   Context sync: nothing after can start
                   until exception is "taken"
```

The magic trick: **exceptions cannot be taken speculatively**. The processor won't jump to your exception handler until it's *committed* to taking that exception. This gives you a synchronization point.

But—and here's the relaxed part—**loads and stores can still reorder across that boundary**. The test `MP+svceret+addr` in Figure 4 shows this: a store before `SVC` and a load in the handler can appear to execute out of order to other threads.

### The Forwarding Wrinkle

Even weirder: store-to-load forwarding works across exception boundaries. If you write `X=1` before an exception, and read `X` in the handler, you can see your own write via forwarding—even though from another thread's perspective, that write hasn't "happened" yet.

---

## The Critique: Strengths & Weaknesses

### Why It Got Into ISCA

1. **Genuine gap in the literature.** I've been reading architecture papers for 25 years, and I can't point to a prior paper that formally addresses this. The System/360 definition has been copy-pasted into every architecture manual since 1964, and nobody updated it for relaxed memory.

2. **Collaboration with Arm architects.** This isn't academics guessing—they explicitly state discussions with "Arm Chief Architect" and GIC experts. The model has buy-in from the people who define the architecture.

3. **Executable formal model.** They extended Isla (an SMT-based tool) to actually run their axiomatic model against litmus tests. This isn't hand-waving; you can check their claims mechanically.

4. **Real-world implications.** The RCU synchronization mechanism in Linux (Section 7) depends on exactly these semantics. Getting this wrong means kernel bugs.

5. **The synchronous external abort observation (Section 4).** This is subtle but important: if loads can generate synchronous exceptions (like memory errors), then load-buffering (LB) behavior is forbidden. This has implications for programming language memory models—it means some Arm implementations are "stronger" than the architectural minimum, which affects compiler optimizations.

### Where It's Weak (Or At Least, Incomplete)

1. **No performance analysis whatsoever.** This is a semantics paper, not a microarchitecture paper. They don't discuss:
   - What's the cost of context synchronization on exception entry?
   - How do real implementations (Neoverse N1/V1/V2, Cortex-A73, etc.) actually handle this?
   - Are there performance cliffs if you disable context sync (FEAT_ExS)?

2. **Limited hardware testing.** They tested 8 platforms (AWS Graviton variants, ODROID, Raspberry Pi). That's decent, but:
   - No Apple M-series silicon (they mention M2 but results are sparse)
   - No Qualcomm Snapdragon
   - No Ampere Altra
   - Server workloads on cloud instances may not stress these corner cases

3. **The "precision" problem is identified but not solved.** Section 6 essentially says "we don't know how to define this properly." The paper is honest about this, but it means the central question remains open. They characterize the problem; they don't solve it.

4. **GIC modeling is a sketch.** Section 7 admits the Generic Interrupt Controller is 950 pages of specification, and they only model "a simple baseline." For real IPI-based synchronization (like `sys_membarrier`), you'd need much more.

5. **No discussion of x86 or RISC-V.** They claim the challenges "also appear in other, similarly relaxed, architectures" but don't demonstrate this. x86-TSO is much stronger than Arm; does this problem even exist there? RISC-V has its own memory model (RVWMO)—how does it handle exceptions?

6. **The UNKNOWN values escape hatch.** The Arm manual says certain register/memory values become "UNKNOWN" on exception. The paper acknowledges this but doesn't formalize it. This is a significant gap—if values can become UNKNOWN, your formal model needs to account for that non-determinism.

---

## Discussion Questions

### For Testing Your Understanding

1. **The paper shows that `MP+dmb.sy+svc` (message-passing with a DMB and SVC) is allowed on ODROID but forbidden on Raspberry Pi (Figure 9 shows 0 observations on Pi devices). Why might this be?**
   
   *Hint: Look at Section 3.2.2 about speculative exception entry. The Pi's simpler in-order cores (Cortex-A53 on Pi3) may not exhibit the same reordering as the out-of-order A73 on ODROID.*

2. **Section 4 argues that synchronous external aborts (SEAs) on loads forbid load-buffering (LB). Walk through why this is true.**
   
   *The key: if a load can generate an SEA, then instructions program-order-after that load are "speculative" until the load completes. Writes can't propagate speculatively. So in LB, where Thread 0 does `R x; W y` and Thread 1 does `R y; W x`, neither write can propagate until its preceding read completes—breaking the cycle that enables LB.*

3. **The RCU-MP test (Figure 14) relies on interrupt masking (`DAIFSet`/`DAIFClr`) to create a "read critical section." What happens if the interrupt is taken *during* the critical section? Why doesn't this break the synchronization?**
   
   *The masking prevents the interrupt from being delivered while masked. The interrupt remains pending but isn't taken until `DAIFClr`. This is the whole point—the critical section is "atomic" with respect to the SGI-based synchronization.*

### For Challenging the Authors

4. **You claim context synchronization on exception entry acts like ISB. But ISB is a *software* barrier with defined semantics. Exception entry is a *hardware* event. How do you know implementations actually provide ISB-equivalent ordering, rather than something weaker that happens to pass your tests?**

5. **The paper doesn't address nested exceptions. If an exception handler itself takes an exception (e.g., a page fault in the kernel), how do your ordering guarantees compose? Does context synchronization on the inner exception provide ordering with respect to the outer exception's pre-boundary instructions?**

6. **Your model assumes single-copy atomicity for most accesses. But Arm allows non-single-copy-atomic accesses (e.g., misaligned accesses, LDP/STP). How do exceptions interact with partially-completed multi-copy accesses? The paper mentions UNKNOWN values but doesn't model them.**

---

## Contextual Fit: Where This Sits in the Literature

This paper is part of a broader Cambridge/INRIA effort to formalize relaxed memory models:

- **Foundational work**: Sarkar et al. on POWER (2011), Alglave et al. on x86-TSO (2010), Flur et al. on ARMv8 (2016)
- **The "user" Arm model**: Pulte et al. (POPL 2018) gave the axiomatic model this paper extends
- **Systems extensions**: Simner et al. on instruction fetch (ESOP 2020), virtual memory (ESOP 2022)

This paper is the **exceptions chapter** of that ongoing project.

It also connects to:
- **The out-of-thin-air problem** in programming language semantics (Batty et al., 2015). Section 4.2 notes that forbidding LB (via SEAs) sidesteps this problem—a surprising connection between hardware error handling and PL theory.
- **RCU formalization** (Alglave et al., ASPLOS 2018), which this paper extends to cover the SGI-based implementation.

---

## The Bottom Line

This is a **foundational semantics paper**, not a systems or microarchitecture paper. It won't help you build a faster core. But if you're:
- Writing an OS kernel that handles exceptions
- Building a formal verification tool for concurrent systems code
- Designing a new architecture and need to specify exception behavior

...then this paper identifies a real gap and provides the first serious attempt to fill it.

The honest admission that they can't fully define "precision" is actually a strength—it's better to clearly state an open problem than to paper over it with hand-waving. The 60-year-old definition is broken, and now we know it.