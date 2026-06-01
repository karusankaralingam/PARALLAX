# NeuRex: A Case for Neural Rendering Acceleration
## Forensic Architecture Analysis

---

## Q1: Whiteboard Explanation

Let me walk you through what NeuRex actually does at the hardware level.

**The Problem They're Solving:**
Instant-NGP (the target workload) replaces the massive 8-layer MLP of original NeRF with 16 multi-resolution hash tables + a tiny MLP. The hash tables store 2D feature vectors (4 bytes each), and for each sample point, you need 8 lookups per level × 16 levels = 128 hash lookups, then trilinear interpolation, then MLP inference.

**The GPU Bottleneck (Figure 6):**
On GPUs, hash encoding (ENC) takes >40% of rendering time, and it's *serialized* with MLP computation. Why? Because the hash function (Equation 2: `h = (x⊕(y·P1)⊕(z·P2)) mod T`) produces pseudo-random indices scattered across a 2MB-per-level table. Each lookup pulls a 64-byte cacheline but uses only 4 bytes — a 16× bandwidth waste. For edge devices with 256KB L2 (Xavier NX), even a single 2MB hash table causes constant off-chip thrashing.

**The NeuRex "Trick" — Restricted Hashing (Section 4.2, Figure 9):**
Instead of hashing into the entire table, NeuRex *partitions* the 3D scene into R³ subgrids (default R³=64). Each subgrid's sample points hash *only* into 1/64th of the table (a "subtable"). The key insight:

```
subgrid_id = Σ ⌊p_k · R⌋ · R^k  (Equation 3)
```

This means you can load one 32KB subtable into on-chip SRAM, process *all* points in that subgrid across *all* 16 levels, then move to the next subgrid. Off-chip access becomes sequential bulk transfers instead of random 4-byte fetches.

**The Hardware (Figure 10):**
Two engines running in parallel:
1. **Encoding Engine (EE):** IGU (Index Generation Unit) computes 8 vertex coordinates + 8 interpolation weights per point. ELU (Encoding Lookup Unit) fetches features from either:
   - *Grid Cache* (64KB): For coarse levels (L=0-7) where accesses are highly localized
   - *Subgrid Buffer* (128KB, double-buffered): For fine levels (L=8-15), holds the entire subtable
   
2. **Tensor Compute Engine (TCE):** A 32×32 systolic array (or 16× for server variant) doing fused FC layers.

**The Pipeline (Figure 8):**
While Batch₀'s features go through MLP, Batch₁'s encoding lookups happen simultaneously. This breaks the GPU's serialization.

---

## Q2: The Key Insight

**The "Magic Trick":** The hash function's randomness is a *feature* for collision avoidance but a *bug* for memory systems. NeuRex exploits the fact that **spatial locality in 3D input space can be *artificially imposed* onto hash table address space** by restricting each subgrid to a contiguous subtable partition.

This is clever because:
1. It requires **no retraining** of the hash tables — just a different modulo divisor at inference time
2. It converts random access patterns into **bulk sequential transfers** amenable to DRAM row buffer locality
3. It **decouples** the on-chip memory requirement from total table size — you only need to hold 1/R³ of each level at a time

The architectural payoff: A 128KB subgrid buffer can now serve what previously required a 2MB+ cache. Per Section 6.1, this is why NeuRex-Edge achieves 9.17× speedup over Xavier NX (with its 256KB L2), while NeuRex-Server "only" gets 2.88× over RTX 3070 (which has a larger L2 that can hold a full table).

**Secondary Insight (Figure 7):** Coarse levels (L=0-1-2) have naturally localized access due to few voxels sharing many points, while fine levels (L=13-14-15) are uniformly random. Hence two different memory structures: a small *Grid Cache* (direct-mapped, coalesces 8 vertices into 32B blocks) for coarse levels, and the *Subgrid Buffer* (simple addressed SRAM) for fine levels.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Apples-to-apples comparison with real silicon (Table 4, Figure 13):** They compare against actual GPUs (Xavier NX, RTX 3070) running the authors' heavily-optimized CUDA kernels from Instant-NGP, not some strawman. The 9.88× edge speedup is meaningful.

2. **Full RTL implementation and synthesis (Section 5):** They don't just simulate — they synthesize in 28nm and report actual area (3.14mm² for Edge, 21.37mm² for Server) and power (1.31W/6.10W). The numbers are plausible.

3. **Quality validation (Figure 15-16):** They measure PSNR degradation from restricted hashing — only 0.7-3.9% drop with default tables, recoverable with 4× larger tables. Importantly, they show *visual* comparisons (Figure 16) not just metrics.

