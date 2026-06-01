# TRACI Paper Audit: Evaluation Methodology Critique

## Q1: Whiteboard Explanation

Let me walk you through what TRACI actually does.

**The Problem Setup:**
Deep Learning Recommendation Models (DLRMs) have embedding tables that can be terabytes in size. You *must* distribute them across multiple GPUs. When you process a batch, each GPU needs embedding vectors that live on *other* GPUs. This cross-GPU communication—called "Aggregation"—dominates execution time (Figure 3 shows 60-90% of time in some configurations).

**The Core Operation:**
Aggregation is NOT like All-Reduce. In All-Reduce (Figure 1a), every GPU contributes one vector X and receives the same result Y. The pattern is static and predictable. In Aggregation (Figure 1b), the pattern depends on the *input data*—which user IDs and item IDs appear in this batch. GPU 1 might need vectors from GPUs 3, 7, and 42, while GPU 2 needs vectors from GPUs 2, 5, and 99.

**The Two Reuse Opportunities:**
1. **Input Reuse**: The same embedding vector X might be requested by multiple GPUs. Instead of sending X three times, cache it in the network switch.
2. **Output Reuse**: Multiple embedding vectors might all reduce to the same output location Y. Instead of sending them all to GPU Y, reduce them *in the switch* and send one result.

**The TRACI Solution:**
1. A new memory primitive called `GetReduce` that carries both the input address (IAddr) and output address (OAddr) in each message—essential for the switch to discover reuse relationships.
2. **In-Switch Cache (ISC)**: When a response passes through a switch, cache the data. Future requests for the same IAddr get answered directly by the switch (Figure 8).
3. **Reduction Table (RTB)**: Track pending requests by OAddr. When multiple responses arrive destined for the same OAddr, reduce them in the switch before forwarding (Figure 7).

The key architectural insight: by putting both IAddr and OAddr in the network transaction, the switches can *dynamically discover* reuse on-the-fly without any pre-analysis of the input batch.

---

## Q2: The Key Insight

**The authors' stated insight (Section 2.3):** "Harnessing either reuse type can theoretically provide larger than 3× bi-sectional traffic reduction in Aggregation."

**The actual deeper insight:** Prior work exploiting input or output reuse did it *at the GPU endpoints*—before transmission or after reception. This forces a choice: exploit output reuse (combine before sending) OR input reuse (multicast after receiving), but not both. The paper's real contribution is recognizing that *moving the optimization into the network switches* eliminates this conflict because switches sit in the middle of the data path.

**The critical technical enabler:** The `GetReduce` transaction design (Section 4). Existing shared-memory operations like `Get` carry only the source address—a point-to-point semantic. By adding the output address (OAddr), messages now carry enough metadata for switches to:
- Group requests by OAddr (for reduction)
- Match responses by IAddr (for caching)
- Handle dynamic reduction counts via a counter mechanism (the "arrived count" field)

**However, let me note the uncomfortable truth:** This insight is elegant but constrained. The 3× theoretical reduction in Table 1 comes from specific assumptions about uniform random distribution across GPUs. Real workload skew will erode these gains significantly—which the authors quietly acknowledge by showing highly variable speedups across datasets in Figure 10.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### **Strengths**

1. **Diverse Benchmark Suite (Table 3):** The authors evaluate 23 datasets across three categories: Facebook synthetic (17 datasets), CTR applications (Kaggle, Avazu, Terabyte), and web-review applications (Amazon, LastFM, DBLP). This is commendable breadth.

2. **Ablation Study Done Right (Section 6.2):** Figure 10 breaks down "Cache Only," "Reduction Only," and "Cache + Reduction." This reveals that the two mechanisms have complementary strengths:
   - CTR datasets (one-hot): reduction provides zero benefit, cache provides all gains
   - Facebook synthetic: reduction dominates at 16 GPUs, cache matters more at 64+ GPUs
   This transparency is valuable.

3. **Sensitivity Analysis (Section 6.4):** Figure 14 shows speedup vs. cache/RTB size. Figure 15 shows scaling behavior across GPU counts. Figure 16 reveals RTB miss rates at scale. This is the kind of honest analysis that builds confidence.

4. **Alternative Topology Evaluation (Figure 12):** Testing on a 4×4×4 3D mesh (TPU-style) shows the design isn't overly specialized to fat-tree. Speedups drop to 1.32× average, which is honest reporting.

### **Weaknesses**

1. **The Simulation-Only Problem:** All results come from gem5 Garnet simulations (Section 6.1). There is no FPGA prototype, no real switch implementation, no actual hardware validation. For a paper claiming specific cycle-accurate latency improvements, this is a significant gap.

