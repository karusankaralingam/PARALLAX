## Q1: Whiteboard Explanation

Let me break down what Cambricon-SR actually does, as if I'm sketching this on a whiteboard.

**The Problem:** Neural Scene Representation (NSR) takes 2D photos and learns a 3D representation. The key bottleneck is the *encoding stage*, where you look up features from a hash table for sampled 3D points. This involves millions of fine-grained, irregular memory accesses to an encoding table — a nightmare for memory systems.

**The Core Observation:** Most entries in that encoding table are useless. The paper shows that 80%+ of table entries can be pruned without hurting quality (Figure 4). But here's the catch: the hash function maps 3D coordinates to table indices *irregularly*, so you don't know which lookups will hit pruned entries until you compute the hash.

**The Three-Part Solution:**

1. **ST-NSR Algorithm (Section 3):** They train with a "dense table" off-chip for gradient accumulation, but use a "sparse table" on-chip for forward/backward passes. Entries below a threshold are pruned. The threshold updates dynamically using top-k selection.

2. **Sparse Index Unit (Section 4.4):** Before hitting the sparse table, you need to filter out requests targeting pruned entries. The naive approach — 2048 parallel random SRAM accesses to a bitmap — causes routing nightmares. Their trick: **sequential SRAM reads** combined with **parallel address matching**. They read the bitmap sequentially, then match pending requests against the current bitmap segment. This converts irregular random access into regular sequential access.

3. **Dynamic Shared Buffer (Section 4.5):** After fixing encoding, the MLP stage becomes the bottleneck. Each MLP unit needs a buffer for ray data, but most rays are small. Instead of giving each unit its own buffer (sized for worst-case), they share buffers across 32 MLP units, reducing buffer requirements by 85.3% and allowing 4× more MLP units.

**The Dataflow (Figure 7):** Forward/backward stages run on-chip with the sparse table. The update stage reads the dense table from DRAM, but only for changed entries (sparse gradients mean only ~11% of DT needs updating).

---

## Q2: The Key Insight

The fundamental insight is a **sparsity-locality inversion**: NSR's encoding table sparsity is algorithmically exploitable, but the *hardware challenge* isn't the sparsity itself — it's that you can't predict which accesses are valid until after computing irregular hash indices.

The clever part is recognizing that filtering invalid requests *before* they hit the sparse table array requires high-throughput bitmap lookups, but those lookups are also irregular (same hash-function-driven randomness). The paper's key technical insight is that **you can convert irregular random bitmap access into regular sequential access** by accepting higher latency in exchange for eliminating bank conflicts entirely.

The Sparse Index Unit (Section 4.4) essentially inverts the problem: instead of asking "is address X valid?" 2048 times in parallel (irregular), they ask "which pending addresses match the current bitmap segment?" as they sequentially scan through the bitmap. This is fundamentally the same computation but with completely different memory access patterns.

This is elegant because it exploits a property of the workload that isn't obvious: the requests can *tolerate* some latency (they're batched), but they absolutely cannot tolerate throughput loss from bank conflicts.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Reasonable Dataset Coverage:** They evaluate on 8 scenes spanning synthetic (Ficus, Hotdog, Mic from NeRF-Synthetic), small real-world (Fox), and large-scale real-world (Bonsai, Room, Counter, Kitchen from Mip-NeRF-360). This is better than many NSR papers that only use synthetic scenes. Section 5.1.2 correctly notes that the latter four are "larger-scale real-world datasets."

**2. Multiple Algorithm Coverage:** Testing Instant-NGP, Zip-NeRF, and K-Planes (Section 5.1.2) shows the architecture isn't algorithm-specific. This includes both hash grid encoding (Instant-NGP, Zip-NeRF) and multi-planes encoding (K-Planes).

**3. Honest Ablation Study:** Figure 18 and Figure 19 clearly decompose contributions. The ablation shows SIU contributes 3.07× speedup, while Sparse Update Unit and Dynamic Shared Buffer contribute 1.24× and 1.36× respectively. This transparency is commendable.

**4. Quality-at-Fixed-Time Comparison:** Table 1 shows PSNR/SSIM at the same modeling time (0.1 seconds per scene), which is the right metric for real-time applications. They correctly note that more iterations compensates for per-iteration quality loss (Figure 13).

### Weaknesses

**1. The 1259× GPU Speedup is Misleading:** This number compares an A100 running general-purpose code against a custom accelerator. The paper admits in Section 3.3 that their GPU implementation of ST-NSR only achieves 1.19× overall speedup — meaning most of their "1259× speedup" comes from being a custom chip, not from the sparsity algorithm. A fairer comparison would normalize by area or power. The A100 is a 826mm² chip at 7nm; Cambricon-SR is 234.86mm² (Table 2). Per-mm² performance would be more informative.

