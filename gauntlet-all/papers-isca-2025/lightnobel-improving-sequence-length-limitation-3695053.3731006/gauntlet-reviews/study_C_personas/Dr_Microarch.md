## Q1: Whiteboard Explanation

Let me walk you through the wiring diagram of LightNobel.

**The Problem:** Protein Structure Prediction Models (PPMs) like AlphaFold2/ESMFold have a unique data structure called **Pair Representation** with dimensions (Ns, Ns, Hz), where Ns is sequence length. This means activation memory scales **quadratically** with sequence length. Figure 4 shows that at sequence length 2,034, activation size is already 24.15× larger than weights, requiring 144GB—exceeding a single GPU's capacity.

**The Core Mechanism (Figure 8):**

1. **Token Aligner** reads quantized token blocks from HBM, decodes them, and reorganizes into token-wise format in scratchpads (double-buffered).

2. **Reconfigurable Matrix Processing Unit (RMPU)** - The MAC engine:
   - Figure 9 shows the hierarchy: 16 multipliers → PE → 8 PEs form a PE Lane → 20 PE Lanes form a PE Cluster → 4 PE Clusters form the RMPU Engine
   - The **Reconfigurable Data Aligner (RDA)** splits all inputs into 4-bit chunks with sign extension
   - **Dynamic Accumulation Logic (DAL)** handles the mismatch between 4-to-1 and 5-to-1 adder trees depending on whether you have 4 or 5 outliers (Figure 9e)
   - Key insight: 20 PE Lanes is the LCM of 4 and 5, so they can support both configurations

3. **Versatile Vector Processing Unit (VVPU)** - Figure 10:
   - 128 SIMD lanes with local crossbar network
   - Handles LayerNorm, Softmax, residual connections
   - **Critically**: implements runtime top-k selection via bitonic sorting for dynamic outlier detection
   - Scalar Support Unit (SSU) handles quantization metadata formatting

4. **Global Crossbar Network (GCN)** connects everything, using swizzle switches for routing flexibility

**The Quantization Scheme (AAQ):**
- Activations are classified into 3 groups based on position in the dataflow (Figure 6):
  - **Group A** (pre-LayerNorm, residual): INT8 inliers + 4 outliers at INT16
  - **Group B** (post-LayerNorm, pre-Linear): INT4 inliers + 4 outliers at INT16
  - **Group C** (post-Linear): INT4 inliers, no outlier handling needed

---

## Q2: The Key Insight

**The "Magic Trick":** The paper exploits a **domain-specific property of PPM activations** that doesn't exist in LLMs: the token-wise "distogram pattern."

Figure 5 is the smoking gun. Unlike LLMs where outliers cluster in specific *channels* (prompting channel-wise quantization), PPM activations show:
- Small variance *between channels* (Figure 5a—all channels look similar)
- Large variance *between tokens* (Figure 5b—tokens at different (i,j) positions have vastly different ranges)

This happens because Pair Representation encodes **pairwise spatial relationships** between amino acids—a property unique to protein structure. The position-dependent variance follows the protein's distogram.

**Why this matters architecturally:** Token-wise quantization means each token gets its own scaling factor determined at runtime. This enables:
1. **No dequantization during MAC operations**: You can multiply quantized values directly and apply the scaling factor once at the end (Section 5.2)
2. **Dynamic top-k outlier selection per token**: Rather than predetermined thresholds, they run top-k at runtime (O(n log n), but n=128 is tiny)

The hardware consequence is the DAL (Dynamic Accumulation Logic) in Figure 9e—it selectively applies scaling factors to inlier accumulations before combining with outlier results, avoiding per-element dequantization.

**The "delta" vs. baseline:** Standard attention accelerators (Mokey, Olive, Tender) assume tensor-wise or channel-wise quantization with static outlier thresholds. LightNobel adds:
- Runtime top-k in the VVPU
- Multi-precision datapath reconfiguration (4-bit chunks with dynamic grouping to 4, 5, 8, or 16 PE Lanes)
- Per-token scaling factor storage and application

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Apples-to-apples accuracy comparison (Table 1, Figure 13):** They compare against 6 quantization schemes on identical datasets (CAMEO, CASP14, CASP15) using TM-Score. The result that Tender and MeFold fail catastrophically (TM-Score drops to 0.428-0.493) while AAQ maintains 0.539-0.802 is convincing validation that naive sub-INT8 quantization breaks PPM.

