# Paper Deconstruction: zkSpeed — Accelerating HyperPlonk for Zero-Knowledge Proofs

Let me be direct: this paper has nothing to do with N:M structured sparsity or dynamic pruning accelerators. This is a **Zero-Knowledge Proof (ZKP) accelerator** for cryptographic workloads. But since you've asked me to read it, I'll switch hats and give you a rigorous deconstruction of what's actually here—because the underlying hardware design principles (streaming architectures, compute vs. memory boundedness, Pareto-optimal design space exploration) are relevant to any accelerator designer.

---

## Q1: Whiteboard Explanation

Imagine you want to prove to someone that you know a secret (like a password) without revealing the secret itself. Zero-Knowledge Proofs let you do exactly that. HyperPlonk is a specific ZKP protocol that's gaining traction because it offers:
1. **Small proofs** (~5KB vs. 8MB for competitors like Orion)
2. **Universal setup** (one-time setup works for all applications, unlike Groth16 which needs a new "trusted ceremony" per application)
3. **O(n) prover time** instead of O(n log n) via SumCheck

**The problem:** Generating these proofs is *brutally slow*—minutes to hours on CPUs for realistic applications. The prover has to do arithmetic on massive polynomials (2^24 terms) with huge bit-widths (255-381 bits).

**What zkSpeed does:** It's a custom ASIC that accelerates the entire HyperPlonk proof generation pipeline. Think of it as eight specialized "units" bolted together:

1. **MSM Unit** (Multi-Scalar Multiplication): The "heavy lifter"—computes dot products between scalars and elliptic curve points. This is compute-bound and takes ~65% of chip area (Table 5).

2. **SumCheck Unit**: The protocol's signature kernel—iteratively reduces polynomial summations. This is *memory-bound* because intermediate polynomial tables explode in size after the first round (binary values → 255-bit values, Section 4.1.2).

3. **Multifunction Tree Unit (MTU)**: A clever reusable tree structure that handles three different operations (Build MLE, MLE Evaluate, Product MLE) using the same hardware via a hybrid DFS/BFS traversal (Figure 6).

4. **FracMLE Unit**: Computes modular inverses using batched Montgomery inversion—batch size of 64 was found optimal (Figure 8).

5. **Other units**: Construct N&D, MLE Combine, MLE Update, SHA3.

**The key architectural insight:** The protocol has both compute-bound kernels (MSMs) and memory-bound kernels (SumCheck). Rather than building a general-purpose processor, they built specialized units for each kernel and connected them via a simple shared bus with streaming dataflow. The chip needs HBM3-class bandwidth (2 TB/s) to keep the SumCheck units fed.

---

## Q2: The Key Insight

**The "Delta" here is threefold:**

### 1. First Full-Protocol HyperPlonk Accelerator
Previous ZKP accelerators (SZKP, PipeZK, NoCap) targeted different protocols—Groth16 or Spartan+Orion. This is the **first to accelerate HyperPlonk end-to-end**, which matters because HyperPlonk's protocol structure (SumCheck-based with Plonk encodings) creates fundamentally different computational patterns than NTT-based protocols.

### 2. Unified SumCheck Architecture for Heterogeneous Polynomials
Section 4.1.4 reveals the real hardware trick: HyperPlonk has *three different SumCheck variants* (ZeroCheck, PermCheck, OpenCheck) with polynomials of varying degrees (Equations 3-5). Rather than building three separate units, they designed a **unified PE that shares modular multipliers across all variants**, saving 48.9% area compared to naive duplication. The key observation is that polynomials like f_z1 appear multiple times across terms—computing extensions once and reusing them avoids redundant computation (Section 4.1.1, Figure 4).

### 3. Hybrid Tree Traversal for Large Polynomials
The Multifunction Tree Unit (Section 4.3) solves a nasty problem: naive BFS traversal of tree-structured computations requires storing entire intermediate levels (128MB for 2^23 problem size). Their **hybrid DFS/BFS approach** uses DFS for upper levels (to consume intermediates immediately) and BFS for lower levels (to enable parallelism), achieving >99% PE utilization (Figure 6) while fitting in practical SRAM budgets.

### 4. Batched Modular Inversion with Optimal Batch Size
The FracMLE unit (Section 4.4) must compute modular inverses for every element of an MLE table—expensive at 509 cycles each. They use Montgomery batching with a multiplier tree to amortize costs, and prove mathematically that **batch size 64 minimizes both latency imbalance and area** (Figure 8). This is a clean optimization problem with a closed-form sweet spot.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Design Space Exploration (Figure 9)**
The authors don't just present one design—they sweep ~thousands of configurations across 7 bandwidth levels and plot global Pareto frontiers. This is rigorous methodology. The insight that "below 100mm², HBM's PHY overhead dominates so DDR5 is actually Pareto-optimal" (Section 7.1) is the kind of nuanced finding that comes from proper exploration.

**2. Honest Comparison Against Strong Baselines**
Table 4 compares against SZKP (Groth16, ISCA'24 state-of-art) and NoCap (Spartan+Orion, MICRO'24). They acknowledge SZKP+ achieves 6× faster proving time than zkSpeed at iso-area—but requires circuit-specific setup. They're not hiding the tradeoffs.

**3. Real Workload Evaluation (Table 3)**
They evaluate on actual ZKP circuits (Zcash, Zexe recursive circuits, rollups) rather than just synthetic benchmarks. The 720-862× speedups are consistent across workloads.

**4. End-to-End Protocol Acceleration**
Unlike papers that accelerate one kernel (MSM or NTT) and extrapolate, they implement *all* HyperPlonk steps. Figure 12 shows the full runtime breakdown for both CPU and accelerator.

