# Caravan: Evaluation Critique

## Q1: Whiteboard Explanation

Let me draw this out for you.

**The Problem:**
Imagine a LiDAR sensor on a self-driving car scanning the environment. It produces a "point cloud" — thousands of 3D points representing surfaces. For tasks like segmentation ("which points belong to the same object?"), you need to repeatedly find *neighbors* of each point.

**The Naive Approach is Terrible:**
Comparing every query point against every point cloud point is O(n²). So we use a **k-d tree** — a binary tree that recursively splits 3D space. To find neighbors, you traverse down the tree, pruning irrelevant regions.

**The Key Observation (Figure 1-2):**
Because LiDAR rotates to scan, *consecutive points are spatially close*. In their data, 80%+ of consecutive points are within 2 meters. Spatially close points → similar k-d tree traversals.

**Caravan-SW (Software Only):**
Pack 16 consecutive queries into a "query pack" (QP). Traverse the tree *once* for all 16 using AVX512 SIMD. If all queries agree on "go left" or "go right," you visit the node once instead of 16 times. A `valid_mask` tracks which queries are still relevant at each node.

**The Sparsity Problem (Figure 3, 8):**
At deeper tree levels, queries *diverge* — some go left, others right. In leaves, you have maybe 7 valid queries and 10 leaf points, but AVX512 has 16 lanes. Traditional approach: broadcast one vector, iterate over the other. Either way, you waste SIMD lanes.

**Caravan-HW (Two New Instructions):**
- **EDIRS**: Extract Dense IDs, Repeating *Sequence* (cycles through valid indices)
- **EDIRE**: Extract Dense IDs, Repeating *Element* (repeats each valid index)

These generate index vectors so you can `permutexvar` both sparse vectors into *dense* form. Instead of 7.65 or 9.99 sparse iterations, you get 5.16 dense iterations (Figure 15).

**Result:** 5.19× neighbor search speedup, 1.97× end-to-end segmentation speedup, with 0.032 mm² of new silicon.

---

## Q2: The Key Insight

**The Paper's Stated Insight:**
"Consecutive neighbor search queries in point cloud processing are often similar, visiting k-d tree nodes with considerable resemblance" (Abstract, Section 3.1).

**Why It's Actually Clever:**
This isn't just "data locality." The insight is that *algorithm behavior* (k-d tree traversal paths) is correlated across queries due to *sensor physics* (LiDAR rotation). They convert a *workload property* into a *SIMD opportunity* that didn't exist in the original algorithm.

**Why Existing Solutions Miss This:**
Previous neighbor search SIMD work either:
1. Parallelizes *independent* queries (no shared traversal)
2. Parallelizes within a *single* query's leaf processing

Caravan does neither — it parallelizes *correlated* queries that share most of their traversal but may diverge partially. The `valid_mask` mechanism (Section 3.2) handles divergence without revisiting nodes.

**The Hardware Insight (EDIRS/EDIRE):**
The leaf processing is an "all-to-all SIMD pattern" with *runtime-determined* sparsity on *both* vectors. Existing ISAs have no efficient way to densify two sparse vectors for pairwise operations. The insight that you only need quotient/remainder arithmetic on compressed indices (Algorithm 2) makes the hardware trivially cheap.

**What Makes It Non-Obvious:**
The sparsity isn't static (like in sparse DNNs) — it varies per leaf visit based on divergence patterns. Hardcoding "iterate over queries" or "iterate over points" is suboptimal because neither is consistently sparser.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. End-to-End Application Evaluation (Strong)**
Unlike QuickNN, ParallelNN, BitNN, and RTNN that only evaluate neighbor search in isolation, Caravan evaluates full point cloud segmentation in Autoware.ai with real 8-minute LiDAR sequences (Section 4.1). This is critical because Tigris reports 392× neighbor search speedup but only 1.86× end-to-end (Table 2). The authors acknowledge Amdahl's Law explicitly (Section 4.3, Figure 14: neighbor search is 61% of segmentation time).

**2. The Baseline is State-of-the-Art and Practical (Strong)**
They use PCL/FLANN (Section 4.1), which is the actual library used by Autoware and Baidu Apollo in production. This isn't a strawman — it's what industry deploys.

**3. Hardware Cost Analysis is Thorough (Strong)**
Table 1 provides area (0.032 mm²), power (2.43 mW), and latency (9 cycles at 2.5 GHz) for synthesized hardware in the same 14nm node as their target Xeon. The comparison table (Table 2) shows Tigris needs 15.57 mm² for 1.86× end-to-end speedup; Caravan gets 1.97× with 500× less area.

**4. Diagnostic Analysis of Limitations (Strong)**
Figure 8 quantifies exactly why Caravan-SW has diminishing returns: average valid queries drop from 79% (QP=4) to 55% (QP=16). Figure 15 shows the iteration reduction from Caravan-HW (7.65 → 5.16 average). This isn't handwaving — they measured where the ceiling is.

