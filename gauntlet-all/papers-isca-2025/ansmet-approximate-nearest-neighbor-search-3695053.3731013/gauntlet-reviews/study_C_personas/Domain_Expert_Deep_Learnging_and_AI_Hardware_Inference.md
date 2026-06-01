# Paper Deconstruction: ANSMET (ISCA '25)

## Q1: Whiteboard Explanation

Alright, let me sketch this out for you.

**The Problem:** You have a billion vectors, each with 100-1000 dimensions (think: embeddings from a language model or image encoder). When a query arrives, you need to find the *k* closest vectors. Even with clever indexing (like HNSW graphs that let you skip most vectors), you still visit hundreds of vectors per query. Each vector is hundreds of bytes. That's a *lot* of memory traffic for what amounts to computing some dot products and comparisons.

**The Core Observation (Figure 1, page 4):** The authors profile FAISS on a CPU and find two damning facts:
1. Distance comparison dominates execution time (memory-bound, low arithmetic intensity ~0.125 ops/byte)
2. **50-90% of compared vectors are "rejected"** — meaning you fetched the whole vector, computed the distance, and then discovered it wasn't close enough. All that memory bandwidth? Wasted.

**The "Napkin Sketch" Solution:**

Imagine fetching a vector bit-by-bit, from most significant to least significant. After fetching just the top 4 bits of each dimension, you can compute a *lower bound* on the distance. If that lower bound already exceeds your current threshold (the k-th smallest distance found so far), you can **stop fetching the rest of the vector**. You just saved 75%+ of the memory accesses for that vector.

The trick is: how do you compute a valid lower bound from partial bits? For Euclidean distance, if you've fetched the prefix `01__` and the query has `0101`, the minimum possible difference occurs when the missing bits exactly match the query. If the prefix already diverges (`11__` vs. `0101`), the best case is setting the missing bits to minimize the gap (all zeros → `1100`).

**The System (Figure 5, page 9):**
- **Host CPU:** Runs the HNSW graph traversal (irregular, control-heavy)
- **NDP Units (in DIMM buffer chips):** Perform distance computations right next to the DRAM, exploiting 8× higher internal bandwidth than the CPU sees over the memory bus
- **The Handoff:** CPU says "compare query Q against vectors at addresses A, B, C with threshold T." NDP units fetch data, compute partial distances, trigger early termination if possible, and report back distances (or "rejected").

**Data Layout Transformation (Section 4.2):** To make early termination work, you can't store vectors in the naive layout `[dim0_full, dim1_full, ...]`. Instead, you transform to `[MSB_of_all_dims, next_bits_of_all_dims, ...]`. This way, each 64B fetch gives you partial bits from *many* dimensions, enabling better distance bounds early.

---

## Q2: The Key Insight

