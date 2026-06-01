# AiF: Accelerating On-Device LLM Inference Using In-Flash Processing

## Q1: Whiteboard Explanation

Let me walk you through what's actually happening inside this system.

**The Problem Setup:**
On-device LLMs are memory-bound nightmares. You have a 40B parameter model needing ~40GB of weights, but your laptop has 8-16GB of DRAM. The obvious solution—offload to SSD—fails because SSDs deliver 4-8 GB/s while DRAM gives you 80-100 GB/s. From Equation 1 (Section 3.1), your token generation rate is capped at `read_bandwidth / model_size`. With a 40GB model on an 8 GB/s SSD, you get 0.2 tokens/second. Unusable.

**The Architectural Insight:**
A 1-TB SSD has 16 flash chips, each capable of reading at 1.6 GB/s internally. That's 25.6 GB/s *aggregate internal bandwidth*—but you can't use it because everything must squeeze through constrained flash channels (multi-drop bus topology, one chip talking at a time per channel) to reach the SSD controller.

**AiF's Solution: Move the Compute INTO the Flash Chip**
Instead of moving 40GB of weights out to compute, AiF puts INT8 multipliers and an adder tree *inside each flash chip* (the "Product Elements" or PEs in Figure 6). You send only the input vector (~8-32 KiB) down to the chips, they compute GEMV locally using their internal bandwidth, and return only the tiny output vector (~16 KiB). Data reduction ratio is enormous.

**The Two Hardware Tricks:**

1. **Charge-Recycling Read (cr-read):** Normal flash reads have three phases: precharge (0V→6V on all wordlines), evaluation (sense the cell), discharge (6V→0V). When reading consecutive wordlines in a block (which you always do for LLM weights), cr-read skips the full precharge/discharge. Instead, it only swaps voltages between the previous WL (VREF→VPASS) and next WL (VPASS→VREF) while keeping everything else at VPASS. This is shown in Figure 8—the "recycling" phase replaces both precharge and discharge. Result: tR drops from ~28µs to ~9.7µs (Figure 10a), giving 2.8× bandwidth improvement.

2. **Bias-Error Encoding (be-enc):** TLC flash stores 3 bits per cell across LSB/CSB/MSB pages. Conventional (2,3,2) Gray coding requires 2, 3, and 2 sensing operations respectively. AiF uses (1,3,3) coding (Figure 12b) where LSB pages need only 1 sensing operation—essentially SLC-speed reads. The trick: this encoding places the LSB decision boundary at V⁴REF, which sits in the most stable voltage region (P3/P4 states). Per Figure 13b, LSB page errors drop by 80% compared to conventional coding.

**Why This Enables a Lightweight ECC:**
With be-enc, LSB pages have only ~9 bit errors per 1KiB instead of ~49 (Figure 13). This means ECCLITE—a simple BCH decoder correcting 10 bits per KiB—is sufficient. Per Table 1, this takes only 0.167mm² and 45.1mW, versus the full-blown LDPC decoder that would need 40mm² and 10W to achieve 102.4 GB/s throughput (Figure 5).

**The Capacity Trade-off:**
Storing LLM weights only on LSB pages means you use 1/3 of your flash capacity for model storage. The CSB/MSB pages in "IFP blocks" still store general data, just with slightly degraded performance (MSB now needs 3 sensing operations instead of 2).

---

## Q2: The Key Insight

**The Core Mechanism:** AiF exploits the observation that *successive wordline reads within a flash block share almost all their voltage setup*. The precharge and discharge phases—which consume 64% of read latency—exist only to return the block to a known initial state for arbitrary subsequent operations. But during LLM inference, you're reading sequentially through a block containing one weight matrix. AiF eliminates this unnecessary state-machine reset by recycling the charged voltages between consecutive reads.

**What Makes This Non-Obvious:**
The conventional flash interface assumes nothing about the next operation. This is correct for general workloads but wasteful for LLM inference where access patterns are deterministic. The authors recognized that LLM weights are "write once, read many" and can be *statically pre-arranged* within blocks at model loading time (via `aif_post` command, Section 5.2).

