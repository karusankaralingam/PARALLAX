## Q1: Whiteboard Explanation

Imagine you're trying to train a massive language model like GPT-3 on a wafer-scale chip (WSC)—essentially a giant piece of silicon the size of a dinner plate with dozens of dies (processing units) connected in a 2D grid.

**The Core Problem:** On a WSC, you can't just copy Megatron's parallelism strategy from GPU clusters. Why? Because:
1. **Memory vs. Compute Tradeoff:** Unlike GPU systems where you can add more memory externally, on a wafer, every square millimeter of memory steals from compute resources. You can't afford to replicate tensors everywhere.
2. **No Long-Distance Links:** You can't run a wire diagonally across a 215mm wafer—signal integrity degrades catastrophically beyond 50mm (bit error rate increases 10^8×, per Section II).

**The Old Way (Megatron-style):** When you split weights across dies, you still replicate activations. This wastes 2.1× memory (Figure 4c) and requires all-reduce operations that eat 40% of training time (Figure 4b).

**TEMP's Solution - Tensor Stream Partition (TSPP):** Instead of replicating, you cut *both* inputs and weights into non-overlapping chunks. Each die computes a small piece, then passes data to neighbors in a "stream." Think of it like a bucket brigade instead of everyone having their own water supply.

**But Here's the Catch:** TSPP naturally wants a ring topology. On a 2D mesh with no diagonal links, connecting Die 0 to Die 7 in a logical ring requires 7 physical hops (Figure 5a)—that's 7× the latency of adjacent dies.

**TEMP's Key Trick - Topology-Aware Tensor Partitioning (TATP):** Instead of a naive ring, TATP uses bidirectional "relay" communication. Die 3 doesn't wait for Die 0 to send W0 directly—it receives W2, W1, W0 in sequence from Die 2 via one-hop transfers at each step (Figure 8c). Every transfer is just one hop, eliminating tail latency.

**Sweet Spot:** The paper finds TATP works best at parallelism degrees of 8-16 dies (Figure 9). Below that, you don't get enough parallelism; above that, communication overhead dominates.

---

## Q2: The Key Insight

**The Central Insight:** Wafer-scale chips offer abundant die-to-die bandwidth but lack flexible long-distance interconnects—so you must *restructure the parallelism itself* to match the physical topology, rather than forcing GPU-style parallelism onto incompatible hardware.

**Why This Matters:**

The authors recognized that existing tensor parallelism (Megatron, FSDP) embeds an implicit assumption: any-to-any communication is cheap. On GPU clusters with NVSwitch, this holds. On a 2D mesh wafer, it's catastrophically wrong.

The key realization is that the "stream" paradigm from distributed GEMM algorithms (SUMMA, Cannon—references [14], [96], [117]) can be adapted, but *only if* you co-design the logical communication pattern with the physical topology. The naive ring-based stream creates O(N)-hop tail latency; TATP's bidirectional relay reduces this to O(1)-hop by doubling the data transfer volume but ensuring every transfer is between adjacent dies.

**The Implicit Tradeoff:** TATP deliberately trades bandwidth (sending data both directions redundantly) for latency predictability. This is viable *only* because WSCs have ~4 TB/s D2D bandwidth per die (Table I)—bandwidth is abundant, but hop count is catastrophic.

**Related Work Positioning:** PrimePar [118] attempted spatial-temporal partition but required diagonal links (Section IX). SP [52] and CP [65] reduce replication but still expose collective communication to computation time. TEMP is the first to explicitly design tensor parallelism around 2D mesh constraints.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Systematic Baseline Construction (Section VIII-A)**
The authors construct 6 baselines by crossing 3 partitioning schemes (Megatron-1, Megatron-3, FSDP) with 2 mapping strategies (SMap, GMap). This 3×2 construction is methodologically sound—it separates the contribution of TATP (partitioning) from TCME (mapping). The baselines include state-of-the-art systems like Megatron-LM and PyTorch FSDP.

**2. Multi-Dimensional Metrics**
They report not just throughput, but memory occupancy (Figure 13), power efficiency (Figure 14), and bandwidth utilization (Figure 4b). The power breakdown in Figure 14 is particularly valuable—it shows compute dominates (>50%), explaining why total power savings are "modest" while power *efficiency* improves 1.85×.

**3. Honest OOM Reporting**
Figure 13 explicitly marks OOM conditions for Megatron-1 baselines on larger models. This is transparent—many papers would simply omit these configurations.

**4. Ablation Study (Section VIII-C)**
Figure 16 isolates contributions: TATP provides 1.21× average improvement, TCME adds 1.14×. This multiplicative decomposition is clean.

**5. GPU Cluster Comparison (Figure 15)**
They compare against 32 A100 GPUs matched on theoretical FP16 peak performance. This is a meaningful iso-compute comparison.

### Weaknesses

**1. The Baseline Problem: GMap is Not State-of-the-Art for WSCs**

The authors admit GMap "fails to explore the vast mapping space and lacks contention-aware optimization" (Section VIII-A). This is essentially a strawman—they're claiming credit for fixing problems in a baseline they constructed to *have* those problems. The strongest baseline (FSDP+GMap) is still a GPU-oriented system naively ported to WSCs.

**Critical Missing Baseline:** There's no comparison against Cerebras's actual weight-streaming system [16], which is the only production WSC training system. Reference [16] describes weight streaming on real Cerebras hardware—why isn't it a baseline?

**2. Simulation-Only Evaluation**

