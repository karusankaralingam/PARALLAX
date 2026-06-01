## Q1: Whiteboard Explanation

Let me walk you through the wiring diagram of Cambricon-SR as if we were at a whiteboard.

**The Problem:** Neural Scene Representation (NSR) algorithms like Instant-NGP use hash-based encoding tables to map 3D coordinates to feature vectors. The bottleneck is *massive irregular memory access* to these encoding tables. Cambricon-R (the predecessor) kept the entire 16MB encoding table on-chip to eliminate off-chip access, but its NoC throughput limited training to only 250 iterations per scene—too few for quality results on large scenes.

**The Core Dataflow (Figure 7):**
The system processes rays in a fine-grained pipeline with 32 sampled points per batch:
1. **Sampling Module** → converts pixels to rays, samples 3D points
2. **Encoding Module** → looks up features from the sparse encoding table
3. **MLP Module** → predicts RGB color and density
4. **Update Stage** → updates both sparse table (on-chip) and dense table (off-chip)

**The Sparse Table Mechanism (Figure 9):**
Here's the key structural change. Instead of one dense 128MB table, they maintain:
- **Dense Table (DT)** stored in HBM (off-chip) — 128MB, stores all entries
- **Sparse Table (ST)** stored in on-chip SRAM — ~26MB, stores only non-pruned entries (~14-20% of DT)
- **CAM (Content Addressable Memory)** — translates DT addresses to ST addresses

When you want entry at DT address 3, you query the CAM with "3", it returns ST address "1", and you read from SRAM location 1. This is the address indirection layer that makes sparse storage work.

**The Sparse Index Unit (SIU) — Figure 10:**
Before requests even hit the encoding table, the SIU filters out ~86% of invalid requests (pruned entries). It uses a bitmap where each bit indicates if an entry is valid. The trick is *sequential SRAM access with parallel matching*: instead of random-accessing the bitmap (which causes bank conflicts), they read the bitmap linearly and compare incoming addresses against the current read position. Requests are grouped by which Sequential Access Unit (SAU) will reach them fastest ("nearest grouping").

**The MLP Module with Dynamic Shared Buffer:**
32 MLP units share one activation buffer (instead of each having its own). Buffer blocks are allocated dynamically per 32-point batch using flag registers. This reduces buffer capacity per MLP unit by 85.3%, allowing 4× more MLP units (512 total).

---

## Q2: The Key Insight

**The "Magic Trick":** The fundamental insight is that hash-based encoding tables in NSR exhibit **>80% natural sparsity** after training converges—most entries have values below a threshold and contribute negligibly to the representation. This isn't structured 2:4 sparsity; it's *value-based* sparsity where entire F-dimensional entries are pruned if all dimensions fall below θ.

**Why this matters architecturally:** The prior work (Cambricon-R) was bottlenecked by NoC throughput to the encoding table banks. Even though everything was on-chip, you still had ~1800 memory requests per cycle hitting the table. By introducing sparsity at the algorithm level, they can:
1. Store only ~20% of entries on-chip (ST fits in 26MB instead of 128MB)
2. Filter out 86% of requests *before* they hit the NoC (via SIU)
3. Use the saved area for 4× more MLP units

**The second clever trick** is the **Sparse Update Unit's out-of-order storage with in-place replacement** (Section 4.3). When an entry transitions from sparse→dense, it needs to be added to the ST. When another goes dense→sparse, it needs to be removed. Instead of shifting the compacted storage, they use two queues (Queue A for additions, Queue B for deletions) and simply *overwrite* the location freed by a deletion with the new entry. The CAM is updated to reflect the address swap. This avoids O(n) data movement on every update.

**The SIU trick** (Section 4.4.2) deserves special mention: transforming a random-access problem into a streaming problem. A naive 2048×2048 crossbar to access the bitmap would be unroutable. Instead, they read the bitmap sequentially at 4 bytes/cycle, and incoming requests wait in per-SAU buffers until the sequential scanner reaches their address. "Nearest grouping" routes each request to whichever SAU will reach it soonest.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive ablation study (Figure 18-19):** They isolate contributions of Sparse Update Unit (1.24×), Dynamic Shared Buffer (1.36×), and SIU (3.07×). This is proper engineering methodology.

2. **Quality-at-same-time comparison (Table 1):** They correctly frame the comparison as "what quality do you get in 0.1 seconds?" rather than just iterations/second. This is the right metric for the application space. Cambricon-SR achieves 2-6 dB PSNR improvement over Cambricon-R at the same wall-clock time.

3. **Realistic dataset selection:** They use both synthetic datasets (NeRF-Synthetic) and large-scale real-world datasets (Mip-NeRF-360). The latter four datasets show lower PSNR precisely because they're harder—this is honest reporting.