2. **Hardware baseline is state-of-the-art (Section 6):** Comparing against A100/H100 80GB PCIe variants with identical 80GB HBM2E memory is fair. Using CASP16 (ongoing competition) proteins demonstrates real-world relevance.

3. **Cross-validation of simulator (Section 6):** The 3.30% average discrepancy between Python simulator and RTL simulation provides confidence in cycle-accurate claims.

4. **Design space exploration transparency (Section 7, Figures 11-12):** They show *why* they chose INT8+4outliers for Group A vs INT4+4outliers for Group B vs INT4+0outliers for Group C. The efficiency-vs-TM-Score tradeoff curves are informative.

### Weaknesses

1. **The 28nm vs 7nm/4nm comparison is misleading:** Table 2 claims "21.94% of area and 19.37% of power compared to A100." But A100 is 7nm and H100 is 4nm. A 28nm design at 178.8mm² would be ~45mm² at 7nm (conservative 4× scaling). The power efficiency gains are inflated by comparing process nodes 2-3 generations apart.

2. **Memory bandwidth assumptions:** They use "80GB of 5 HBM2E memory stacks" at 2TB/s (Section 6), identical to baseline GPUs. But LightNobel at 28nm cannot physically integrate HBM2E—this would require an interposer or advanced packaging that isn't costed.

3. **End-to-end performance excludes Input Embedding:** Figure 14(a) shows LightNobel only accelerates the Protein Folding Block. For ESMFold, this is 83.8-94.5% of runtime (Figure 3), but the protein language model (ESM-2, 3B parameters) still runs on CPU. The 1.74× end-to-end speedup over ESMFold is dominated by this unaccelerated portion.

4. **Sequence length extrapolation (Figure 15b):** For sequences >3,360, they "estimate requirements by applying equivalent computational processes" rather than actual measurements. The 9,945-length support claim is simulated, not demonstrated.

5. **No comparison against multi-GPU baselines:** For long sequences, practitioners use chunking + multi-GPU. The paper only compares against single-GPU execution.

---

## Q4: What the Authors Didn't Tell You

**Hardware Costs They Glossed Over:**

1. **The Crossbar Network Dominates:** Table 2 reveals the Global Crossbar Network consumes 25.133mm² (70.28% of total area) and 9,215mW (67.95% of power). This isn't an accelerator for matrix multiplication—it's predominantly a crossbar switch with some MAC units attached. The GCN is necessary because token-wise quantization with dynamic outlier positions means data arrives out-of-order and must be routed dynamically.

2. **Top-k Sorting Latency:** Section 4.1 admits top-k selection is O(n log n). While they claim n=128 is "manageable," the VVPU must perform bitonic sort for *every token* during runtime quantization. With millions of tokens (Ns² where Ns can be 10,000), this adds up. The paper never breaks down what fraction of VVPU cycles go to top-k vs. other operations.

3. **Scratchpad Sizing is Optimistic:** Token Scratchpad is 128KB×2, Weight Scratchpad is 64KB (Table 2). For sequence length 10,000 with Hz=128, a single layer's Pair Representation is 10,000×10,000×128×2 bytes = 25.6GB. They rely entirely on streaming from HBM with double-buffering, meaning memory bandwidth is the true limiter.

4. **The "No Dequantization" Claim is Subtle:** Section 5.2 says RMPU "minimizes redundant dequantization." Looking at Figure 9(e), scale factors are still multiplied—they just do it once per PE Cluster output rather than per element. This is clever but not free; the DAL adds 4× 4-to-1 adder trees plus scale factor multipliers.

5. **Accuracy Numbers Hide Distribution:** Figure 13 shows *average* TM-Score. Section 2.4 notes TM-Score >0.5 indicates "strong structural similarity." The averages are 0.517-0.802, but what's the variance? Are there proteins where AAQ fails catastrophically?

6. **The 537 TOPS Compute:** Section 8.2 claims "only 537 TOPS of computational resources" outperforms A100 (624 TOPS) and H100 (3,026 TOPS). This is because PPM is memory-bound on GPUs (Figure 4 shows activation/weight ratio of 2,607× at 10,000 sequence length). The RMPU's 32 engines × 4 clusters × 20 lanes × 8 PEs × 16 MACs = 327,680 4-bit operations/cycle. At 1GHz, that's 327 TOPS in INT4-equivalent, suggesting their TOPS number mixes precisions.