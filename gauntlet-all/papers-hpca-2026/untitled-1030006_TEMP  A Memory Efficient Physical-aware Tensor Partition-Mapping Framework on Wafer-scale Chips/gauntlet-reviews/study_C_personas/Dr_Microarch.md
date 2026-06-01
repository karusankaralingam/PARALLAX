# TEMP: Tensor-Stream Partition for Wafer-Scale Chips

## Q1: Whiteboard Explanation

Let me draw this out for you. The problem starts with a fundamental constraint that the authors exploit.

**The Wafer-Scale Reality:**
- You have a 215mm × 215mm wafer with a 6×8 array of dies arranged in a 2D mesh (Figure 3)
- Each die has ~72GB HBM and 1800 TFLOPS compute
- Die-to-die (D2D) links give you 4TB/s bandwidth but **only between physically adjacent dies**
- Critical constraint: Signal integrity degrades catastrophically beyond 50mm (Figure 7b shows 10^8× BER increase), so no diagonal or long-distance links are physically possible

**The Existing Problem (Figure 4a):**
Megatron-style tensor parallelism partitions weights across dies but **replicates activations**. This causes:
1. 2.1× memory overhead (activations duplicated everywhere)
2. 40% of training time spent on collective communication (All-Reduce)
3. Only ~40-53% D2D bandwidth utilization

**The Core "Trick" - TSPP (Tensor Stream Partition Parallelism):**
Instead of keeping tensors stationary and doing bulk All-Reduce, you:
1. Partition BOTH inputs and weights into non-overlapping sub-tensors
2. Stream sub-tensors between dies while computing on others
3. Overlap communication with computation

For a GEMM O = I × W across 4 dies:
- Die i holds sub-input I_i and sub-weight W_i
- Over 4 rounds, each die computes one partial output per round
- Sub-weights stream in a "ring" pattern while computation proceeds

**The WSC-Specific Problem (Figure 5a):**
A naive ring implementation is disastrous. If you logically connect Dies 0→1→2→3→4→5→6→7 in a ring, the "wrap-around" from Die 7 back to Die 0 requires **7 physical hops** while adjacent transfers take 1 hop. This creates 7× tail latency.

**TATP - The Topology-Aware Solution (Figure 8):**
The key insight is a **bidirectional redundant-transfer orchestration**:
- Instead of a unidirectional ring (which needs wrap-around), stream in BOTH directions simultaneously
- Dies 0 to N/2-1 receive data flowing "forward"
- Dies N/2 to N-1 receive data flowing "backward"
- Every transfer is now exactly 1 physical hop
- Algorithm 1 details the precise scheduling: each die computes and relays simultaneously

**The "Sweet Spot" (Figure 9):**
TATP parallel degree of 8-16 dies is optimal. Below that, you don't get enough overlap benefit. Above that, sub-tensors become too fine-grained and communication time exceeds compute time.

---

## Q2: The Key Insight

**The One Clever Hardware Insight:**

The authors recognized that wafer-scale chips have an **inverted bottleneck profile** compared to GPU clusters: abundant D2D bandwidth (4TB/s per link) but severely constrained on-chip memory (both SRAM and HBM share limited wafer area). Existing parallelism strategies were designed for GPU clusters where the opposite is true (memory is relatively cheap, inter-node bandwidth is the bottleneck).

The magic trick is **trading redundant communication for zero memory replication** by exploiting the mesh topology's nearest-neighbor bandwidth. Rather than storing replicated tensors and doing expensive collective operations, you stream sub-tensors through the mesh while computing, ensuring:

1. **No tensor replication** → Memory usage drops to theoretical minimum
2. **Communication hidden behind computation** → High bandwidth utilization
3. **All transfers are single-hop** → No tail latency from topology mismatch

The bidirectional streaming pattern (Algorithm 1) is the specific mechanism that makes this work on a 2D mesh without requiring wrap-around links. It's essentially a **double-buffered, spatially-aware SUMMA variant** [references 14, 96, 117 in paper] adapted to the physical constraints of wafer-scale integration.

**Why this matters architecturally:**
The 50mm signal integrity limit (Section II, Figure 7b) makes torus links impossible at wafer scale. This forces all prior ring-based collective algorithms to suffer O(N)-hop worst-case latency. TATP restructures the computation order so that the *logical* ring never requires *physical* long-distance links.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Baseline Construction (Section VIII-A)**
The 3×2 baseline matrix (Megatron-1, Megatron-3, FSDP) × (SMap, GMap) is methodologically sound. They're comparing against real frameworks (Megatron, PyTorch FSDP) with both naive and intelligent mapping, not strawmen.

**2. The Ablation Actually Isolates Components (Figure 16)**
TATP alone gives 1.21× average speedup; TCME adds another 1.14×. This shows both contributions are independently valuable.