All results come from ASTRA-sim [130], not real hardware. Section VIII-G validates the DNN cost model against simulation (Figure 21), but this is circular—they validate their model against their simulator. The correlation coefficients (0.988-0.997) and error rates (4.37-4.57%) look good, but without silicon validation, we don't know if ASTRA-sim itself is accurate for WSC workloads.

**Missing Validation:** They cite ASTRA-sim is "validated against real hardware" (Section VII-A), but the referenced validation [130] was for GPU clusters and chiplet systems, not heterogeneously-integrated wafer-scale chips.

**3. Cherry-Picked Model Selection**

Table II shows 6 models: GPT-3 (3 sizes), Llama2/3, OPT. These are all decoder-only Transformers with very similar architectures. What about:
- **Encoder-decoder models** (T5, BART) with different attention patterns?
- **Mixture-of-Experts models** (DeepSeek is mentioned but not evaluated despite being cited in the intro)?
- **Vision Transformers** or multimodal models with different compute/memory ratios?

**4. The "Sweet Spot" Analysis is Under-Explored**

Figure 9 shows the optimal TATP degree is 8-16 dies. But this analysis uses *one* workload ("one GPT-3 175B linear layer"). The claim that this generalizes across models needs more support. Figure 18 shows optimal TATP is consistently 8 or 16 across GPT-3 variants, but this is a narrow model family.

**5. Traffic Contention Mitigation is Not Quantified in Isolation**

Section VI-B describes the traffic-conscious optimizer, and Figure 11 shows an example, but there's no figure isolating how much contention reduction TCME achieves before/after optimization. The 14% reduction in collective communication latency (Section VIII-B) conflates TATP and TCME contributions.

**6. Multi-Wafer Results are Thin**

Figure 19 shows 4 models on 2-6 wafers with pipeline parallelism. But the inter-wafer bandwidth assumption (9 TB/s, citing Tesla Dojo [109]) is extremely optimistic. Section VIII-I mentions this enables "cross-WSC deployment," but the evaluation doesn't stress-test lower inter-wafer bandwidths that real systems might have.

**7. Sequence Length Coverage**

Figures 17-18 test S=2K and S=16K. But modern LLM inference often targets 128K+ context (Claude, GPT-4-Turbo). The paper's maximum sequence length (16K) is relatively modest.

---

## Q4: What the Authors Didn't Tell You

**1. The Real TSPP Overhead: Doubled Communication Volume**

TATP's bidirectional relay strategy sends each sub-tensor *both* directions (Algorithm 1, lines 6-9). For N dies, naive ring transfers O(N) chunks total; TATP transfers roughly O(2N) chunks but in O(N) steps with O(1)-hop each. The paper never quantifies this bandwidth overhead explicitly. Section V mentions TATP "deliberately trades bandwidth for latency predictability" but doesn't show where this trade-off hurts—presumably at very high die counts where bandwidth becomes scarce.

**2. The Activation Checkpointing Question**

Modern training systems (Megatron-3, FSDP) heavily use activation checkpointing/recomputation to reduce memory. The paper mentions "activation recomputation" in passing (Section II-A references [52]) but never clarifies whether their baseline includes it. If baselines use checkpointing and TEMP doesn't (relying on TSPP's memory efficiency instead), the comparison may conflate memory savings from different mechanisms.

**3. The HBM vs. SRAM Hierarchy**

Table I shows 72GB HBM per die vs. 80MB SRAM. The paper's memory analysis (Figure 4c, Figure 13) reports total memory usage but never breaks down *where* tensors reside. TSPP's fine-grained streaming likely requires activations to live in SRAM during computation—if intermediate tensors spill to HBM, the 100ns HBM latency (Table I) could dominate the 200ns D2D latency.

**4. The Yield Problem**

Section VIII-F mentions fault tolerance and shows throughput gracefully degrades with core faults (Figure 20c). But wafer-scale chips have fundamental yield concerns—not every die works. The paper assumes a perfect 6×8=48 die array. What happens with realistic yield? The fault tolerance section tests *random* faults, but real yield issues create *clustered* dead regions that could break TATP's rectangular parallel groups.

**5. The Search Time Claims Need Context**

Section VIII-H claims their algorithm is "200× faster than ILP." But the comparison point is [144] (Alpa) running on an Intel Xeon CPU. Alpa solves a different problem (inter/intra-operator parallelism for GPU clusters) with different constraints. A fairer comparison would be ILP solving the *same* WSC mapping problem with the *same* cost model.

**6. Compute Utilization is Not Directly Reported**

Figure 4b shows bandwidth utilization (33-53%). Figure 7c shows compute utilization dropping to ~70% at large scales. But Section VIII doesn't report achieved compute utilization for their evaluated configurations. The 1.7× speedup claim (abstract) could come from better utilization *or* reduced communication—we can't tell which dominates without explicit utilization numbers.

**7. The Power Model is Coarse**

Section VII-A says power = compute + memory + communication, with compute at "2 TFLOPS/Watt" (Table I). But modern AI accelerators have significant power variation with workload intensity, thermal throttling, and voltage/frequency scaling. The paper treats power as a linear function of operations, which may underestimate dynamic effects.

**8. No Convergence Verification**

The entire evaluation is throughput/samples-per-second. There's no verification that TSPP's fine-grained tensor streaming produces numerically identical results to baseline implementations. While they claim to use FlashAttention with online softmax "to guarantee correctness" (Section VII-A), TSPP changes the order of partial sum accumulations—numerical precision effects (especially in FP16) could affect training dynamics.