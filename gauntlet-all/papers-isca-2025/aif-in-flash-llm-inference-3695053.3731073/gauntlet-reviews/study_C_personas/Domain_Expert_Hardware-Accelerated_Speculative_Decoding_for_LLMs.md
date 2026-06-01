# Paper Deconstruction: AiF: Accelerating On-Device LLM Inference Using In-Flash Processing

## Q1: Whiteboard Explanation

Let me sketch this out simply.

**The Problem:** You want to run a 40B parameter LLM on your laptop, but you only have 8GB of RAM. The model needs ~40GB. The obvious solution is to store the model on your SSD and load pieces as needed. But here's the death spiral: SSDs give you maybe 8 GB/s bandwidth. LLM inference during the decode phase reads the *entire model* for *every single token*. At 8 GB/s reading 40GB, you get... 0.2 tokens per second. Unusable.

**The Core Insight:** Inside your SSD are 16 flash chips, each reading at ~1.6 GB/s. If they could all read in parallel, that's 25.6 GB/s internal bandwidth—way more than the 8 GB/s you see externally. The problem? All that data has to squeeze through narrow "flash channels" to reach the SSD controller before it goes to your CPU. Classic bottleneck.

**AiF's Solution:** Don't move the data out. Put tiny compute units (just INT8 multipliers and adders) *inside each flash chip*. The model weights live on flash. When you need a matrix-vector multiply (GEMV), you send just the small input vector (8-32 KB) into the chips. Each chip multiplies its portion of the matrix with the vector and sends back only a tiny result vector. The "data reduction" is massive—you read a 175MB matrix internally but only send out 16KB.

**The Two Dragons to Slay:**

1. **Speed:** Even 25.6 GB/s internal bandwidth isn't enough for 3+ tokens/s on 30B+ models. You need ~100 GB/s.

2. **Errors:** Flash memory has high bit error rates (~10⁻³). Normal ECC decoders are massive and power-hungry. But LLMs are *extremely* error-sensitive—even 10⁻⁷ RBER causes 60%+ accuracy drops (Figure 4b).

**Their Two Techniques:**

1. **Charge-Recycling Read (cr-read):** Normal flash reads have precharge→evaluate→discharge phases for *every* wordline. Since LLM weights are stored sequentially in the same block, most wordlines share voltage settings. Skip the precharge/discharge between consecutive reads by "recycling" the voltages. This cuts read latency from ~28μs to ~9.7μs—a 2.8x bandwidth boost per chip.

2. **Bias-Error Encoding (be-enc):** TLC flash stores 3 bits per cell across LSB/CSB/MSB pages. They redesign the voltage state encoding so LSB pages need only 1 sensing operation (like SLC) and have 80% fewer errors than standard encoding. Store LLM parameters *only* on LSB pages. Now your lightweight on-chip ECC (correcting just 10 bits per 1KB instead of 50+) is sufficient.

**Combined result:** 6.4 GB/s per chip × 16 chips = 102.4 GB/s internal bandwidth for LLM inference. That exceeds DRAM bandwidth.

---

## Q2: The Key Insight

**The Real Contribution:** This is *not* fundamentally about "in-flash processing"—that's been done before for vector search, DNNs, and other applications (references [24, 38, 45, 77, 78]). The delta is the **two flash-level read optimizations specifically tailored to LLM inference characteristics.**

**The Magic Trick has two parts:**

**1. Cr-read exploits spatial locality in model weights.** The authors recognized that LLM parameters, unlike general storage workloads, exhibit perfect sequential locality—you read entire weight matrices from consecutive wordlines within a block. In conventional flash, each read cycles through precharge (0V→6V on all WLs), evaluate, and discharge (6V→0V)—paying full RC delay costs every time. Cr-read observes that between consecutive wordline reads, only *two* wordlines change: the previous target (V_REF→V_PASS) and the next target (V_PASS→V_REF). Everyone else stays at V_PASS. By eliminating the full discharge/precharge cycle, they cut tR from ~28μs to 9.7μs (Section 4.2.2, Figure 10a).

