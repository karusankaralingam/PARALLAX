# CoopRT: A Toolsmith's Dissection

*Adjusts glasses, opens terminal*

Alright, let's talk about what's actually under the hood here. This is an ISCA '25 paper, so the bar is high, but simulation is still simulation—let's see how well they've built their approximation of reality.

---

## 1. Tooling Breakdown: Vulkan-sim 2.0

The authors built their evaluation on **Vulkan-sim 2.0**, which is itself built atop **GPGPU-Sim**. This is a reasonable choice for RT unit research—it's one of the few publicly available simulators that models the ray tracing pipeline with any fidelity.

**What Vulkan-sim is good for:**
- Cycle-level modeling of the RT unit's warp buffer, memory scheduler, and intersection test pipeline
- Functional BVH traversal with realistic memory access patterns
- Integration with the broader GPU memory hierarchy (L1/L2/DRAM)

**What Vulkan-sim is *not* good for:**
- It's not RTL-validated. There's no silicon correlation study.
- The RT unit model is based on reverse-engineering and public documentation, not actual hardware specifications
- Power modeling comes from **GPUWattch**, which was designed for GPGPU workloads, not RT-specific power characteristics

**The critical admission (Section 6.1):**
> "Vulkan-sim performs the actual BVH traversal process in the functional simulator, and passes a list of BVH node addresses for each thread to the timing simulator."

This is a **trace-driven hybrid approach**. The functional simulator pre-computes the traversal path, then the timing simulator replays memory accesses. This is standard practice, but it creates a fundamental tension with their contribution.

---

## 2. The Modeling Risk: Trace-Driven Simulation for a Dynamic Technique

Here's where I get nervous. CoopRT fundamentally changes *which nodes get visited* and *when*. The paper acknowledges this:

> "The functional simulator assumes a single thread traverses the BVH tree in DFS fashion for a given ray, and therefore generates the list of nodes accordingly... However, when multiple threads traverse the BVH together, it is impossible to know beforehand which nodes will be eliminated."

Their solution:
> "We resolve this issue by not doing any node eliminations in the functional simulator, and instead, passing the thit values of each node to the timing simulator."

**Translation:** They over-approximate the node list (no early termination in functional sim), then do runtime culling in the timing simulator based on `min_thit` comparisons.

**The risk:** This approach is *conservative* for correctness but potentially *optimistic* for performance. Why?

1. **Memory access ordering changes:** When helper threads steal work, they may access nodes in a different order than the pre-computed trace. If the timing simulator doesn't model cache replacement effects accurately under this new access pattern, the hit rates could be wrong.

2. **Contention modeling:** They show L1 miss rates *increase* with CoopRT (Figure 16), which is expected. But is the contention model in GPGPU-Sim accurate for this access pattern? GPGPU-Sim's cache model is well-validated for GPGPU workloads, but RT workloads have very different spatial locality characteristics.

3. **The `min_thit` synchronization:** In their model, helper threads update the main thread's `min_thit` through a crossbar. They claim "it is logically impossible for more than one thread to find a primitive hit for a given ray at the same cycle." This is true *given their assumptions* about response FIFO throughput, but it's a modeling constraint, not a physical law.

---

## 3. The "Impossible Physics" Check

Let's look at their configuration (Table 1):

| Parameter | Value |
|-----------|-------|
| Core Clock | 1365 MHz |
| L1 Data Cache | 64KB, 20 cycles |
| L2 Cache | 3MB, 160 cycles |
| Memory Clock | 3500 MHz |

**The L1 latency of 20 cycles at 1365 MHz is ~14.6 ns.** For a 64KB cache, this is plausible but on the aggressive side for a modern GPU. Real RTX 2060 L1 latency is likely 28-32 cycles based on microbenchmarking studies.

**The L2 latency of 160 cycles is ~117 ns.** This is reasonable for a shared L2 with crossbar traversal.

**What's missing:**
- No mention of **DRAM refresh** modeling. For memory-bound workloads like RT, refresh interference can add 5-10% to average memory latency.
- No discussion of **NoC contention** under high bandwidth utilization. They show DRAM utilization increasing from 44% to 85% (Figure 18), but the interconnect model in GPGPU-Sim is relatively simple.
- **Warp scheduling policy** isn't specified. GTO? LRR? This matters for memory-bound workloads.

---

## 4. The Configuration Table Deep Dive

**RT Unit Warp Buffer Size: 4 entries**

This is the key parameter they're optimizing around. They show that CoopRT with 4 entries outperforms baseline with 32 entries (Figure 13). But here's the thing:

- They don't cite a source for why 4 is the "right" baseline
- Real RTX hardware likely has more sophisticated warp buffering
- The comparison is somewhat self-serving: "Our technique with small buffers beats naive scaling with large buffers"

**BVH Construction: Embree 3.14**

This is a CPU-side BVH builder. The quality of the BVH (tree depth, node utilization) significantly affects traversal performance. They report tree depths of 7-18 across scenes (Table 2), which is reasonable for Embree's SAH-based construction.

**But:** Real GPU drivers use GPU-accelerated BVH builders with different heuristics. The traversal characteristics could differ.

---

## 5. Artifact Availability: The Good News

**They have a Docker image.** This is excellent. The artifact appendix (Section A) provides:
- Source code for modified Vulkan-sim
- Shell scripts for reproduction
- Raw simulation outputs
- DOI: `10.5281/zenodo.15103378`

**The bad news:**
- Simulations take "one week if run in parallel"
- They recommend 32GB+ RAM
- The Docker image is 30GB

This is honest about the computational cost, but it means most reviewers probably ran the figure-generation scripts on pre-computed results rather than re-running simulations.

---

## 6. Area Estimation: FreePDK45

They synthesized their RTL using **FreePDK45**, a free 45nm process design kit. This is standard for academic area estimation, but:

- 45nm is ancient. Scaling to 7nm or 5nm (where modern GPUs live) is non-trivial.
- They report 13,347 µm² for the combinational logic, equivalent to ~2,200 flip-flops.
- The "3% of warp buffer" comparison is reasonable but assumes the warp buffer is the dominant area in the RT unit (which may not be true).

**What they didn't do:**
- No timing closure analysis
- No power estimation for the new logic (they use GPUWattch for the whole GPU, but the new crossbar and LBU aren't characterized)
- No discussion of routing congestion

---

## Discussion Question for the Student

**How would you design a microbenchmark to verify their claim that helper threads don't introduce cache pollution?**

Consider:
1. The paper shows L1 miss rates *increase* with CoopRT (Figure 16). Is this pollution or just more accesses?
2. What access pattern would isolate the effect of helper threads on the main thread's working set?
3. Could you construct a synthetic BVH where cooperation is *harmful* due to cache interference?

The key insight is that their "cooperation" changes the temporal ordering of memory accesses. A microbenchmark should control for:
- BVH structure (depth, branching factor)
- Ray coherence (how similar are rays in a warp?)
- Cache capacity relative to working set

If you can find a case where CoopRT *hurts* performance, you've found the boundary of their technique's applicability—and that's valuable information the paper doesn't fully explore.

---

## Summary Verdict

**Simulation quality: B+**

They've done reasonable work within the constraints of Vulkan-sim, and they're transparent about the trace-driven limitations. The artifact availability is excellent. But the lack of RTL validation, the use of an outdated process node for area estimation, and the absence of DRAM refresh modeling leave room for skepticism about the absolute numbers.

**The 2.15x geometric mean speedup is probably directionally correct, but I'd want ±20% error bars before betting silicon on it.**