## Q1: Whiteboard Explanation

Imagine you're doing ray tracing—shooting rays from a camera to figure out what objects they hit. The scene is organized into a **Bounding Volume Hierarchy (BVH)** tree: nested boxes that let you skip large chunks of geometry if a ray misses the outer box.

**The Problem:** Standard BVH trees store bounding boxes as six FP32 numbers (24 bytes per box). This creates two costs:
1. **Memory bandwidth**: Boxes dominate 69-74% of memory traffic (Figure 5)
2. **Compute energy**: FP32 ray-box intersection requires expensive floating-point multipliers

**Naive Solution Fails:** Just switching to FP16 causes 2.7x more ray-box tests and up to 19.6x more ray-triangle tests (Figure 1). Why? Lower precision *expands* boxes (to guarantee they still enclose the original geometry), creating false-positive intersections.

**AQB8's Insight—Multi-Level Quantization:** Organize the BVH into *clusters*. Each cluster has:
- One **anchor BB** in full FP32 (the reference frame)
- Many **quantized BBs** in INT8 (relative to the anchor)

When a ray enters a cluster (hits the FP32 anchor), you transform the ray into the anchor's local coordinate system. Then all subsequent ray-box tests within that cluster use **integer arithmetic only**—no FP32 multiplies.

The "multi-level" part: anchors appear throughout the tree at different scales (Figure 7). Large anchors for coarse structure, smaller anchors for fine detail. This bounds the quantization error because INT8 values represent offsets within progressively smaller regions.

**Hardware payoff:** Replace most FP32 BOX units with tiny INT8 QBOX units (5.1x smaller area per unit). The QBOX just does: `q_t = i_w * 2^{r_w} * m_w * q_x + q_b`—multiplies, shifts, and adds on integers.

---

## Q2: The Key Insight

The key insight is **coordinate system relativity eliminates the precision-performance tradeoff**.

Prior work compressed bounding boxes but still *decompressed to FP32* before intersection tests—saving bandwidth but not compute. Naive low-precision arithmetic fails catastrophically because quantization errors accumulate from the global origin, expanding boxes across the entire scene.

AQB8 observes that graphics scenes are **spatially sparse**: localized clusters of geometry separated by empty space (Figure 6(c)). By defining *local* coordinate systems (anchors) for each cluster, you quantize only the *relative offset* from a nearby high-precision reference—not the absolute world position. An INT8 offset within a 1-meter box has 256x finer resolution than an INT8 offset across a 256-meter scene.

This transforms the problem from "how do we tolerate INT8 quantization errors globally?" to "how often do we switch coordinate systems?" Since clusters >> boxes, the FP32 anchor processing is amortized away.

The technical enabler is the **ray re-quantization** trick (Section 4.4): instead of decompressing boxes to match the ray, they transform the ray to match the boxes. This inverts the conventional wisdom from prior compression work [7, 23, 37, 69, 77].

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Solid Baseline Comparison Design (Section 6.1):** The authors explicitly control for confounding variables by comparing Baseline, Compress, and AQB8 on *the same tree topology*. They test both 2-ary and 6-wide BVH configurations, demonstrating the technique generalizes across branching factors. This is methodologically sound.

**2. Appropriate Workload Selection:** The seven scenes (Table 1) span reasonable diversity—indoor (BA), outdoor (HOU), specular surfaces (BMW), complex geometry (1.4M triangles in BA). Triangle counts range from 0.1M to 1.8M. They use both standard benchmarks (pbrt-v4 scenes [51, 52]) and community test scenes [9].

**3. Multi-Level Memory Hierarchy Analysis:** Figure 12 breaks down traffic at L1D, L2, and DRAM separately, showing consistent reductions at each level (74%, 60%, 70% respectively). This is more convincing than just reporting DRAM numbers.

**4. Energy Methodology is Transparent:** They separate compute energy (gate-level simulation with PrimePower) from memory energy (CACTI for caches, 6.5 pJ/bit for GDDR6). The assumption of "zero-latency memory" for energy isolation (Section 6.3.3) is explicitly stated.

**5. Hardware Area Accounting is Honest:** Figure 15 shows they *add* units to Compress-2 and AQB8-2 to maintain throughput parity ("linear extrapolation based on operation counts"), then measure total area. This prevents the strawman of comparing an under-provisioned design.

### Weaknesses

**1. The Traversal Step Increase is Underplayed:** Figure 14 shows AQB8-2 increases ray-triangle tests by **6-31%** (mean ~15%). The paper dismisses this as "their total count is much lower than that of ray-box tests." But ray-triangle tests are *more expensive* per test (Table 3: 0.29 nJ vs 0.024 nJ for QBOX). For the TEA scene with 31% increase, this matters.