### Weaknesses

**1. Single Application, Single Dataset (Major Concern)**
The entire evaluation is on Autoware segmentation with one 8-minute LiDAR sequence from Tier IV (Section 4.1). They *claim* applicability to localization and 3D DNNs (Sections 2.3, 3.5) but provide *zero* data. The spatial locality observation (Figure 1) might not hold for:
- Localization (queries come from a *different* point cloud than the k-d tree)
- Solid-state LiDAR (different scanning patterns)
- Indoor scenes (VR use case mentioned but not tested)

**2. The "Search Locality" Assumption Isn't Stress-Tested**
Figure 1 shows consecutive points are close, but this is *input* locality, not *search* locality. They never measure: what if the k-d tree is built on a reference map (localization) instead of the same scan? What if point density varies dramatically within a scan?

**3. Caravan-HW Benefit is Modest Over Caravan-SW**
Caravan-SW: 4.05× neighbor search, 1.85× end-to-end
Caravan-HW: 5.19× neighbor search, 1.97× end-to-end

The new instructions add 28% neighbor search speedup but only 6.5% end-to-end speedup (1.97/1.85 ≈ 1.065). Given the silicon cost and ISA complexity, is this worth it? The theoretical max is 2.56× (Figure 13); Caravan-SW already captures 72% of it.

**4. GPU Comparison is Absent (Suspicious)**
Section 5 dismisses GPUs as "prioritized for image tasks" and cites [33] claiming GPUs can "slow down" segmentation. But [33] is from 2018 — modern GPU neighbor search libraries (FAISS, cuML) exist. EdgePC [69] shows 3.68× on GPUs. Why no direct comparison?

**5. The Min QP Size Tuning is Unexplained**
Figure 11 shows Min QP Size = 8 is optimal, but *why*? The paper says it "balances" VPU usage and search frequency (Section 4.3) without explaining the sensitivity. Is 8 optimal for all scenes? All k-d tree depths?

**6. Memory System Effects Unanalyzed**
Caravan-SW reduces visited nodes by 83% (Figure 6), but what about cache behavior? Does packing queries improve or hurt spatial locality for k-d tree node accesses? No cache miss data, no memory bandwidth analysis.

---

## Q4: What the Authors Didn't Tell You

**1. The Localization Use Case Would Probably Fail**
In localization (ICP/NDT), queries come from the *current* scan, but the k-d tree is built on a *reference map*. Consecutive query points are still spatially close to *each other*, but their k-d tree traversals diverge rapidly because the reference tree has different spatial partitioning. The "search locality" observation (Section 3.1) critically assumes queries search a tree built from *the same scan* — this is only true for segmentation.

**2. The 80% Locality Number (Figure 1) is Cherry-Picked**
Figure 1 shows histogram of consecutive point distances from a Velodyne LiDAR. But the *order* of points depends on the LiDAR driver and buffering. Different sensors (Ouster, Hesai, solid-state) have different point ordering. The 80% within 2m claim may not generalize.

**3. Caravan-SW Adds Significant Code Complexity**
Section 3.2 describes the `valid_mask` bookkeeping, pivot query selection, and recursion parameter updates. This is ~2-3× more complex than baseline PCL traversal. They don't report code size increase or compilation overhead.

**4. The Instructions Aren't as "Simple" as Claimed**
Figure 9 shows 16× 5-bit dividers for EDIRS/EDIRE. Division is expensive — even 5-bit division. They synthesized at 1 GHz and use a frequency divider to get 9 cycles at 2.5 GHz. This suggests timing closure was non-trivial; the "cheap" narrative (Section 3.4) deserves scrutiny.

**5. The "All-to-All SIMD Pattern" Generality Claim (Section 3.5) is Speculative**
They claim applicability to ray tracing, genomics, and feature matching with zero experiments. The Smith-Waterman example is particularly suspect — that algorithm has *sequential* dependencies (dynamic programming), not just pairwise comparisons.

**6. What Happens When the k-d Tree Doesn't Fit in Cache?**
Their test platform has a Xeon with 13.75 MB L3 (reference [29]). Real AD point clouds can be 100K+ points. They don't report k-d tree sizes or cache residency. If the tree spills to DRAM, the latency-bound traversal could dominate over any SIMD benefit.

**7. The Comparison Table Normalizes Unfairly**
Table 2 compares Caravan's 1.97× end-to-end to Tigris's 1.86× and EdgePC's 1.55×. But:
- Tigris targets localization (different algorithm)
- EdgePC targets 3D CNNs (different algorithm)
- Only Caravan is evaluated on segmentation

They're comparing speedups across *different applications*.