**2. Be-enc trades off general I/O performance for IFP reliability.** This is the clever systems insight. They recognized two things from their flash characterization (Figure 11): (a) increasing tR on non-LLM pages barely impacts external bandwidth because the PCIe interface is already the bottleneck, and (b) modern SSDs have massive ECC headroom because they're over-provisioned for worst-case wear scenarios. So they reconfigure TLC state encoding from (2,3,2) to (1,3,3)—LSB pages now need only 1 sensing operation (like SLC) with 80% fewer errors (Figure 13b), while MSB pages get worse. Since LLM weights are stored only on LSB pages, the in-flash ECC decoder (ECC_LITE) only needs to handle ~10 errors per 1KB instead of ~50, slashing area by 15× and power by 15× (Table 1 vs. Figure 5).

**Why this matters:** Prior IFP work ignored these flash-level details because their target workloads were either error-tolerant (approximate computing) or didn't demand 100+ GB/s bandwidth. The authors correctly identified that LLM inference sits at a nasty intersection: it's both error-intolerant *and* bandwidth-starved.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Rigorous flash-level validation.** This isn't just simulation handwaving. They characterized 160 real TLC flash chips (3.6M+ wordlines, Section 4.3 footnote 6), validated cr-read on fabricated CTF cell arrays (Section 4.2.2, Figure 10b), and used industry-standard EDA tools (Cadence Virtuoso/Spectre, Synopsys Design Compiler) for circuit modeling. The 2.9% and 0.9% differences vs. measured tR and power (Section 4.2.2) are credible calibration.

**2. Full-system evaluation methodology.** They integrated NVMeVirt (a respected SSD emulator) with llama.cpp, a real inference engine, not a toy benchmark (Section 6.1). The timing model captures actual GEMV delays including NVMe protocol overhead.

**3. Comprehensive model coverage.** Eight models from 7B to 70B, including dense transformers and MoE (Mixtral-8x7B). Table 3 is refreshingly transparent about exact model configurations.

**4. Honest scalability analysis.** Figure 17(b) shows sub-linear scaling (1.35-1.68× for 2× capacity increase) and they correctly attribute this to vector arithmetic overhead and NVMe control overhead. Many papers would have hidden this.

**5. Realistic overhead analysis.** Figure 18 shows be-enc causes 6.8% IOPS drop and 9.3% latency increase for random reads on IFP blocks. They don't hide this trade-off.

### Weaknesses

**1. Baseline Selection Issues.** The "In-Memory" baseline uses an Intel Core i9-14900KS + 128GB DDR5 at 86.4 GB/s (Section 6.1). This is a *CPU-only* baseline—no GPU, no NPU during inference. They explicitly state they "exclude dedicated accelerators" with a thin justification about arithmetic intensity. But in reality, many laptops have integrated GPUs or Apple Silicon with unified memory achieving much higher effective bandwidth for LLM inference. The comparison to "in-memory" at 86.4 GB/s makes AiF's 102.4 GB/s look good, but this is a convenient framing.

**2. Energy Model Assumptions.** Figure 17(a)'s energy comparison assumes an NPU at 1.4 TOPS/W for the in-memory baseline's computation (citing reference [47]). But they're comparing against their own flash-based system where ECC_LITE and PEs consume only 51.68mW (Table 1). The NPU baseline seems designed to make flash look favorable. A fairer comparison would include DRAM static power implications at different capacity points.

**3. Missing Prefill Phase Analysis.** Section 5.1 admits the prefill phase runs entirely on the host CPU, not AiFSSD. For long-context applications, prefill latency can dominate. They never quantify the prefill overhead or how their "phase splitting" affects overall request latency.

**4. Memory Capacity Fixed at 8GB.** All SSD-based experiments use 8GB host memory (Section 6.1). But modern laptops range from 8-64GB. The crossover point where AiF stops being beneficial isn't characterized. If you have 32GB RAM and a 13B model, is AiFSSD still useful?