**2. Cambricon-R Comparison is Self-Referential:** Cambricon-R is from the same research group (same authors: Song, Hu, Chen, etc.). Comparing against your own prior work is necessary but insufficient. Where are comparisons to Instant-NeRF [55], Instant-3D [24], or NeuGPU [40]? Section 2.2 discusses these but Section 5 doesn't compare against them. The excuse that they're "designed for edge devices" (Section 2.2) is valid, but NeuGPU has actually taped out — that's a real comparison point.

**3. Cherry-Picking Sparsity Rates Per-Scene:** Figure 4 and Section 3.2 reveal that sparsity rates are *manually tuned per scene* (86%, 88%, 96%, etc. for forward; 80%, 85%, 95%, etc. for backward). This is fine for a research paper but raises questions about deployment: do you need to profile each new scene? The paper doesn't discuss how to automatically select sparsity thresholds for unseen scenes.

**4. The "5 Seconds on A100" Claim Needs Context:** Section 1 states "it takes at least 5 seconds to compute the representation of a scene on A100." This is for training to PSNR 25. But the GPU baseline in Figure 14 shows times of 10^4 to 10^5 ms for large scenes — that's 10-100+ seconds, not 5 seconds. The "5 seconds" appears to be for small synthetic scenes only.

**5. Off-chip Access Increase is Understated:** Figure 17 shows Cambricon-SR has *more* off-chip access than Cambricon-R (visible in the bar graph). Section 5.2.4 acknowledges this but buries it: "more off-chip memory accesses are required for Cambricon-SR compared with Cambricon-R." This directly trades off against the energy claims.

**6. Energy Breakdown Hides Inefficiency:** Section 5.2.3 admits SIU wastes 52.8% of its energy on "unused accesses" from sequential bitmap scanning. This is 8.85% of total chip energy wasted. The paper frames this as acceptable ("worthwhile trade-off"), but this is a limitation of the approach.

**7. Area Scaling Not Discussed:** The chip is synthesized at 45nm and scaled to 7nm (Section 5.1.1). Scaling CAM (14.29% of chip area per Section 4.2) is notoriously problematic — CAM area doesn't scale as well as SRAM or logic. The paper doesn't discuss this.

---

## Q4: What the Authors Didn't Tell You

**1. The Dense Table is Still 128MB Off-Chip:** The abstract and introduction emphasize "sparse encoding table," but Section 4.1 reveals the dense table must be maintained off-chip "to accumulate gradients" because "it must be stored off-chip." The sparsity reduces *what you access*, not *what you store*. For each training iteration, you're still touching the 128MB dense table during the update stage.

**2. Threshold Update is Approximated:** Section 4.1 quietly admits they "use only half of the DT" to compute the sparsity threshold and use "the sparsity threshold from the last iteration." They claim "negligible impact" (less than 0.1 PSNR), but this means the sparsity pattern is always one iteration stale and computed from incomplete data.

**3. CAM is a Major Cost:** Section 4.2 states CAM area is 33.56mm² or 14.29% of total chip area. This is a significant overhead not emphasized in the abstract. CAM also has higher power density than SRAM.

**4. The Technique Fails for 3DGS:** Section 2.1 states "In this paper, we focus on NeRF-based NSR algorithms." 3D Gaussian Splatting [19] is explicitly excluded, yet 3DGS is increasingly dominant in practice for its speed. The sparse encoding table approach may not apply to 3DGS since it uses explicit Gaussian parameters, not hash tables.

**5. Sparsity Comes with Training Overhead:** Algorithm 1 shows each iteration requires: (a) sparse table lookup, (b) gradient computation, (c) dense table update, (d) top-k threshold computation, and (e) sparse table regeneration. The overhead of steps (d) and (e) isn't clearly broken down.

**6. The 80% Sparsity is Not Uniform:** Figure 3(a) shows sparsity varies from ~80% to ~97% depending on table size and scene. The "more than 80%" claim uses the worst case. For some configurations (256MB table), sparsity exceeds 95%.

**7. Quality Loss is Cumulative:** Section 3.2 claims "impact on PSNR is less than 0.5" for forward sparsity and separately "less than 0.5" for backward sparsity. But Figure 4 tests these in isolation. Combined, the quality loss could be additive.

**8. Latency vs. Throughput Trade-off:** The SIU design explicitly trades latency for throughput. Section 4.4.1 notes the "straightforward approach" has "extremely high latency." Their solution reduces this but doesn't eliminate it. The paper reports throughput but not per-request latency.