**3. Direct GPU Comparison (Figure 15)**
Comparing a 32-die WSC against a 4-node A100 cluster (matched FLOPS) is fair. The finding that WSC+MeSP *loses* to GPU+MeSP but WSC+TEMP *wins* is compelling—it shows the framework is necessary, not just the hardware.

**4. Scaling to Multi-Wafer (Figure 19)**
Testing on 2-6 wafers with up to 504B parameters demonstrates the framework doesn't break at scale.

**5. Fault Tolerance Analysis (Figure 20)**
The throughput vs. fault rate curves are valuable. 80% throughput at 25% core fault rate is impressive resilience.

### Weaknesses

**1. Simulation-Only Evaluation**
All results are from ASTRA-sim, not real silicon. The authors acknowledge this (Section VIII-A) but the DNN-based cost model (Section VII-A) adds another layer of approximation. Figure 21 shows 4.38-4.57% error rates, but these compound across operators.

**2. The "Sweet Spot" Claim Needs More Support (Figure 9)**
The TATP degree of 8-16 is claimed optimal, but this is shown for only one configuration (GPT-3 175B linear layer). Different operator shapes, batch sizes, and sequence lengths could shift this. The paper doesn't systematically explore the sensitivity.

**3. Communication Optimizer Convergence Not Proven (Section VI-B)**
The iterative rerouting algorithm (Figure 11d) terminates when "load improvement stagnates" but no convergence guarantees are provided. For adversarial workload mixes, this could fail to find good solutions.

**4. Missing Comparison Against FRED [94]**
The related work mentions FRED (another WSC-targeted distributed training framework), but it's not included in the baselines. Given FRED also targets wafer-scale with communication optimization, this is a notable omission.

**5. Power Numbers Are Suspicious (Figure 14)**
Total power savings of only ~5-12% while communication power drops 11-24% suggests the power model may be incomplete. At 5pJ/bit D2D power (Table I), reduced communication should have larger impact unless compute power dominates more than the 50% claimed.

**6. Search Time Comparison Incomplete (Section VIII-H)**
They claim >200× speedup over ILP but don't compare against other heuristic methods (simulated annealing, beam search). The ILP baseline may be artificially slow.

---

## Q4: What the Authors Didn't Tell You

### Hardware Costs They Glossed Over

**1. Buffer Requirements for TATP**
The bidirectional streaming in TATP (Algorithm 1) requires double-buffering: while computing on one sub-tensor, the next must be arriving. For a TATP degree of 16 on GPT-3 175B, each sub-weight chunk is ~12GB/16 = 750MB. Double-buffering means 1.5GB of dedicated SRAM per die just for streaming buffers. They never quantify this.

**2. The DMA and NoC Overhead**
Figure 3 shows each die has DMA controllers and NoC routers, but the paper assumes these are "free"—no contention modeling for DMA queues, no NoC bandwidth sharing between TATP streams and other traffic.

**3. TCME Runtime Overhead**
The traffic-conscious communication optimizer (Section VI-B) runs "iteratively" with five phases. This is compile-time cost, but they don't report how long it takes. For dynamic workloads or fine-tuning scenarios, this matters.

### The Roofline They Don't Show

The paper never presents a roofline analysis. With 4TB/s D2D bandwidth and 1800 TFLOPS compute per die, the arithmetic intensity threshold is:
```
1800 TFLOPS / 4 TB/s = 450 FLOP/byte
```
LLM linear layers typically have arithmetic intensity of 50-200 FLOP/byte. This means the system is **memory-bound**, not compute-bound, which explains why TSPP (which trades compute for memory) helps. But they never make this explicit.

### The Implicit Assumptions

**1. Homogeneous Dies**
The entire framework assumes all dies are identical. Real wafer-scale fabrication has yield issues—some dies will be slower or faulty. Their fault tolerance section (Figure 20) handles complete failures but not performance variation.

**2. Static Workloads**
TEMP optimizes for a fixed model architecture and batch size. Dynamic batching, speculative decoding, or mixture-of-experts workloads would require re-running the dual-level solver.

**3. The HBM Bandwidth Assumption**
Table I claims 0.8-1 TB/s HBM bandwidth per die, but HBM3 specifications show this requires 8-12 stacks per die. The 210mm² HBM die area suggests only 3-4 stacks physically fit. The bandwidth numbers may be aspirational.

### What the "1.7× Speedup" Actually Means

Decomposing Figure 13:
- Communication latency reduced by 14-38%
- Computation latency roughly unchanged
- Memory reduced by 18-51%

The speedup comes almost entirely from communication reduction, not computation improvement. On a different topology (e.g., future wafers with optical links enabling long-distance connections), TATP's advantage would diminish.

### The Search Space They Claim to Explore

Section VII-B claims the DLS algorithm avoids O(N^2m) complexity by partitioning the graph. But the genetic algorithm component (line 13 of Figure 12b) still has unbounded search time depending on convergence criteria. The "200× faster than ILP" claim (Section VIII-H) should be taken with caveats about solution quality, which they don't compare.