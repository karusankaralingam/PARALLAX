# Cambricon-SR: A Toolsmith's Analysis

## Q1: Whiteboard Explanation

Let me walk you through what this paper actually does, starting from first principles.

**The Problem:** Neural Scene Representation (NSR) learns a 3D representation of a scene from 2D photos. Think of it as training a neural network to "remember" a room so you can render it from any viewpoint. The bottleneck is the **encoding table**—a massive lookup table (128 MB dense) that stores learned features. Every training iteration requires millions of irregular, fine-grained memory accesses to this table.

**The Core Insight:** Most entries in this encoding table are near-zero and don't contribute meaningfully to the scene. The authors observe that >80% of entries can be pruned with negligible quality loss (Figure 4 shows PSNR drops <0.5 dB at these sparsity levels).

**The Architecture in Three Parts:**

1. **Sparse Index Unit (SIU):** The challenge is that hash functions produce *irregular* addresses—you can't predict which entries will be accessed. Rather than using a 2048×2048 crossbar (which EDA tools couldn't even route), they sequentially scan a bitmap and match against buffered requests. Incoming requests are grouped by "nearest SAU" to minimize comparisons.

2. **Sparse Update Unit:** The dense table (DT) must live off-chip (128 MB), but only ~0.8% of entries change sparsity state per iteration. They use CAM (Content Addressable Memory) for address translation and do in-place replacement of entries via two queues (Queue A for sparse→dense, Queue B for dense→sparse).

3. **Dynamic Shared Buffer for MLPs:** The number of sampled points per ray varies wildly, but Cambricon-R allocated buffers for the *maximum*. Average utilization was only 20.29%. Sharing buffers across 32 MLP units reduces capacity per unit by 85.3%, enabling 4× more MLP units.

**The Dataflow:** Forward/backward stages keep the sparse table (~26 MB) and gradients (~14 MB) on-chip. The update stage incrementally modifies the on-chip sparse table using off-chip dense table reads—but only for changed entries.

---

## Q2: The Key Insight

**The key insight is that encoding table sparsity in NSR is not just about storage compression—it's about eliminating the *memory access requests themselves* before they reach the memory system.**

Previous work (Cambricon-R) stored the entire dense table on-chip and used an NoC to handle irregular accesses. But the NoC throughput was fundamentally limited—scaling the crossbar/wires doesn't scale performance. The authors recognized that with 86% sparsity, most memory requests are *invalid* (accessing pruned entries). Filtering these requests *before* they enter the NoC converts a bandwidth problem into a filtering problem.

The non-obvious part: filtering irregular bitmap accesses is itself an irregular memory access problem! Their solution—sequential SRAM scanning with parallel address matching—sidesteps the bank conflict problem entirely. They trade latency for throughput by grouping requests to the "nearest" sequentially-advancing scanner.

This is elegant because it decouples two hard problems: (1) achieving high sparsity in the algorithm (ST-NSR), and (2) efficiently exploiting that sparsity in hardware (SIU). Neither alone would work—sparse tables without SIU just moves the bottleneck; SIU without sparse tables has nothing to filter.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Real RTL Implementation with EDA Synthesis:** The authors implemented Cambricon-SR in Verilog and synthesized it with Synopsys Design Compiler at TSMC 45nm/750MHz (Section 5.1.1). They provide a detailed area/power breakdown (Table 2: 234.86 mm², 356.57W). This is more rigorous than pure simulation.

2. **Cycle-Accurate Simulation with Ramulator:** DRAM timing is modeled using Ramulator [20] integrated into their simulator (Section 5.1.1). This captures HBM2 timing behavior rather than assuming idealized bandwidth.

3. **Algorithm-Hardware Co-Evaluation:** They evaluate ST-NSR on GPU first (Section 3.3), showing 1.19× speedup—proving the algorithmic contribution independent of hardware. The GPU implementation uses A100's L2 cache residency and shared memory bitmaps, demonstrating they hit real GPU bottlenecks (bank conflicts in shared memory).

4. **Comprehensive Ablation Study:** Figure 18-19 isolates contributions: Sparse Update Unit (1.24×), Dynamic Shared Buffer (1.36×), SIU (3.07×). This decomposition builds confidence in each component.

5. **Quality-vs-Speed Pareto Curves:** Figure 1 directly compares Cambricon-R and Cambricon-SR on the performance-quality tradeoff, the actual metric that matters for applications.

