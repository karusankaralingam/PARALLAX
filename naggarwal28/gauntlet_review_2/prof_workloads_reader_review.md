# Prof. Bench's Evaluation Methodology Audit

*adjusts glasses and pulls up the paper*

Alright, let's dissect this C³ paper's experimental methodology. The claims are bold—"minimal performance overhead" of 3.8-25.4%—but as I always say, **the devil lives in the experimental setup**.

---

## 1. Methodology Audit: The Simulation Environment

### The Baseline Problem

They use **gem5 in syscall emulation mode** with Ruby for cache coherence. This is standard for architecture research, but let me flag several concerns:

**First**, look at Table III carefully:
- They simulate **8-30 cores** with **4MB shared LLC**
- They calibrate core counts to "match approximately the same MPKI as observed on real hardware"

This is a reasonable approach, but here's the catch: **they're scaling down everything to make simulations tractable**. The paper admits this:

> "To make simulations tractable in a reasonable timeframe, we use small input sizes and scale the cache sizes and number of cores"

This is a classic simulation compromise, but it raises questions about whether their coherence traffic patterns actually reflect real CXL deployments at scale.

**Second**, they use **Garnet** (an on-chip network model) to simulate CXL interconnects:

> "Although Garnet was originally designed as an on-chip network and real CXL systems communicate over a PCIe fabric, Garnet is tailored for coherence protocols"

This is a methodological red flag. CXL runs over PCIe, which has fundamentally different characteristics—credit-based flow control, TLP packetization, completion timeouts. They're abstracting away the transport layer entirely.

---

## 2. The "Gotcha" Graphs

### Figure 10: The Variance Problem

Look at Figure 10 carefully. The **mean** overhead is 5.5%, but individual benchmarks vary from **3.8% to 29.4%**. That's nearly an order of magnitude spread.

The worst performers:
- `lu-ncont`: ~25-29% overhead
- `barnes`: ~19-25% overhead  
- `histogram`: ~19-25% overhead

Now look at Figure 11—they actually explain why. These workloads have **high cross-cluster coherence traffic** with contended cache lines. The paper admits:

> "We detected some cache lines are hot-spots for both read and write across the two clusters, in CXL-sensitive applications"

This is honest, but it also reveals that **C³'s overhead is highly workload-dependent**. For applications with significant cross-cluster sharing (which is... the whole point of CXL shared memory), you're looking at 20-30% overhead, not 5%.

### Figure 9: The MCM Comparison

The left side shows 22-39% degradation when switching from ARM MCM to TSO. But wait—**this isn't C³'s overhead**. This is the inherent cost of enforcing stronger memory ordering. The paper conflates two different things:

1. The cost of C³'s protocol bridging
2. The cost of different MCMs

The "mixed" configuration (ARM/TSO) shows only 2.6-12.7% degradation, which they present as a win. But this is comparing against the **all-TSO** baseline, not the all-ARM baseline. The framing is clever but potentially misleading.

---

## 3. The Missing Data

### Where's the Sensitivity Study on CXL Latency?

Table III shows they use **70ns link latency** to achieve ~400ns round-trip CXL memory access. But CXL latency varies significantly:

- CXL 1.1/2.0 devices: ~150-300ns additional latency
- CXL 3.0 with switches: potentially 400-600ns
- Multi-hop CXL fabrics: could exceed 1μs

**I would have loved to see a sensitivity study on link latency.** Does C³'s overhead scale linearly with CXL latency? Or does the protocol translation overhead become negligible at higher latencies? This is critical for understanding real-world applicability.

### Where's the Scalability Analysis?

They test **2 clusters** with 8-30 cores total. But CXL 3.0 supports up to **16 hosts** sharing memory. What happens to C³'s overhead with:
- 4 clusters?
- 8 clusters?
- Mixed architectures (x86 + ARM + GPU)?

The paper's title promises "Heterogeneous Architectures" but only evaluates CPU-to-CPU scenarios. No GPUs, no FPGAs, no accelerators—despite mentioning them repeatedly in the introduction.

### Where's the Directory Contention Analysis?

Figure 11 hints at convoy effects from CXL directory blocking states. But they don't quantify:
- Directory occupancy rates
- Queue depths at the CXL directory
- Impact of directory associativity