2. **The Baseline May Be Too Weak:** The baseline is a simple `Get` operation with no optimization (Section 2.5). But industry systems already employ software optimizations:
   - TorchRec [33] does table-wise and column-wise partitioning
   - HugeCTR does hierarchical embedding aggregation
   
   The paper dismisses these in Section 2.4 by claiming row-wise partitioning is "most scalable," but doesn't benchmark against systems using these optimizations.

3. **The CTR Benchmark Cherry-Pick:** Look carefully at Table 3: CTR datasets have **average pooling size of 1** (they're one-hot). This means zero output reuse by definition. For Terabyte specifically, Table 1 shows "Avg. output reuse degree = 1" and "Bi-sec traffic reduction from output reuse = 1×" (no reduction at all). Yet the paper still reports aggregate "gmean" numbers that include CTR datasets—diluting the apparent benefit.

4. **The 256-GPU Cliff (Figure 10):** Look at the "Cache + Reduction" bars across system sizes:
   - 16 GPUs: ~2.25× gmean
   - 64 GPUs: ~3.12× gmean
   - 256 GPUs: ~2.0× gmean (dropping!)
   
   The paper buries this in text: "the reduction table size is not large enough and some packets are bypassed" (Section 6.4.2). At the scale where TRACI should matter most, it loses effectiveness. Figure 16 confirms RTB miss rates spike at 128-256 GPUs.

5. **End-to-End Numbers Are Extrapolated:** Figure 17 shows "Application Speedup" but Section 6.5 admits: "We use Astra-sim to estimate the percentage of end-to-end latency spent on embedding communication." The embedding speedups are from gem5, MLP estimates are from Astra-sim, and they're *combined* to derive end-to-end speedup. This is not a real end-to-end measurement.

6. **Training Evaluation is Sparse:** Section 6.2's "Training results" (Figure 11) only evaluates 3 datasets (one per category) with 2 batch sizes. Compare this to inference which gets 23 datasets × 3 scenarios × 3 system sizes in Figures 10a-c.

7. **Batch Size Inconsistency:** The paper evaluates batch sizes of 8 and 128. Production DLRM systems typically use batch sizes of 1024-8192 for throughput. The paper doesn't explain why these small batch sizes were chosen, and larger batches would likely show different reuse characteristics.

---

## Q4: What the Authors Didn't Tell You

**1. The Coherence Model is Application-Specific:**
Section 5.3.2 casually states: "Our method for coherence handling is to invalidate all cache blocks whenever a multi-GPU synchronization happens." This works for DLRM training where batches are naturally synchronized, but it means TRACI's in-switch cache provides **zero benefit** across batch boundaries. For inference serving with streaming requests (no batching), input reuse across different requests is impossible. The high speedups for "inference without batching" (Figure 10a) are actually measuring *within-sample* reuse, not *across-sample* reuse.

**2. The GetReduce Primitive Requires GPU Architecture Changes:**
Section 4 describes GetReduce as a new memory operation that "GPU threads can issue." This requires changes to:
- The GPU ISA
- The GPU memory controller
- The NVLink interface
- The NVSwitch firmware/hardware

The paper treats this as a minor software change ("The only change in software is to re-implement the embedding layer," Section 3), but it's actually a GPU hardware change. NVIDIA would need to add this instruction to future architectures.

**3. Deadlock Prevention Has Performance Costs:**
Section 5.2.2 admits that when RTB is full, requests from other switches must be "bypassed" to avoid deadlock. This means reduction opportunities are lost exactly when traffic is heavy—which is when you need them most. The paper doesn't quantify how often this bypass happens under realistic load.

**4. The "Theoretical" Table 1 Numbers Don't Match Reality:**
Table 1 claims 3.16× traffic reduction from output reuse for LastFM. But Figure 10 shows actual speedups of only ~1.5-2.5× for LastFM across different configurations. The theoretical analysis assumes uniform random distribution; real embeddings have skewed access patterns.

**5. Floating Point Reduction Ordering:**
Section 5.2.1 describes switches performing "an addition between the data in the RTB entry and the data carried by the incoming packet." Floating-point addition is not associative. The final result depends on the order packets arrive at the switch. The paper never discusses:
- Whether this introduces numerical differences
- Whether those differences affect model convergence
- Whether the results are reproducible

**6. The Hardware Overhead Numbers Hide Power:**
Table 4 reports 2.82% area overhead for cache + RTB. But these are SRAM structures that must be clocked and accessed every cycle for every packet. The paper reports no power analysis. At 64 GB/sec per link (Table 2) with 64-byte flits, each switch processes ~1 billion flits/second. That's significant dynamic power in the reduction ALUs and cache lookups.

**7. The Comparison to CPU-GPU Systems is Self-Serving:**
Section 7's "Comparison to CPU-GPU hybrid systems" argues GPU-only systems have "better performance and scalability." But it admits they're "more expensive." For many recommendation workloads, cost per query matters more than raw throughput. The paper doesn't compare $/QPS metrics.