### Weaknesses

1. **Technology Node Scaling is Questionable:** RTL is synthesized at 45nm, then "scaled" to 7nm using [44] (Stillmaker & Baas 2017). This scaling methodology is known to be imprecise—it doesn't capture 7nm-specific effects like wire-dominated routing, FinFET leakage, or advanced SRAM bitcell densities. The 1259× vs. A100 comparison conflates algorithmic/architectural gains with potentially optimistic area/power scaling.

2. **No Validation Against RTL:** The CAM design (33.56 mm², 14.29% of chip—Section 4.2) is a significant area component, but they only report area from synthesis, not functional validation. CAM timing at 750MHz in a 7nm-scaled design would be tight. No CAM access latency is reported.

3. **Simulator Fidelity for SIU is Unclear:** The SIU design involves complex request buffering and sequential scanning. The paper says they use a "cycle-accurate simulator" to determine buffer sizes (Section 4.4.2, Figure 11), but the simulator itself isn't described. Did they model wire delays? Router contention in the grouping crossbar?

4. **Limited Baseline Diversity:** Cambricon-R is simulated "according to its paper" (Section 5.1.3)—not with released artifacts. They don't compare against other published NSR accelerators like Instant-3D [24] or NeuGPU [40] (which taped out!). NeuGPU would be a particularly interesting comparison since it's real silicon.

5. **Dataset Selection Bias:** The 8 datasets include 3 synthetic (NeRF-Synthetic), 1 small real-world (Fox), and 4 large real-world (Mip-NeRF-360). The synthetic scenes show much higher PSNR (Table 1: 31-37 dB) than real scenes (20-29 dB). The sparsity rates in Figure 4 vary significantly across datasets (80-96%). It's unclear how the "fixed" sparsity threshold generalizes.

6. **Missing Training Convergence Analysis:** They mention using the threshold from the "last iteration" and only "half the DT" for threshold computation (Section 4.1). The claim that this has "negligible impact (<0.1 PSNR)" isn't backed by convergence curves—just a single number.

---

## Q4: What the Authors Didn't Tell You

1. **The CAM Power Problem:** 15 MB of CAM (Section 4.2) is enormous. They use blocking (16 blocks × 160 entries), but even blocked CAMs have significant search energy. The paper reports CAM area but *not* CAM power separately. The "Encoding Module (w/o SIU)" at 189W (Table 2) likely includes CAM—this would be a dominant power consumer and would dwarf the 53.81W SIU.

2. **SIU Wastes 70.7% of Bitmap Reads:** They admit this in Section 5.2.3—sequential scanning reads data that doesn't match any request. This contributes 52.8% of SIU's energy. They call this "worthwhile," but it means the SIU's effective energy efficiency is roughly 3× worse than the headline numbers suggest.

3. **The Dense Table Still Lives Off-Chip:** Despite all the sparse optimizations, the 128 MB dense table must be read from HBM2 during the update stage. Figure 17 shows Cambricon-SR has *more* off-chip accesses than Cambricon-R for some workloads. The Sparse Update Unit reduces this by 6.06× (Section 4.3), but the update stage still accounts for 32.85% of total time.

4. **Threshold Computation Uses Only Half the DT:** Section 4.1 describes using the first half of the DT for threshold computation, justified by hash function "randomization." But hash functions can have subtle biases, and the encoding table is not uniformly utilized (Section 3.2 shows higher sparsity at larger resolutions). This approximation could cause systematic threshold errors in certain scenes.

5. **No Open-Source Artifacts:** There's no GitHub link, no released simulator, no RTL. The paper references official Instant-NGP [37] for GPU baselines, but Cambricon-SR itself is "paperware." Reproducing the SIU design or CAM blocking strategy would require significant reverse-engineering.

6. **The 32 MLP Units Sharing One Buffer Creates Contention:** Section 4.5 describes atomic operations for gradient accumulation and flag registers for block allocation. The control unit managing 32 MLP units is a centralized resource. No contention analysis or stall cycles are reported.

7. **Sparsity is Scene-Dependent:** Figure 4 shows optimal sparsity rates ranging from 80% to 96% across datasets. The paper uses scene-specific sparsity rates, but doesn't explain how these would be determined for *new* scenes without ground-truth quality metrics. In deployment, you'd need online sparsity adaptation.