### Weaknesses

**1. No GPU Comparison**
The baseline is a 32-core AMD EPYC CPU. Section 8 mentions prior GPU work [39-41] but provides no direct GPU comparison. Given that GPUs are the de facto acceleration platform for ZKPs in practice, this is a significant omission. They claim 801× over CPU, but the GPU gap is likely much smaller—GZKP [41] and cuZK [40] achieve substantial speedups on GPUs for similar workloads.

**2. Area Comparison is Misleading**
They compare their 366mm² ASIC to a 296mm² CPU die at "iso-compute area" (Section 7.3). But the CPU die includes cores, caches, and interconnect for *general-purpose* computation. A fairer comparison would be against a GPU or against the marginal area cost of adding ZKP acceleration to an SoC.

**3. HBM PHY Costs are Substantial but Glossed Over**
The paper acknowledges HBM3 PHYs cost 59.2mm² (Table 5)—16% of total area—but then excludes them from CPU comparisons "since the AMD EPYC processor has its own separate die for I/O" (Section 7.3). This is cherry-picking. HBM3 PHYs are a *required* part of the design for high-performance configurations.

**4. Power Analysis is Incomplete**
Table 5 reports 170.88W average power, but no breakdown by protocol phase. Given that MSMs are compute-bound and SumChecks are memory-bound, power profiles likely differ dramatically. No power efficiency comparison (proofs/Joule) against CPU or GPU.

**5. Scalability Claims Need Scrutiny**
Figure 11 shows SumCheck speedup saturating quickly with PEs due to bandwidth limits. At 4096 GB/s, 16 SumCheck PEs achieve only ~12× speedup over 1 PE—sub-linear scaling. This suggests diminishing returns for larger designs.

**6. No Verification/Tape-out**
The results are from RTL synthesis with HLS (Catapult) scaled from 22nm to 7nm using standard factors (Section 6.1). No FPGA prototype, no silicon. The modular multiplier critical path (1.05ns at 381-bit) seems aggressive for full synthesis closure.

---

## Q4: What the Authors Didn't Tell You

### 1. The "90% Sparsity" in Witness MSMs Doesn't Translate to 10× Speedup
Section 3.3.1 mentions witness polynomials are "90% sparse (0s and 1s)." But look at Table 1: Witness MSMs have 1370 million modmuls—not dramatically fewer than Wire Identity MSMs (2290M) which are fully dense. Why? Because sparse MSMs still require computing the sum of all points corresponding to 1-valued scalars (Section 4.2), and the remaining 10% dense scalars dominate runtime via Pippenger's algorithm. The sparsity helps, but it's not a silver bullet.

### 2. SumCheck is Actually Memory-Bound Despite Being "O(n)"
The theoretical O(n) complexity of SumCheck (vs. O(n log n) for NTT) is celebrated, but Section 4.1.2 reveals the catch: after round 1, MLE table entries expand from binary to 255-bit, causing **100× data growth**. They're forced into a streaming architecture that becomes memory-bound. Table 1 shows SumCheck variants have arithmetic intensity of only 0.04-0.22 modmul/byte—far below the compute-bound MSMs at 7.8-8.7.

### 3. The "801× Speedup" is Geometric Mean Hiding Variance
Table 3 shows individual speedups ranging from 720× (Zcash at 2^17) to 862× (Zexe at 2^22). Figure 14 reveals per-kernel speedups vary from 410× (OpenCheck) to 2354× (Witness MSMs). The gmean smooths over the fact that SumCheck variants achieve only 410-560× speedup while MSMs achieve 784-1205×.

### 4. The Protocol is Fundamentally Serial
Section 3.3.6 explains that SHA3 "acts as an order-enforcing mechanism" between protocol steps. This means **the four major phases (Witness Commits, Gate Identity, Wire Identity, Polynomial Opening) must execute sequentially**. There's no cross-phase parallelism possible. The "streaming" dataflow is intra-phase pipelining, not inter-phase overlap.

### 5. On-Chip MLE SRAM Dominates Area at Large Problem Sizes
Section 7.3.2 admits: "at 2^22 size a single-core MSM is chosen... because on-chip MLE SRAM area begins to dominate, limiting MSM compute area." Table 5 shows SRAM is 143.73mm²—39% of total chip area. The 10-11× compression from binary packing (Section 4.6) helps but doesn't solve scaling. They suggest "storing MLE tables entirely off-chip may improve MSM speedups at higher SumCheck bandwidth costs"—a tradeoff punted to future work.

### 6. NoCap Achieves Similar Prover Time at 10× Less Area
Table 4 reveals NoCap (for Spartan+Orion) runs provers in 151.3ms vs. zkSpeed's 171.6ms, but uses only 38.73mm² vs. 366.46mm². The authors justify zkSpeed's 10× area cost with "three orders-of-magnitude reduction in proof size" (5KB vs. 8MB). Whether this tradeoff makes sense depends entirely on your application. For a blockchain with many verifiers, yes. For a single-verifier ML inference proof, maybe not.

### 7. Jellyfish Compatibility is Unclear
Section 8 briefly mentions Jellyfish (HyperPlonk variant with higher-arity gates) could "improve the runtime with sufficient bandwidth" but is "left for future work." This matters because Jellyfish is increasingly used in practice for its expressiveness with cryptographic operations.

---

**Bottom Line:** This is solid ISCA-quality work that does what it claims—first HyperPlonk accelerator with 801× speedup over CPU. But readers should understand the speedup comes primarily from MSM acceleration, SumCheck is bandwidth-limited and scales poorly, GPU comparisons are missing, and the area/power costs are substantial for the speedups achieved compared to other ZKP accelerators targeting different protocols.