**The Delta (What's Actually New):**

The *real* contribution is the **hybrid partial-dimension/bit early termination scheme with hardware support** — and critically, the observation that this works *multiplicatively* with NDP.

Prior work did:
- Partial-*dimension* early termination (fetch first N dimensions, estimate distance) — but this fails for inner-product distances where unfetched dimensions can contribute negative values
- Bit-serial computation (BitNN) — but with a fixed 1-bit step, which wastes most of each 64B fetch when dimensions are few

ANSMET's insight: **combine partial dimensions AND partial bits**, with a data layout that packs the most-significant bits of many dimensions into each 64B fetch. This gives a much tighter distance lower bound early, triggering more terminations sooner.

**The Magic Trick (Figure 3, page 7):**

The authors discovered a pattern across datasets: bit prefixes have three regimes:
1. **Low-entropy zone (high bits):** Most vectors share the same prefix here (e.g., all FP32 values normalized to similar ranges). Skip this fast.
2. **High-termination zone (middle bits):** This is where the discriminative information lives. Move slowly here — each additional bit can trigger termination.
3. **Low-impact zone (low bits):** Few terminations happen here because these bits barely affect the distance.

This motivates their **dual-granularity fetch**: large bit chunks initially (skip common prefixes), then fine-grained chunks in the discriminative range.

**Guarantee of No Accuracy Loss:** Because they always use *lower bounds*, any vector that passes the threshold check with partial data will also pass (or get re-checked) with full data. This is mathematically sound — no ML-based prediction, no approximation.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Apples-to-Apples NDP Comparison (Table 1, Figure 6):**
   - They use a consistent 4-channel DDR5-4800 system with 8 DIMMs (32 ranks) across all configurations
   - CPU baseline uses 16 OoO cores at 3.2GHz — not a strawman
   - They report cycle-accurate simulation (Ramulator 2.0), not back-of-envelope estimates

2. **Comprehensive Dataset Coverage (Table 2):**
   - 7 datasets spanning UINT8/INT8/FP32, 96-960 dimensions, 1M-1B vectors
   - Both L2 and inner-product distances
   - They explicitly test datasets where their approach should struggle (e.g., SIFT/BigANN with UINT8 have minimal prefix elimination benefit — and they show only ~10% improvement there, Figure 6)

3. **Honest Breakdown of Contributions (Figure 6):**
   - They separate NDP-Base (5.26× from bandwidth alone) from early termination (additional 1.52×)
   - They show the contribution of each optimization: simple ET → dual-granularity → prefix elimination
   - This is good practice — you can see exactly what each technique buys

4. **Energy Results (Figure 7):**
   - NDP-Base saves 77.8% energy over CPU-Base
   - They note that NDP-Base *without* early termination can consume *more memory energy* than CPU-ET for some datasets (DEEP, Txt2Img, GIST) — an honest admission

5. **Preprocessing Cost Transparency (Table 4):**
   - Data layout transformation adds only 1.6% overhead to HNSW graph construction
   - Sampling uses just 100 vectors — modest cost

### Weaknesses

1. **No Comparison Against GPU Baselines:**
   - Section 8 acknowledges GPUs (FAISS-GPU, CAGRA, SONG) but doesn't benchmark against them
   - An A100 with 2TB/s HBM bandwidth would be a formidable competitor for memory-bound ANNS
   - The authors' implicit argument is capacity (HBM limited to ~80GB vs. terabyte DIMM systems), but they should quantify the crossover point

2. **Single Index Algorithm (HNSW):**
   - They claim generality to IVF and other indexes (Section 4.1) but only evaluate HNSW
   - IVF has different access patterns (sequential cluster scans vs. graph traversal) — early termination effectiveness may differ

3. **Fixed 80% Recall Target (Section 6):**
   - They tune `efSearch` until recall ≥ 80%, then compare performance
   - But 80% recall is often insufficient for production (many applications need 95%+)
   - Figure 8 shows the recall-QPS tradeoff, but the gap between NDP-ETOpt and NDP-Base shrinks at higher recalls — the benefit of early termination diminishes when you must keep more candidates

4. **Scalability Ceiling (Table 3, page 13):**
   - Performance scales from 8→32 NDP units (1.94× to 6.04×)
   - But 32→64 units yields only 6.04× to 7.60× — diminishing returns
   - They attribute this to "limited parallelism in the index algorithm" — but this is a fundamental limitation for single-query latency

5. **Simulation-Only Evaluation:**
   - No FPGA prototype or real silicon
   - NDP unit area (0.06mm²) and power (300mW) are estimated from CACTI at 22nm — reasonable but unvalidated
   - The host-NDP coordination (polling, instruction encoding) is simulated but not stress-tested with real memory controller quirks

6. **Load Imbalance Mitigation via Replication (Section 5.3):**
   - They replicate "top four HNSW layers" (5.27MB, 0.14% of data) to reduce imbalance from 1.49× to 1.05×
   - But this is specific to HNSW's hierarchical structure — what about IVF or other indexes?
   - Zipf-skewed queries reduce imbalance from 2.19× to 1.09× — good, but real workloads may have different skew patterns

---

## Q4: What the Authors Didn't Tell You

1. **The Outlier Problem Is More Severe Than Presented:**
   - Table 5 shows that allowing 0.1% outliers requires storing *backup copies* of non-compressed vectors
   - If you don't keep backups, accuracy drops by **34.7%** (!)
   - This means their "no accuracy loss" claim requires 1.1% extra storage and 1.4% extra memory accesses
   - For billion-scale datasets, 1.4% extra accesses is millions of additional fetches

2. **Inner-Product Distance Has Fundamental Limitations:**
   - They note (page 7) that partial-dimension ET doesn't work for inner-product because unfetched dimensions can contribute negative values
   - Their bit-level ET *does* work, but the termination frequency is lower (visible in GloVe/Txt2Img results in Figure 6 — smaller improvements than L2 datasets)
   - Many modern embedding models (e.g., sentence transformers) use cosine similarity — which requires normalization that may disrupt prefix patterns

3. **The "Adaptive Polling" Is a Patch, Not a Solution:**
   - Section 5.4 describes polling the NDP units to retrieve results
   - They estimate when to poll based on the early termination distribution from sampling
   - But Figure 9 shows conventional polling adds 13% overhead; adaptive polling reduces this by 62% but still has 5.9% overhead
   - A proper interrupt mechanism (like MEDAL's RFU pin approach) would eliminate this entirely — but they explicitly avoid modifying DDR protocols

4. **Product Quantization Compatibility Is Limited (Section 4.3):**
   - They admit: "partial bits of the codewords are not useful"
   - Many production ANNS systems use PQ heavily (e.g., FAISS IVF-PQ)
   - ANSMET's bit-level early termination is incompatible with PQ's lookup-table-based distance computation
   - This limits deployment scenarios

5. **The 5.26× NDP Speedup Isn't From Their Innovation:**
   - The 5.26× average speedup (NDP-Base vs. CPU-Base) comes entirely from prior work on DIMM-based NDP
   - ANSMET's *novel* contribution (early termination) adds only 1.52× on top
   - The paper's framing emphasizes the combined 8× speedup, but the truly new part is the smaller multiplier

6. **What Happens With Concurrent Queries?**
   - Each QSHR handles one query at a time with 8 comparison tasks (Section 5.1)
   - 32 QSHRs per NDP unit × 32 NDP units = 1024 concurrent query slots
   - But real vector databases serve thousands of QPS — what's the queuing model?
   - They never discuss batching across queries or the impact on per-query latency vs. throughput

7. **The Common Prefix Assumption May Not Hold:**
   - Their dual-granularity fetch assumes a "low-entropy" zone in the high bits (Figure 3)
   - This works for normalized embeddings (DEEP, GIST) but may fail for:
     - Unnormalized activations
     - Mixed-distribution datasets
     - Dynamic datasets where the distribution shifts over time
   - The sampling-based approach uses only 100 vectors — outlier distributions could break the learned parameters