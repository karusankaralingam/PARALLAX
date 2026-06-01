## Q1: Whiteboard Explanation

Let me walk you through what PADE actually does at the hardware level.

**The Core Problem They're Solving:**

Current dynamic sparse attention accelerators use a two-stage approach: (1) a *predictor* that computes low-bitwidth Q×K^T (e.g., 4-bit MSB multiplication) to identify important token pairs, then (2) an *executor* that recomputes the full-precision attention for only those important pairs.

The problem? As Figure 2(a) shows quantitatively, when you move to 8-bit quantized models (the industry trend), the predictor consumes **over 63%** of total power. The predictor must still load and process the *entire* K tensor regardless of sparsity. You've eliminated computation but not memory bandwidth.

**The "Stage Fusion" Trick (BSF):**

Instead of predict-then-execute, PADE processes Keys bit-plane by bit-plane using bit-serial arithmetic. Here's the dataflow (Figure 4(b)):

1. Load only the MSB plane of all Keys
2. Compute partial Q×K^T scores using just that 1-bit plane
3. For each Key, decide: "Is this *definitely* unimportant?" If yes, terminate immediately—never load remaining 7 bit planes
4. If uncertain, request the next bit plane on-demand
5. Crucially: **accumulate** the partial results, don't recompute

The magic is that prediction and execution become the *same* computation. You're not predicting then re-doing work—you're incrementally building the final answer while pruning.

**The Hardware Reality (Figure 11):**

The QK-PU contains 128 bit-wise PE Lanes arranged as 8 rows × 16 lanes. Each PE lane (Figure 11(b)) has:
- A **Grouped Lightweight Sparsity ANDer Tree (GSAT)**: This computes the partial dot product between an 8-bit Query and a 1-bit Key plane. It's partitioned into eight 8-dimensional sub-groups, using 5-to-1 MUXes per sub-group.
- A **Scoreboard** (32 entries × 45 bits): This is the key reuse mechanism. When a PE computes a partial score S^r_{i,j} for Key j at bit-plane r, it stores it here. When bit-plane r+1 arrives, the PE reads back the partial sum and accumulates.
- A **Decision Unit**: Compares the upper-bound score against the threshold to decide whether to request the next bit plane or prune.

**The BUI-GF Pruning Logic (Section IV-A, Figure 6):**

This is how they avoid the inaccuracy problem of naive bit-sliced speculation. For a partial dot product computed with only r bit planes known:

- For positive Query elements, set all unknown Key bits to 1 → gives maximum possible contribution
- For negative Query elements, set unknown Key bits to 0 → same logic
- This gives you S^{r,max}_{i,j} (upper bound)
- Flip the logic for the lower bound S^{r,min}_{i,j}

The threshold T = max(S^{:,min}_{i,:}) - α × radius (Equation 4). If a token's *upper bound* falls below this threshold, it's safely prunable.

---

## Q2: The Key Insight

**The One Clever Insight:** The prediction overhead in sparse attention isn't from the *algorithm*—it's from the *architecture's inability to reuse partial computation*. By restructuring computation into bit-serial form, you can make prediction and execution become a single unified operation where early-stage speculation directly contributes to final results.

**Why This Works at the Bit Level:**

The insight exploits two's complement representation properties (Equation 2): all bits except the sign bit contribute non-negative values. This means as you process more bit planes, the score can only *increase or stay the same* in magnitude for positive contributions. This monotonicity enables *safe* early pruning—if the upper bound is too low, no future bits can save the token.

**The Structural Delta vs. Prior Work (Table I):**

| Existing Accelerators | PADE |
|----------------------|------|
| Separate predictor module (4-bit Q×K^T) | No predictor—same hardware does speculation and execution |
| Predictor output discarded after masking | Partial scores accumulated via scoreboard |
| Load full K tensor for prediction | Load bit-planes on-demand; early termination stops loads |
| Value-level early termination | **Bit-level** early termination |
| Coarse-grained tiling | Interleaving-based sparsity-tiled attention (ISTA) enables tiling *despite* row-wise softmax dependency |

The "delta" is a scoreboard-based PE architecture that transforms prediction from a sunk cost into a reusable investment.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive Baseline Comparison (Figure 14, 21):** They compare against 5 SOTA accelerators (Sanger, SpAtten, DOTA, Energon, SOFA) and normalize everything to 28nm/800MHz with identical SRAM and HBM bandwidth. This is unusually rigorous.

2. **End-to-End System Integration (Figure 24):** They actually address deployment—PADE as a co-processor sharing HBM with GPU, with explicit data conversion overhead quantified (<2% latency increase with bit-oriented layout).

3. **The Power Breakdown Analysis (Figure 2):** This is the strongest part. The observation that predictor overhead dominates at low bitwidths (63% at INT8) is backed by TSMC 28nm synthesis numbers, not just cycle counts.

4. **Ablation Study with Clear Attribution (Figure 16(a)):** BUI-GF contributes 30% latency reduction, BS-OOE adds 24%, ISTA adds 27%. The breakdown is additive and believable.

5. **DSE for Non-Obvious Parameters (Figure 17):** They justify scoreboard size (32 entries saturates utilization at 95%) and sub-group size (8 minimizes area×power). This shows actual hardware design iteration.

**Weaknesses:**