**The Enabling Reliability Trick:**
The second insight is that (1,3,3) Gray coding is implementable with *zero hardware changes*—existing flash chips already support dynamic VTH encoding reconfiguration (Section 4.3.2, citing [58, 63]). By biasing reliability toward LSB pages, they create an asymmetric system: LLM data gets SLC-like reliability and speed, while general I/O data gets slightly worse MSB performance (hidden by the external bandwidth bottleneck anyway—Figure 11a shows internal bandwidth doesn't matter until tR exceeds ~60µs).

**The Hardware Tax They Actually Pay:**
- 0.209 mm² per chip for PEs + ECCLITE (Table 1)
- 51.68 mW average power during inference
- Loss of 2/3 capacity for LLM storage (though CSB/MSB remain usable for general data)

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**S1: Real Device Validation for Flash Characteristics**
The cr-read technique is validated on a fabricated 9×9 CTF cell array (Section 4.2.2, footnote 5). Figure 10(b) shows BL current measurements match between conventional and cr-read, confirming no data distortion. They also characterized 160 real TLC chips across 3.68 million wordlines (footnote 6) following JEDEC standards. This isn't just simulation.

**S2: Honest About Sublinear Scalability**
Figure 17(b) shows that doubling capacity only yields 1.35-1.68× speedup, not 2×. The authors explicitly explain this in Section 6.2: NVMe protocol overhead (interrupts, DMA) and vector loading time through flash channels create bottlenecks. This is refreshingly honest compared to papers that only show best-case numbers.

**S3: Full System Integration**
They modified llama.cpp and integrated with NVMeVirt emulator (Section 6.1). The evaluation includes real LLM workloads (8 models from 7B to 70B parameters) rather than synthetic benchmarks. The comparison against actual in-memory inference (Table 3) with DDR5 bandwidth is meaningful.

**S4: Energy Analysis Includes All Components**
Figure 17(a) models flash reads, ECC, GEMV, flash channels, AND PCIe energy—not just the part they optimized. They cite specific pJ/bit numbers: 5.8 pJ/bit for flash channels [66], 7.5 pJ/bit for PCIe [50], 7 pJ/bit for LPDDR5 [29].

### Weaknesses

**W1: AiF-- Baseline Assumes Impossible ECC**
Section 6.1 states: "Although ECCLITE cannot perform error correction without be-enc, we assume error correction is feasible to focus on evaluating the performance of AiF−−." This means the AiF-- numbers in Figure 16 are *aspirational*—you cannot actually build AiF-- because the on-chip ECC would consume prohibitive area/power (Figure 5 shows 40mm² and 10.7W). The 2.67× advantage of AiF over AiF-- is therefore comparing a buildable system against an unbuildable one.

**W2: SPICE Simulation vs. Real Silicon for Timing**
While cell behavior was validated on real silicon, the actual tR numbers come from SPICE simulation using BSIM-CMG models (Section 4.2.2, footnote 4). The 2.9% error in tR and 0.9% error in power are claimed acceptable, but full-chip effects (voltage droop across large WL capacitances, thermal variation) aren't captured. The 9.7µs cr-read latency is simulation-derived.

**W3: Limited Sensitivity to P/E Cycling**
Figure 11(b) shows characterization at 4K P/E cycles, but consumer SSDs rated for 300-600 TBW on 1TB can see cells approaching this limit. The paper doesn't show how cr-read behavior degrades with cell wear—if VTH distributions widen, the "recycling" voltage assumptions may require larger margins.

**W4: GC Overhead Hand-Waved**
Footnote 8 (page 537) mentions: "AiFSSD performs page copies while preserving the original LSB page order within the IFP block whenever GC is triggered." This means garbage collection must physically copy pages in a specific order—adding write amplification and latency. No quantification of GC overhead during inference is provided.

**W5: No Mixed Workload Evaluation**
Figure 18 evaluates random read overhead in isolation. What happens during inference when background I/O hits IFP blocks? The evaluation assumes LLM inference dominates—realistic on-device scenarios may have concurrent filesystem activity.

---

## Q4: What the Authors Didn't Tell You

**1. The Real Area Comparison is Misleading**
Table 1 claims 0.209mm² per chip is "approximately 0.2% of the total flash chip area." But modern flash chips are ~100-150mm² for the cell array alone; the peripheral circuits (row decoders, charge pumps, sense amps) add significant area. The 0.2% figure likely compares against the full die, not the peripheral logic where ECCLITE and PEs must actually fit. In peripheral-under-cell-array (PUC) architectures [69], adding 0.2mm² per chip × 16 chips is non-trivial.

**2. The 102.4 GB/s Number Requires Perfect Conditions**
This "internal bandwidth" assumes: (a) all 16 chips active simultaneously, (b) consecutive cr-reads within blocks with no interruptions, (c) no flash channel contention for input vector distribution. The input vector must be broadcast to all 16 chips—through 8 channels at 2 GB/s each (Table 2). For a 32 KiB input vector, this takes ~2µs per chip pair, serialized across channels.

**3. INT8 Precision Only—No Path to Lower Precision**
All evaluation uses INT8 (Section 6.1). Modern on-device LLMs increasingly use INT4 or even INT2 quantization [9, 51, 53]. The PE design (INT8 multipliers + adder tree) would need redesign for variable precision. The authors claim "AiFSSD can be integrated with any LLMs" (Section 4.1), but this is true only for INT8 models.

**4. KV Cache Must Still Live in DRAM**
Figure 15 shows the MHA stage runs on the host because "it requires access to the KV cache, which must be stored in memory due to its frequent updates" (Section 5.1). For long contexts, KV cache can exceed model weights (Table 3 shows up to 2 GiB for Mixtral). AiF doesn't solve the memory problem for long-context inference.

**5. The "1.4× Better Than In-Memory" Claim Has Caveats**
This result (Section 6.2) compares against a system with 128GB DDR5 at 86.4 GB/s. But the AiF system runs on 8GB memory with most weights on SSD. The comparison is valid for throughput but ignores that In-Memory can handle longer contexts (larger KV cache) while AiF cannot. Also, In-Memory doesn't require the host CPU to orchestrate concurrent execution between host and AiFSSD (Figure 15c)—the "parallel scheduling scheme" adds software complexity not reflected in throughput numbers.

**6. The CSB/MSB Pages Have Degraded Reliability Too**
Figure 13(b) shows CSB errors increase by 43% and MSB errors by 38% with (1,3,3) coding compared to (2,3,2). While the paper claims "large ECC margin" (Figure 11b), this margin is consumed faster on IFP blocks. Long-term reliability for general data stored on CSB/MSB pages of IFP blocks may degrade faster than conventional blocks.

**7. No Discussion of Multi-Model Scenarios**
Edge devices often run multiple models (e.g., LLaMA for text, Whisper for voice). Each model needs its own IFP blocks with static weight placement. Model switching incurs `aif_post` overhead (writing to LSB pages in IFP blocks). The paper evaluates single-model scenarios only.