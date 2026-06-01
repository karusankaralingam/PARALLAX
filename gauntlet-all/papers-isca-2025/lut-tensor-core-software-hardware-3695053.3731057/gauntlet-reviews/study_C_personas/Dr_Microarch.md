## Q1: Whiteboard Explanation

Let me walk you through the actual hardware mechanism of this paper.

**The Problem:** LLMs use weight quantization (INT4/2/1 weights with FP16 activations) creating "mixed-precision GEMM" (mpGEMM). Current GPUs don't natively support this—they either dequantize weights back to FP16 (wasting the compression benefit) or use inefficient software LUT implementations.

**The Core Mechanism (Figure 3, Section 2.2):**

Instead of multiplying, you *precompute* all possible dot products. For a 4-element activation vector [A,B,C,D] with 1-bit weights, there are only 2^4=16 possible results:
- Index 0000 → 0
- Index 0001 → D
- Index 1110 → A+B+C
- Index 1111 → A+B+C+D

The weight bits become the *index* into this table. One table lookup replaces one 4-element dot product.

**The Hardware Trick (Figure 8, Section 3.1.2 & 3.2.1):**

The "magic" is weight reinterpretation. By mapping {0,1} to {-1,+1} (Equation 2), the lookup table becomes *symmetric about zero*:
```
LUT[W3W2W1W0] = -LUT[~(W3W2W1W0)]
```

This means you only need *half* the table entries (2^(K-1) instead of 2^K). The MSB (W3) just tells you whether to negate the result.

**The Datapath (Figure 9):**
- M tables are precomputed (one per row of output tile)
- N weight columns share each table (this is the "reuse" axis)
- K=4 activation elements define table size (8 entries after symmetry)
- Each PE is just a MUX + conditional negation, fed to a shift-accumulator for bit-serial multi-bit weights

**Bit-Serial for Multi-bit Weights (Section 3.2.1):**
For INT2/INT4 weights, they process one bit-plane at a time across W_BIT cycles, shifting and accumulating partial results. Same table, multiple passes.

---

## Q2: The Key Insight

**The singular "aha" moment:** Reinterpreting the quantization scheme from unsigned {0,1,...,2^K-1} to signed symmetric {-(2^K-1), ..., -1, +1, ..., 2^K-1} transforms the lookup table into an *odd function*, enabling:

1. **50% table size reduction** (Equations 4-6, Section 3.1.2): The symmetry means LUT[index] = -LUT[~index], so you only store half the entries.

2. **Elimination of per-PE negation logic** (Equation 6): Because the weight bits are *static* and known offline, the bit-flip for negative indices can be precomputed into the stored weights themselves. The hardware just checks the MSB to decide sign.

3. **Dramatic reduction in broadcasting/MUX cost**: Each table entry must be broadcast to N≈64-128 PEs. Halving entries halves this fanout overhead.

This is clever because the reinterpretation is *mathematically equivalent* (Equation 3 shows DP is unchanged), but the hardware implementation is fundamentally simpler. The "trick" is recognizing that the apparent overhead of signed arithmetic actually creates exploitable structure.

**Why conventional LUT designs missed this (Figure 13):** Without symmetrization, the table storage and MUX costs grow so fast that LUT-based designs lose their area advantage over MACs beyond 2-bit weights. The reinterpretation is what makes LUT competitive up to INT6 weights.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive PPA methodology (Section 4.1.1):** They actually synthesized RTL in TSMC 28nm using Synopsys DC at 1GHz, not just analytical models. This is the right way to do it.

2. **Design Space Exploration is honest (Figure 11, Section 4.2.1):** They swept K values and found K=4 optimal—importantly showing that larger K (which sounds better) actually *hurts* compute density due to exponential table growth.

3. **Direct comparison to prior LUT hardware (Table 2):** The ablation against UNPU [38] with incremental optimizations (+30% from symmetry, +1.44× total) is convincing attribution of gains.

4. **Accel-Sim integration (Section 4.3):** Simulating the modified A100 with their Tensor Core in a validated GPU simulator adds credibility beyond standalone accelerator numbers.

**Weaknesses:**

1. **End-to-end simulator is hand-wavy (Section 4.4):** They admit Accel-Sim is too slow, so they built a "tile-based simulator" claiming 5.21% error. But the validation (Figure 16) only shows *three models on two GPUs*—this is insufficient to trust extrapolations to LUT hardware. They essentially assume "highly optimized kernels behave like accelerators," which sidesteps microarchitectural effects.

2. **Register pressure is punted (Figure 15):** The "Double Register Modeling" scenarios (2X, 4X, 8X reg) acknowledge their design is register-bound but don't actually propose how to add register capacity. They show theoretical gains assuming 8X registers, but that's a massive area/power cost they don't account for.

3. **Comparison to NVIDIA Blackwell omits key details (Section 5):** They mention B100 supports FP4/6/8 mpGEMM natively, but don't benchmark against it. The claim "LUT Tensor Core supports these operations through bit-serial" glosses over whether it's actually competitive.

4. **Batch size sensitivity (Figure 4, Figure 17):** At BS=4096, software LUT is 62× slower than cuBLAS. Their hardware helps, but the GEMM-bound regime (large batches) isn't their strength—they're really optimized for GEMV-dominated decoding scenarios.

---

## Q4: What the Authors Didn't Tell You

**1. The table precomputation isn't free—it's just moved (Section 3.1.1):**
They claim DFG transformation "eliminates" precompute overhead via operator fusion. Table 4 shows overhead drops from ~16% to ~2.5%. But that 2.5% is *added latency on the critical path* before every mpGEMM. They fuse with the preceding operator (e.g., LayerNorm), meaning that operator now takes longer. For memory-bound scenarios, this might be hidden; for compute-bound, it's not.

**2. The "comparable area" claim requires careful reading (Table 1):**
They state LUT-8X occupies "only 38.3% that of conventional W_FP16A_FP16 MAC-based Tensor Cores." But LUT-8X means *8× the baseline LUT array size*. The 1× LUT Tensor Core is ~16% of FP16 TC area (Section 1). So they're actually comparing an 8× scaled-up LUT unit to a 1× MAC unit—an apples-to-oranges comparison that inflates the efficiency claim.

**3. Bit-serial has hidden throughput costs (Section 3.2.1):**
For INT4 weights, bit-serial takes *4 cycles* per lookup. The paper reports peak TOPs assuming single-cycle latency (Table 1 shows 1248/2496 TOPs for LUT-4X/8X). But effective throughput for INT4 is quartered. They don't clearly break down cycle-accurate throughput per weight precision.

**4. Table quantization accuracy claims are narrow (Section 4.6.2, Table 5):**
They validate INT8 table quantization on exactly *one model* (LLAMA2-7B with BitDistiller 2-bit weights) on a limited benchmark suite. The claim "negligible degradation" (7.68→7.69 PPL) is based on this single data point. No experiments on larger models, different quantization schemes, or edge cases.

**5. The roofline analysis reveals a fundamental issue (Figure 19):**
The "naïve LUT" implementation is *memory-bound* despite theoretically higher FLOPs. Their optimizations push toward the ridge point, but they're still bandwidth-constrained for large problems. This means the 4-6× PPA gains (Section 4.2.2) don't translate to 4-6× real speedups—Figure 17 shows actual end-to-end gains of 2-5× depending on batch/sequence configuration.

**6. No discussion of compiler register allocation complexity (Section 3.3):**
The LMMA instruction requires tables in registers and weights/activations in separate register banks. They register this with TVM/Roller but don't discuss the scheduling complexity or potential for suboptimal register allocation when mixing LMMA with other instructions.