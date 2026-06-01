## Q1: Whiteboard Explanation

Let me walk you through the core mechanism of AQB8 as if we were at a whiteboard.

**The Problem Setup:**
Ray tracing accelerators spend most of their time doing ray-box intersection tests during BVH tree traversal. A standard bounding box (BB) requires 6×FP32 values (24 bytes) for its min/max corners. Figure 5 shows BBs account for 69% of L1D accesses and 74% of DRAM accesses. The intersection test itself (Figure 3(a), Section 2.1.3) uses the Slab method:

```
t_x' = w_x * x' + b_x  (Equation 1)
```

where `w = 1/d` and `b = -o/d` are precomputed ray parameters. This requires FP32 multiply-adds.

**The Naive Fix Fails:**
Figure 1 is the smoking gun. If you naively use FP16 for both storage *and* computation, ray-box tests increase by 2.7× and ray-triangle tests explode by 19.6×. Why? Quantized BBs must *enclose* the original (for correctness), so they expand outward (Figure 6(a)), creating false positives and wasted traversal.

**The Multi-Level Quantization Trick:**

Here's the actual mechanism (Section 3-4, Figure 7-8):

1. **Cluster the BVH tree:** Group nodes into clusters using a SAH-derived cost function (Section 4.3.2). Each cluster has:
   - One **anchor BB** stored in FP32 (24 bytes) — the "reference frame"
   - Multiple **quantized BBs** stored as 6×INT8 (6 bytes each) — offsets within the anchor's local coordinate system [0, 255]

2. **Ray quantization (Section 4.4):** Instead of decompressing BBs back to FP32, they transform the *ray* into the cluster's local coordinate system:
   - `q_w = w / S_w` stored as custom FP14 (1 sign, 8 mantissa, 5 exponent)
   - `q_b = b / (S_w * S_x)` stored as INT32
   - `S_x = max(l_x, l_y, l_z) / 255` is cluster-specific

3. **The quantized intersection test (Section 4.5):**
   ```
   q_t = i_w * 2^(r_w) * m_w * q_x + q_b
         └───────────────────────┘
              INT8 multiply, left shift, 2's complement, INT32 add
   ```
   
   This is the "magic trick": the entire ray-box test becomes **integer arithmetic only** — no FP32 multipliers needed for quantized BBs.

**Memory Layout (Figure 10):**
- Standard node: 56 bytes (24B left BB + 24B right BB + 8B metadata)
- Quantized node: 16 bytes (6B left BB + 6B right BB + 4B child data)
- Cluster overhead: 36 bytes (24B anchor BB + 4B scale + 8B base indices)

Since clusters are far fewer than nodes (Table 1: BMW has 0.78M nodes but only 0.09M clusters), net storage drops significantly.

---

## Q2: The Key Insight

**The One-Liner:** *Transform the ray into the bounding box's coordinate system, not the other way around.*

Prior compression work [7, 23, 37, 69, 77] reduces BB storage but decompresses back to FP32 for computation. AQB8 inverts this: keep BBs compressed as INT8 and instead quantize rays when entering a new cluster. 

This is clever because:
1. **Cluster count << BB count:** You only re-quantize rays at cluster boundaries (Algorithm 1, line 6). Table 1 shows ~10-50× fewer clusters than nodes across all scenes.
2. **INT8 arithmetic is cheap:** The QBOX unit (Figure 11(d)) replaces FP32 MUL+ADD with INT8 MUL + barrel shift + INT32 ADD. Table 3 shows QBOX is **5.1× smaller** in area and uses **5.7× less energy per operation** than BOX.
3. **Hierarchical anchors control quantization error:** Larger anchors at coarse levels, smaller anchors at fine levels (Figure 7(b-d)) prevent the cascading expansion that kills FP16-only approaches.

The "multi-level" aspect is really about *adaptive quantization granularity* — it's like having different grid resolutions at different spatial scales.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths:

1. **Full-stack evaluation:** They synthesized hardware with Catapult HLS → Design Compiler (TSMC 40nm), did gate-level power analysis with PrimePower, and integrated into Vulkan-Sim for system-level timing (Section 6.3-6.4). This isn't hand-waving.

2. **Apples-to-apples comparison:** Section 6.1 explicitly controls for tree topology — they compare against baselines with *identical* BVH structure, only swapping the BB representation. Figure 16 shows this across both 2-ary and 6-wide trees.

3. **Memory breakdown is convincing:** Figure 12 shows L1D requests drop 74%, L2 drops 60%, DRAM drops 70%. The hit/miss ratio visualization makes it clear this isn't just about compression — spatial locality from contiguous cluster storage is contributing.

4. **Energy breakdown is granular:** Figure 13 separates compute vs. SRAM vs. cache vs. DRAM energy. AQB8-2 reduces *all* categories, showing the win isn't just memory bandwidth.

5. **Honest about traversal overhead:** Figure 14 shows 3-6% more ray-box tests and 6-31% more ray-triangle tests due to quantization error. They don't hide this.