---

## 4. The Benchmark Selection: Cherry-Pick Check

They use **33 benchmarks** from PARSEC, Splash-4, and Phoenix. These are standard parallel benchmark suites, which is good. But let me note what's **missing**:

### No Datacenter Workloads
- No key-value stores (Redis, Memcached)
- No databases (MySQL, PostgreSQL)
- No ML inference workloads
- No graph analytics

These are the actual use cases for CXL memory pooling. PARSEC's `blackscholes` and `swaptions` are not representative of datacenter memory access patterns.

### No Irregular Memory Access Patterns
- No pointer-chasing workloads
- No sparse matrix operations
- No graph traversals

These stress coherence protocols differently than the regular access patterns in scientific computing benchmarks.

### The "vips" Anomaly

Look at `vips` in Figure 11—it shows only **2.2% more miss cycles** with CXL. Why? Because it has minimal cross-cluster sharing. This is the **best-case scenario** for C³, not the common case.

---

## 5. The Baseline Validity

### Is MESI-MESI-MESI a Fair Baseline?

They compare against a **homogeneous hierarchical MESI** system where C³ "functions as a passive device, simply forwarding inter-cluster coherence requests one-to-one."

But this isn't how real multi-socket systems work. Intel uses **MESIF** with home agent protocols. AMD uses **MOESI** with probe filters. A truly fair baseline would be:
- Intel's actual multi-socket coherence (UPI-based)
- AMD's Infinity Fabric coherence

Comparing against textbook MESI makes C³ look better than it might against production coherence implementations.

### The CXL Protocol Overhead

Section VI.C.1 reveals something important:

> "CXL requires 6 remote message delays when the owner is dirty (4 when clean) with 2 blocking transient states at the directory"

vs. MESI's 3 message delays. This is a **2x protocol complexity increase** inherent to CXL, not C³. The paper correctly identifies this but doesn't separate C³'s overhead from CXL's inherent overhead.

---

## 6. The Litmus Test Validation

The formal verification approach is solid:
- Murφ model checking for FSM correctness
- herd7-generated litmus tests
- 100,000 iterations per test

But I have concerns:

### Sample Size for Rare Events

Memory consistency bugs are often **extremely rare**—they might occur once in 10⁹ executions. Running 100,000 iterations is standard but may not catch subtle bugs. Did they run with different random seeds? Different thread interleavings?

### The ArMOR Fence Removal

They use ArMOR to "remove fences that are no longer required when combining with the stronger MCM." This is theoretically sound, but it assumes ArMOR's fence analysis is correct for their specific protocol combinations. Any bugs in ArMOR would propagate to their validation.

---

## 7. Discussion Questions for the Student

1. **The Real-World Gap**: If we ran C³ on actual Google Search query traces instead of PARSEC, do you think the 5.5% average overhead would hold? Consider that search workloads have:
   - Highly irregular memory access patterns
   - Frequent small allocations
   - Heavy pointer chasing in inverted indices

2. **The Scalability Question**: The paper tests 2 clusters. CXL 3.0 supports 16 hosts. How would you expect C³'s overhead to scale? Linear? Superlinear? Why?

3. **The GPU Elephant**: They mention GPUs repeatedly but never evaluate them. RCC (Release Consistency Coherence) has fundamentally different semantics than MESI. What additional challenges would arise when bridging GPU coherence to CXL?

4. **The Latency Hiding Question**: At 400ns CXL latency, protocol translation overhead might be negligible. But what if CXL latency drops to 100ns with future technology? Would C³'s overhead become the bottleneck?

---

## Summary Verdict

**Strengths:**
- Solid formal verification methodology
- Honest about limitations (they explain the CXL protocol overhead clearly)
- Open-source artifact with reproducibility

**Weaknesses:**
- Simulation-only evaluation with significant abstractions
- Benchmark selection doesn't reflect datacenter workloads
- Missing scalability and sensitivity studies
- No heterogeneous accelerator evaluation despite the title

**The Bottom Line:** The 5.5% average overhead is likely **optimistic** for real CXL deployments. For workloads with significant cross-cluster sharing—which is the primary use case for CXL shared memory—expect 20-30% overhead. The methodology is sound for a first paper in this space, but the claims should be interpreted with these caveats in mind.