**2. The Compress Baseline Appears Weak:** Compress-2 uses "INT8-to-FP32 decompression scheme [69, 77]" but Section 6.1 mentions no optimization of when/how decompression occurs. Figure 13 shows Compress-2 *increases* energy in all scenes—suggesting the implementation may not be state-of-the-art. Did they tune it?

**3. Limited Scene Complexity:** The largest scene (HOU) has only 1.79M triangles with 0.92M rays per benchmark run. Modern games render scenes with 10-100M triangles. Table 1 shows the *number of clusters* scales with nodes, but no analysis of how cluster count grows for very large scenes, or whether the SAH-based clustering algorithm remains efficient.

**4. Resolution is Suspiciously Low:** All Vulkan-Sim evaluations use **256×256 resolution** (Section 6.2b). This is 65K pixels—real-time RT targets 1920×1080 (2M pixels) or 4K. The paper claims this "captures realistic performance perspective" but gives no justification. At 30x more pixels, memory pressure changes qualitatively.

**5. No Dynamic Scene Evaluation:** All scenes are static. BVH reconstruction for dynamic scenes is a known bottleneck. The paper doesn't discuss whether the clustering algorithm (Section 4.3) can be incrementally updated or must rebuild from scratch.

**6. The 2-ary vs 6-wide Gap is Unexplained:** AQB8-2 achieves 1.82x speedup but AQB8-6 only 1.43x (Figure 16). The paper doesn't analyze *why*. Is it because 6-wide trees already have better cache behavior? Different cluster characteristics? This limits actionable insight.

**7. Energy Model Assumes Unlimited Bandwidth:** Section 6.3.3: "functional memory model assumes unlimited memory bandwidth and zero-latency data transfers." But AQB8's benefit is largely *reducing memory traffic*. In a bandwidth-constrained scenario (mobile GPU, their stated motivation), the relative benefit could be larger or smaller depending on where the bottleneck sits.

---

## Q4: What the Authors Didn't Tell You

**1. The Preprocessing Cost is Hidden:** Section 4.3.1 says clustering is "agnostic to the specific BVH construction algorithm" and runs in O(n(log n)²). But they never report *actual* construction times. For a 1.4M-node tree, that's ~20M log operations. How long does this take? Is it acceptable for games with streaming assets?

**2. The Cluster Count Tradeoff is Opaque:** Table 1 shows cluster counts (0.001M to 0.032M) but never explains what drives these numbers. The cost function parameters [ct, ci, cs] = [0.5, 1, 1] appear in Section 4.3.2 as "empirically set"—no sensitivity analysis. What happens if you double cs? Halve it?

**3. The Custom FP14 Format is Underspecified:** Section 4.4.2 introduces a 14-bit floating-point format (1/8/5) for q_w. The justification is that inverse direction components follow a 1/(2w²) distribution. But:
   - Why 8 mantissa bits specifically?
   - What's the dynamic range/precision tradeoff vs FP16?
   - Does this require custom FP14 hardware or is it emulated?

**4. Ray Re-Quantization Frequency is Missing:** Algorithm 1 (line 6) says "re-quantize ray if needed" when jumping between clusters. How often does this happen? If it's frequent (many cluster transitions per ray), the FP32 anchor processing stops being "amortized."

**5. The Triangle Data is Untouched:** Triangles remain in FP32 (Section 4.6, Figure 10). But Figure 5 shows triangles are 12-20% of L1D traffic. Why not quantize triangles too? The paper never discusses this design choice.

**6. Image Quality Validation is Missing:** The paper claims quantization "guarantees correctness" by expanding boxes (Section 3). But there's no PSNR, SSIM, or flip-test comparison of rendered images. False positives change traversal order, which could affect soft shadow noise or temporal stability.

**7. The Mobile GPU Motivation Vanishes:** The abstract and Section 2.3 emphasize mobile GPUs ("DRAM bandwidth is typically more constrained [56]"). But the evaluation uses a 30-SM configuration with 3MB L2 cache (Table 2)—this is desktop/workstation class. No mobile-class configuration is tested.

**8. TRIG Unit Overhead is Ignored:** Table 3 shows TRIG units dominate area (192.92 mm² vs 16-97 mm² for others). AQB8 increases ray-triangle tests by 6-31% but keeps the same TRIG count. At scale, you'd need more TRIG units, partially offsetting the area savings from QBOX.