**5. INT8-Only Evaluation.** All models are INT8-quantized (Section 6.1). Modern on-device inference increasingly uses INT4 (AWQ, GPTQ cited in Section 7). At INT4, models are 2× smaller, so the bandwidth pressure is halved. Would AiF still outperform well-optimized INT4 in-memory inference?

**6. No Comparison to Dedicated Accelerators.** The related work mentions ISP solutions extensively but never benchmarks against any. They could have compared to existing computational SSD solutions, even in estimated form.

---

## Q4: What the Authors Didn't Tell You

**1. The LSB-Only Storage Halves Your Effective Capacity.**
Be-enc stores LLM parameters only on LSB pages of TLC flash. In TLC, each cell has LSB/CSB/MSB pages. If you're dedicating LSB pages to model weights, you're using only 1/3 of those cells' capacity for IFP data. A "1TB SSD" might effectively provide ~330GB for model storage before capacity pressure forces you to use CSB/MSB for general data. For a 70B model at INT8 (~70GB), this is fine. But scale to multiple models or larger ones, and the capacity math gets tight. Section 4.3.2 mentions CSB/MSB pages "accommodate incoming general data afterward" but doesn't quantify the capacity trade-off.

**2. Garbage Collection is a Lurking Problem.**
Footnote 8 (Section 5.2) casually mentions: "AiFSSD performs page copies while preserving the original LSB page order within the IFP block whenever GC is triggered." This is non-trivial. GC in SSDs is already a performance killer; now you have special constraints preserving sequential LSB ordering. The paper never evaluates performance under sustained mixed IFP/non-IFP workloads where GC would actually occur. All experiments seem to be "model loaded once, inference repeatedly"—not realistic long-term operation.

**3. The 102.4 GB/s is Best-Case Theoretical.**
They achieve 102.4 GB/s only when cr-read applies to *every* read—meaning perfectly sequential access within blocks. Section 4.2.1 notes cr-read "can only be applied when successive reads to the same block are requested without any interval." Any interruption (e.g., servicing a random read) forces a full precharge/discharge cycle. The 102.4 GB/s assumes 100% cr-read eligibility. Real workloads mixing LLM inference with background SSD activity would achieve less.

**4. No Latency Numbers for Individual Tokens.**
They report tokens/s throughput but never report per-token latency or time-to-first-token. For interactive applications (chatbots), latency matters more than throughput. If there's variance in GEMV completion times due to flash timing jitter, the user experience could suffer even at high average throughput.

**5. The Control Path Overhead Isn't Fully Characterized.**
Section 6.2 mentions "control overhead grows, mainly due to the longer time required to load input vectors into flash chips via flash channels." Loading a 32KB input vector into 16 chips over 8 channels at 2GB/s channel bandwidth takes only ~2μs per chip—seemingly negligible. But they never break down where the actual overhead comes from. Is it NVMe command processing? Interrupt latency? DMA setup? Without this breakdown, it's hard to know if their claimed scalability limitations are fundamental or implementation-specific.

**6. ECC_LITE Failure Modes Under Wear.**
Figure 13(b) shows LSB pages with (1,3,3) encoding have max ~9 errors per 1KB at 4K P/E cycles + 1-year retention. ECC_LITE corrects up to 10 bits per 1KB (Section 4.4). The margin is razor-thin. What happens at 5K P/E cycles, or 2-year retention, or higher temperatures? The paper claims they tested at "worst-case operating conditions prescribed by manufacturers" but doesn't show the safety margin degradation curve. For a consumer SSD expected to last 5+ years, this seems risky.

**7. They Don't Discuss Model Updates.**
When you fine-tune or update an LLM, you need to reprogram flash. Flash has limited P/E cycles (typically 3K-5K for consumer TLC). If you're treating the SSD as persistent model storage with frequent updates, you're burning endurance. They never discuss this operational consideration.