### Weaknesses:

1. **TSMC 40nm is ancient:** Modern RT accelerators are in 5nm or below. The 5.1× area ratio between QBOX and BOX (Table 3) may shrink at smaller nodes where FP32 units are more optimized. The relative benefit needs re-validation at modern process nodes.

2. **Scenes are small by modern standards:** Table 1 shows 0.5-1.4M triangles. Modern games have 10-100M+ triangles. The cluster-to-node ratio may not scale favorably — more geometry could mean proportionally more clusters, eroding the "re-quantize rarely" benefit.

3. **Ray coherence assumption:** Section 4.7 (Algorithm 1, line 6) requires re-quantization when "jumping back" to a different cluster. With incoherent rays (e.g., path tracing with random bounces), cluster switches could become frequent. The paper only tests with primary rays ("one sample per pixel" in Section 6.2).

4. **Vulkan-Sim accuracy is unclear:** The baseline RT accelerator is "similar to the RT Unit described in [56]" (Section 5) but actual NVIDIA/AMD hardware is a black box. The 1.82× speedup (Figure 16) is against their own baseline, not commercial silicon.

5. **Dynamic scenes not addressed:** BVH construction includes the clustering pass (Section 4.3). The O(n(log n)²) complexity is "similar" to standard construction, but the constant factor and memory overhead for maintaining cluster metadata during BVH updates isn't evaluated.

6. **Custom FP14 format:** Section 4.4.2 introduces a 14-bit float (1-8-5) for `q_w`. This requires dedicated conversion logic from FP32, which isn't quantified in the area/energy accounting.

---

## Q4: What the Authors Didn't Tell You

### 1. The Cluster Construction is Actually Where the Bodies are Buried

Section 4.3.3 says the clustering algorithm runs in O(n(log n)²) via dynamic programming, but look at Figure 9(a): they compute costs C(X_Y) for node X quantized relative to *every ancestor* Y. For a tree of depth d, this means O(d) costs per node. The "bottom-up" pass stores multiple costs per node, and the "top-down" backtracking assigns policies. 

**What they don't say:** The memory overhead for storing all these intermediate costs during construction could be substantial for deep trees. They also don't provide wall-clock construction times.

### 2. The FP14 Format Has No Standards Compliance

The custom 14-bit float (Section 4.4.2) has 8 mantissa bits and 5 exponent bits. This doesn't match IEEE FP16 (10-5) or BF16 (7-8). You need custom conversion hardware to/from FP32. The paper implies this is absorbed into the "modified BOX units" (Section 5.1, "calculate quantized rays"), but the conversion cost is never isolated.

### 3. The Re-Quantization Latency is Handwaved

When switching clusters (Algorithm 1, line 8), the ray must be re-quantized:
- Compute S_x from anchor BB dimensions
- Compute q_b = b / (S_w * S_x) as INT32

This requires **FP32 division and multiplication** in the critical path. The paper says "the re-quantization of ray data introduces negligible performance overhead" (Section 4.4) because clusters are rare, but doesn't measure the actual latency penalty when it *does* happen.

### 4. The 27% Area Reduction Hides a Unit Count Change

Figure 15 is cleverly constructed. AQB8-2 has:
- 78 TRV units (vs 64 baseline)
- 53 QBOX units + 9 BOX units (vs 51 BOX units baseline)
- 13 TRIG units (vs 11 baseline)

They "use linear extrapolation based on operation counts" (Section 7.4) to scale up unit counts for fair throughput comparison. But this means the area comparison isn't configuration-to-configuration — it's a *hypothetical* accelerator designed for equivalent throughput. If you just swap in QBOX for BOX without adding units, the area reduction would be larger, but throughput would drop.

### 5. The Energy Model Assumes Zero Memory Latency

Section 6.3.3: "our functional memory model assumes unlimited memory bandwidth and zero-latency data transfers." This isolates intrinsic energy but completely decouples it from the performance model. In reality, memory stalls affect both — reducing DRAM accesses saves energy *and* reduces stalls, which should compound. Their energy numbers (49% reduction, Figure 13) may underestimate the full-system benefit.

### 6. The L0 Cache Behavior is Unspecified

Section 2.2 mentions an 8KB L0 cache for BVH nodes/triangles. Section 4.6 claims "fetching one node brings in its neighboring nodes in the same cluster into cache." But the paper never analyzes L0 hit rates separately from L1D. With 16-byte nodes (vs 56-byte standard), you fit 3.5× more nodes in the same cache, but the access pattern to clusters (36 bytes each) isn't characterized.

### 7. Triangle Storage is Untouched

The paper focuses exclusively on BBs. Triangle data (also FP32, also fetched from DRAM) is left at full precision. Figure 5(b) shows triangles are only ~12% of DRAM traffic, but this is still a missed opportunity. The ray-triangle test (TRIG unit, Figure 4) remains unchanged — all FP32.