4. **Area breakdown transparency (Table 2):** They disclose that CAM is 14.29% of chip area (33.56mm²) and SIU is 8.59% (20.17mm²). This is useful for reproducibility.

**Weaknesses:**

1. **The 128MB dense table lives off-chip.** Despite all the sparse machinery, they still need to read the full 128MB DT for threshold computation. They mitigate this by reading only half the DT ("imprecise computation of the threshold") but this seems like a fragile hack. Section 4.1 admits threshold update "takes longer time than the forward and backward process."

2. **CAM energy is likely underreported.** They claim 15MB of blocked CAM with 16 blocks × 160 entries per node. CAM dynamic power scales with the number of entries searched in parallel. Even with blocking, searching 160 entries per lookup at 750MHz is expensive. The energy breakdown (Figure 16) shows encoding module at 50% but doesn't separate CAM from SRAM.

3. **SIU wastes 70.7% of accessed bitmap data (Section 5.2.3).** They acknowledge this contributes to 52.8% of SIU's energy consumption. The sequential-access-with-matching scheme trades energy for area, but this is a significant overhead.

4. **Sparsity rates are per-scene tuned (Figure 4).** The paper sets different sparsity rates (80-96% forward, 80-95% backward) for each of the 8 datasets. This isn't a single configuration—it's 8 configurations. How would you set this for an unseen scene?

5. **GPU baseline fairness:** They compare against A100 at 5 seconds per scene (Section 2.2) but their own ST-NSR GPU implementation only achieves 1.19× overall speedup (Section 3.3). The 1259× speedup is comparing Cambricon-SR against vanilla Instant-NGP on GPU, not ST-NSR on GPU.

---

## Q4: What the Authors Didn't Tell You

**1. The CAM is *huge* and *slow*.**
The paper claims 15MB of CAM (Section 4.2), but here's the math they hide: each node stores ~2,500 entries, there are 16 STAs with 256 nodes total (40MB / ~160KB per node), and each entry needs a CAM slot. At 160 entries per block × 16 blocks per node, you need 2,560 CAM entries per node. The blocking strategy means each lookup still activates an entire 160-entry block for parallel comparison. They quote 33.56mm² (14.29% of chip) for CAM alone—this is *enormous* for what is essentially a sparse index translation layer.

**2. The "dense table" creates an awkward split.**
The ST-NSR algorithm (Algorithm 1, Line 13-16) requires maintaining the full dense table T_dense to accumulate gradients—because pruned entries might become valid later. This DT lives off-chip (128MB in HBM). Every update stage requires reading a portion of this table. The Sparse Update Unit reduces this to only changed entries during *gradient* updates, but threshold computation still requires scanning half the DT. They hide the latency behind the forward/backward stage (Section 4.1), but this is pipeline scheduling, not elimination of the access.

**3. The SIU design space was constrained by EDA tools.**
Section 4.4.1 reveals: "a naive 2048×2048 crossbar...EDA tools are unable to complete the layout and routing for it." This is a real constraint—they couldn't build the obvious solution. The sequential-access trick is clever, but it's born of necessity. The final SIU configuration (32 blocks × 16 SAUs × 192 entries per request buffer) was found via simulation to minimize area while avoiding backpressure.

**4. The Dynamic Shared Buffer introduces contention.**
Section 4.5.1 describes a control unit with flag registers managing buffer blocks across 32 MLP units. They don't discuss contention when multiple MLP units complete rays simultaneously and need to release buffer blocks. The atomic operations for weight gradient accumulation (Section 4.5.1) also add serialization overhead that isn't quantified.

**5. Power scaling assumptions.**
They synthesize at 45nm and scale to 7nm using [44] (Stillmaker & Baas 2017). This scaling methodology is controversial for complex mixed-signal/memory-heavy designs. CAM power doesn't scale linearly with process node due to leakage and minimum sizing constraints.

**6. The threshold update uses stale values.**
Section 4.1: "Cambricon-SR utilizes the sparsity threshold from the last iteration during the update process." Combined with reading only half the DT for threshold computation, this means the sparsity mask can be up to 2 iterations stale and computed from a biased sample. They claim "negligible impact...less than 0.1 [PSNR]" but don't show this data.

**7. Comparison against Cambricon-R may use different iteration counts.**
Table 1 compares at "the same modeling time" (0.1 second per scene). Cambricon-SR achieves 4.12× speedup per iteration (Section 5.2.2), but ST-NSR iterations are algorithmically different from NSR iterations. The 4.12× is a per-iteration speedup for a modified algorithm on modified hardware—it's not an apples-to-apples comparison.