4. **Sensitivity analysis done right (Figure 18):** They sweep batch size and grid cache size to justify their design points, showing diminishing returns past 8192 batch size and 64KB cache.

**Weaknesses:**

1. **Technology node mismatch undermines energy claims (Section 6.5):** NeuRex is synthesized at 28nm; RTX 3070 is 8nm Samsung, Xavier NX is 12nm. They acknowledge this ("it is more appropriate to infer...") but still present Figure 19 showing 15-25× energy efficiency. A 28nm→8nm scaling alone would give ~3-4× power reduction. The comparison is *directionally* correct but quantitatively misleading.

2. **Cycle-level simulator for performance, not RTL (Section 5):** While they synthesize for area/power, actual performance comes from a cycle-level simulator. They don't report correlation with RTL simulation, so timing accuracy is uncertain. The claim that SRAMs "run double-pumped at 2GHz" (Section 5) to hit bandwidth targets is aggressive — no characterization of whether this works in 28nm is provided.

3. **Workload diversity is narrow (Table 3):** All NeRF scenes use the same 16-level, 2¹⁹-entry-per-level configuration from Instant-NGP defaults. No evaluation with different hash table sizes (except the 4× larger quality experiment), different level counts, or different feature dimensions. The SDF/Gigapixel results (Figure 21) are also this same configuration.

4. **Training is out of scope:** They explicitly only evaluate *inference*. For NeRF, training is often the dominant cost (hours vs. milliseconds per frame). The restricted hashing backprop implications aren't discussed.

5. **The GPU pipelining counterargument (Figure 20) is unconvincing:** They claim restricted hashing on GPUs doesn't help because CUDA can't overlap kernels well. But they use `cudaMemcpyAsync` as the gold standard — modern GPUs with CUDA streams *can* overlap kernels if designed for it. The claim that "hardware resources are limited" (Section 6.6) deserves more rigorous profiling.

---

## Q4: What the Authors Didn't Tell You

1. **The Grid Cache is weird and probably fragile (Figure 12):** It's a direct-mapped cache with a 26-bit tag containing: 1-bit valid, 18-bit gid MSBs, 4-bit level ID, 3-bit "filled counter." The 3-bit counter tracks how many of 8 vertex entries have returned from DRAM. This means a GC miss generates **8 separate 64-byte DRAM requests** (but only uses 4 bytes each), then coalesces them on return. They don't analyze GC miss rate, replacement policy effects, or the latency penalty of this 8-request fill sequence. The "request buffer" holding "64 addresses and 64 merged requests per address" (Section 5) is doing serious bookkeeping.

2. **Subgrid Buffer bank conflicts are hand-waved (Section 4.5):** They admit bank conflicts occur when multiple vertices map to the same bank, but claim "the overall rendering time does not noticeably increase" because encoding overlaps with MLP. This assumes perfect overlap, which only works if MLP is the bottleneck — but Figure 6 shows ENC ≥ MLP in most cases. A conflict analysis is missing.

3. **The IGU area is suspiciously large (Table 4):** The Index Generation Unit is 4.80mm² (Server) / 0.60mm² (Edge) — over 22% of total area. For what should be simple integer/FP operations (Equations 2-4: XOR, multiply, mod, subtract), this seems high. They don't break down the 64/8 "compute units" internally or explain the pipelining depth that justifies this.

4. **Memory bandwidth assumptions are buried:** NeuRex-Edge uses LPDDR4-3200 (25.6 GB/s peak); NeuRex-Server uses HBM2 (256+ GB/s). The subgrid buffer fill time must be hidden by on-chip compute, but they never quantify whether this holds for all workloads. For the Fox dataset (1920×1080), they process ~2M points, meaning ~31K points per subgrid with R³=64. If fine-level subtables are 32KB and you're loading them every batch, that's constant streaming pressure.

5. **The PSNR comparison uses different training (Section 6.2):** "Ours-LT" uses 4× larger tables trained *from scratch* — it's not just a runtime change. So the quality recovery requires retraining, which contradicts the "no retraining needed" convenience claim.

6. **No discussion of hash collision rate changes:** Restricted hashing changes the effective table size per subgrid. For fine levels where collisions already occur, restricting to 1/64th the table mathematically *increases* collision rate by up to 64×. They show PSNR doesn't drop much, but this is scene-dependent and could fail badly on complex geometry concentrated in one subgrid.

7. **The systolic array utilization claim (Section 6.1) needs evidence:** They claim TCE "achieves higher compute utilization" than GPU tensor cores due to layer fusion, but don't report actual utilization numbers. The MLP is tiny (32×64, 64×64, 64×64, 64×3), so even with fusion, the 32×32 systolic array is processing 32-wide vectors through a 3-layer pipeline — what's the actual MAC efficiency?