1. **DRAM Modeling Assumptions Are Favorable:** They claim 256 GB/s HBM2 bandwidth with "16×64-bit pseudo channels@2Gbps" (Table III). But bit-plane-granular fetches will suffer from poor row buffer locality. Figure 23(b) shows only 58% BW utilization *after* their custom data layout. Without it, BW utilization is implicitly much worse—they don't report this number.

2. **The Scoreboard Cost is Underreported:** Each PE lane has a 32-entry × 45-bit scoreboard. With 128 PE lanes, that's 128 × 32 × 45 = 184,320 bits = 23KB of scoreboard SRAM *just for partial sums*. This is ~7% of their total 352KB buffer budget, but it's distributed across PEs, making it expensive multi-ported register file-style storage, not bulk SRAM.

3. **Accuracy Claims Need Scrutiny (Table II):** They claim "0% accuracy loss" for PADE(S) vs INT8, but the numbers show small degradations across the board (e.g., MBPP drops from 17.2% to 17.2%—okay—but Winogrande drops from 69.3% to 69.2%). More importantly, they're comparing against their own INT8 baseline, not the FP16 model. The cumulative loss from FP16 → INT8 → PADE(S) isn't highlighted.

4. **The "Predictor-Free" Claim is Technically True but Misleading:** The BUI-GF module (Figure 11(d)) with its threshold updating logic and BUI Generator *is* a predictor—it just shares the datapath with execution. The area breakdown shows BUI Generator + BUI-GF Module = 4.9% area (Figure 20). This isn't zero overhead; it's amortized overhead.

5. **Missing Latency Distribution Data:** They report average speedups, but for sparse attention, worst-case latency matters. What happens when sparsity is low (dense attention patterns)? The BS-OOE mechanism should handle this, but no tail-latency data is provided.

---

## Q4: What the Authors Didn't Tell You

**1. The Real SRAM Cost of Bit-Serial Execution:**

The paper buries this: bit-serial computation requires storing partial products across multiple cycles. Each PE lane's scoreboard must hold partial sums for *multiple Keys simultaneously* (because of OOE execution). At 32 entries per lane × 128 lanes × 45 bits/entry, you're looking at 23KB of high-bandwidth, multi-read/write-port storage that acts like a register file, not like bulk SRAM.

Figure 20 shows "Scoreboard 3.7%" of area, but this is 3.7% of a 4.53mm² chip in 28nm. In a more aggressive node (7nm), scoreboard overhead would dominate because logic shrinks faster than SRAM.

**2. The Memory Access Pattern is Pathological for HBM:**

Bit-plane-first storage (Figure 22) means fetching the MSB plane of K requires accessing every 8th byte of the original tensor. Even with bank interleaving along the bit dimension, you're striding through memory at 8× the natural access pattern. The paper admits this indirectly: "PADE's bit-grained sparsity lowers DRAM bandwidth utilization by around 30%" (Section VI-E).

The 4.6× memory access reduction (Figure 4(c)) is comparing *total bytes transferred* vs stage-splitting. But effective bandwidth is bytes/second, and their utilization is 30% lower. The net improvement is closer to 4.6× × 0.7 / 1.0 = 3.2× in terms of latency hiding.

**3. The Out-of-Order Execution Complexity is Hidden:**

BS-OOE (Section IV-B) requires tracking dependencies between bit-planes of different Keys across different queries. The scoreboard must support:
- Content-addressable lookup by (Token_IDX, Bit_IDX)
- Partial sum read-modify-write in the same cycle
- Eviction on pruning decisions

This is a CAM + register file hybrid. The "lightweight BS Scheduler" claims 75% priority encoder reduction by time-multiplexing (Section V-D), but this adds latency that's "hidden by PADE's staggered QK-PU/V-PU pipeline." They don't quantify this latency or its worst-case impact.

**4. The ISTA Tiling Assumes Attention Locality:**

The head-tail interleaved updating (Figure 10(a)) relies on "Recently generated tokens and the initial token typically exhibit higher weights" [115], [57]. This is true for autoregressive LLMs with causal attention but may not hold for:
- Bidirectional attention (BERT-style)
- Vision Transformers with global attention
- Models with learned position encodings that don't favor recent tokens

Section IV-C admits: "without attention locality, the performance of head-tail interleaving is on par with regular execution and not worse." But they don't report which benchmarks hit this fallback case. The ViT and PVT numbers in Figure 21 show smaller speedups than LLMs—possibly this effect.

**5. The Comparison to GPU is Apples-to-Oranges:**

The H100 comparison (Figure 18(b)) shows 31.1× energy efficiency gain. But:
- H100 TDP is ~700W; PADE's 591mW power suggests they're comparing a full GPU to a single-attention-layer accelerator
- They exclude "non-computational phases using nvprof" but don't specify what this includes. Memory allocation? Kernel launch? PCIe transfer?
- The "GPU+PADE" integration (Section VI-F) shows the realistic gain is 2.1× speedup at 214k sequence length—more modest but more honest

**6. Extension to FP Formats is Hand-Waved:**

Section VI-F claims: "when queries operate in FP format, PADE converts the INT-FP computation into a bit-serial form through exponent alignment, following methodologies adopted in prior works [14], [54], [32]."

This is a single sentence for a major compatibility concern. FP→INT conversion requires:
- Dynamic range alignment per-tensor (or per-channel for MX format)
- Potential accuracy loss from rounding during exponent alignment
- Different bit-plane semantics (FP mantissa bits aren't weighted like INT bits)

They cite three papers but don't show any FP accuracy results or explain how BUI-GF adapts to FP representations.