# Paper Deconstruction: H²-LLM

## Q1: Whiteboard Explanation

Alright, let me explain what's actually happening here without the marketing fog.

**The Problem They're Solving:**
Imagine you're running a personal chatbot on an edge device—maybe a smart home hub or a private server. Unlike cloud inference where you're batching requests from hundreds of users, you've got 1-16 users max. The LLM inference has two phases: *prefill* (crunch through the whole prompt in parallel—compute-heavy) and *decoding* (generate one token at a time—memory-heavy because you're constantly loading weights for tiny matrix operations).

The standard approach to the memory-bound decoding problem is Near-Memory Processing (NMP)—stick some compute units right next to the DRAM so you don't have to ship data across the memory bus. Samsung and SK Hynix have products that do this by embedding tiny processing engines *inside* the DRAM die itself ("in-die NMP").

**Why In-Die NMP Falls Short:**
The problem is DRAM transistors are slow (3× slower than logic transistors) and you can't fit much compute in there. Table 2 (page 5) shows existing products max out at ~1 FLOP/Byte compute-to-bandwidth ratio. As shown in Figure 3, when batch size hits 8+ or when you use GQA/MQA (which reduces KV heads and thus memory traffic), the in-die NMP can't keep up—it becomes compute-bound while the main processor is still memory-bound. Neither is fully utilized. That's a waste.

**The H²-LLM Solution:**
Instead of embedding compute in the DRAM die, they use **hybrid bonding** technology—you stack a full logic die *underneath* the DRAM die and connect them with dense copper pillars (110,000 connections per mm²). This gives you:
1. High bandwidth between memory and compute (because the connection is so dense)
2. Customizable compute on the logic die (since it's real CMOS, not DRAM-limited logic)

But here's the catch that Figure 4-(b) shows: the controllers for all those hybrid-bonding I/O pins eat up to 40% of your logic die area. More bandwidth = bigger controllers = less room for compute. That's the **computation-bandwidth trade-off** they spend the whole paper exploring.

**The Dataflow Innovation:**
The second contribution is their "data-centric" dataflow abstraction. Previous work either fixed which operators go to NMP (e.g., "all attention to NMP, all FFN to main processor") or explored mappings but ignored the prefill stage. H²-LLM:
1. Partitions operators into "Memory Access Groups" (MAGs) that can run in parallel
2. Binds channels to operators (not compute engines to operators—subtle but important)
3. Allows "operator fission"—splitting one operator across both the main processor AND NMP channels

The key insight for dataflow (Section 5.1) is that by binding to *channels* first, you can give the centralized processor access to NMP channels during prefill (when NMP isn't computing) to maximize bandwidth for the compute-heavy prefill phase.

---

## Q2: The Key Insight

**The Delta:** This paper is *not* about KV-cache compression, eviction policies, or PagedAttention-style memory management. It's a **hardware architecture paper** about a different memory hierarchy entirely.

The real insight has two parts:

**Insight 1 (Architecture):** The fundamental tension in hybrid-bonding-based NMP is that you can't have both maximum bandwidth AND maximum compute on the logic die—the memory controllers steal area from the compute units. Figure 18 is the money figure: under batch size 1, you want max bandwidth (51.2 GB/s wins), but at batch size 16, moderate configurations (25.6 GB/s, 8 FPUs) dominate because you need the compute. The paper's contribution is formalizing this design space and showing how to navigate it via DSE (Design Space Exploration).

**Insight 2 (Dataflow):** Previous "compute-centric" dataflow abstractions (like SpecPIM [47]) assign operators to *compute engines* first, which inadvertently restricts bandwidth available to the centralized processor. By instead doing "data-centric" binding—assigning operators to *memory channels* first—you decouple the questions of "where does data live" from "who computes on it." This is crucial because during prefill, you want the centralized processor to use ALL channels (including NMP channels in "normal mode") to maximize memory bandwidth for its compute-bound work. Figure 14 shows this matters: prefill takes 12-90% of end-to-end latency, and their approach gets 1.27× prefill speedup over compute-centric mapping.

**The Mechanism:** The HB-NMP channel has two modes (Section 4.1): 
- *Normal mode*: Centralized processor accesses DRAM banks through external interface (like regular memory)
- *NMP mode*: Local PEs access DRAM banks through hybrid-bonding I/O

Only one mode active at a time—no row buffer conflicts. The data-centric abstraction exploits this by letting prefill use all channels in normal mode, then switching to NMP mode for decoding.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Baselines and Fair Comparisons:**
They compare against three baselines (Section 7.1, page 11): centralized processor only (CP), Samsung's LPDDR5-PIM (ID-NMP), and an enhanced in-die NMP with AiM-style compute (ID-NMP+). Importantly, they give CP 2× the compute capacity to account for NMP's extra resources—this is methodologically sound. They also apply their own data-centric dataflow to ALL baselines, so the comparison isolates the hardware contribution.

**2. Realistic Workload Diversity:**
Table 1 (page 4) shows they use four datasets spanning different use cases: HumanEval (code completion, short prompts), ShareGPT (chatbot, medium), LongBench (context understanding, long prompts), LooGLE (QA, very long prompts with short outputs). This is better than cherry-picking one synthetic workload. They also vary batch size (1/4/16) and test three models with different attention variants (MHA/GQA/MQA).

**3. Detailed DSE Analysis with Takeaways:**
Section 7.4 doesn't just report "we explored the space"—they extract actionable design guidelines. The six takeaways (pages 13-14) are genuinely useful: e.g., "With the increase of batch size, decoding performance becomes more sensitive to input/output buffer size, while its sensitivity to the weight buffer size diminishes." This is the kind of engineering wisdom that transfers to other systems.

**4. Ablation on Dataflow Designs:**
Figure 12 (page 12) compares their data-centric dataflow against four existing strategies (Attn-NMP, Attn-NMP-Split, FC-NMP, CC-NMP). This isolates the dataflow contribution from the hardware contribution. The 1.11× speedup over CC-NMP (compute-centric) validates the prefill-awareness claim.

### Weaknesses

**1. No GPU Baseline:**
The elephant in the room: they compare against hypothetical in-die NMP systems, not against actual edge GPUs like NVIDIA Jetson Orin. They cite Jetson specs (page 11, reference [56]) but never benchmark against it. For an edge-focused paper, this is a glaring omission. A reader might reasonably ask: "Why would I build this custom accelerator instead of just using a Jetson?"

**2. Simulation-Only Evaluation:**
The evaluation (Section 7.1) uses Ramulator2 for NMP simulation and Tileflow's analytical model for centralized processor performance. There's no silicon, no FPGA prototype, no measured power numbers. The energy efficiency claims (1.48× over ID-NMP+, Figure 10) are entirely based on synthesized/modeled values. The MAC energy numbers come from synthesis (40nm for HB-NMP, 10nm for centralized processor)—mixing technology nodes is defensible but introduces uncertainty.

**3. Synchronization and Transfer Overhead is Minimized:**
Figure 16 shows synchronization + data transfer overhead is 1.6%-15.7% of decoding time. They claim this is acceptable, but:
- This is *decoding only*—what about prefill-to-decode transitions?
- The overhead grows with batch size and context length (visible in the figure)
- For longer contexts than tested (they cap at 2048 tokens per Table 1), this could become significant

**4. No End-to-End Latency Distribution:**
They report geomean speedup but never show P99 latency or latency distributions. For edge applications like chatbots, tail latency matters. One request getting stuck during synchronization could ruin user experience.

**5. Limited Model Scale:**
All tested models are 6.7B-8B parameters (Table 4). Modern edge deployment often considers smaller models (1-3B) or larger models with offloading. The paper doesn't explore how their approach scales down or up.

**6. Workload Assumptions:**
The evaluation assumes requests arrive at known batch sizes. Real edge workloads have variable arrival patterns. How does their scheduling work with dynamic batching? This is unaddressed.

---

## Q4: What the Authors Didn't Tell You

**1. The Real Cost of Hybrid Bonding:**
Section 3.2 casually mentions they get HB-related area numbers from "our real-chip tape-out [55]" (page 11), but hybrid bonding is bleeding-edge manufacturing. The paper never discusses yield rates, manufacturing cost, or thermal constraints of stacking logic under DRAM. This matters enormously for "edge-side" deployment where cost is critical. AMD's 3D V-Cache (cited as [79]) is expensive and only in high-end desktop CPUs—not exactly edge territory.

**2. Memory Capacity Assumptions:**
Their system has 8 channels × 16 banks × 256MB = 32GB total memory (Section 7.1). But an 8B parameter model in FP16 is ~16GB just for weights. With KV cache for batch size 16 at 2048 tokens, you're pushing capacity limits. They never discuss what happens when you exceed this—do they support weight offloading? How does their NMP mode interact with paging?

**3. The Prefill "Speedup" Needs Context:**
They claim 1.27× prefill speedup (Figure 14, page 12), but this is over CC-NMP running on *their architecture*, not over a standard processor. The prefill phase runs entirely on the centralized processor in both cases—the speedup comes from better bandwidth utilization when NMP channels operate in "normal mode." This is a scheduling improvement, not a hardware capability improvement.

**4. The Dataflow Search is Expensive:**
Section 7.2 mentions "we iterate the genetic algorithm for 100 rounds and sample 5k individuals per iteration" (page 11). That's 500,000 candidate evaluations. For full architecture DSE (Section 7.3), they increase to 50k individuals per iteration. The artifact appendix says experiments take "about 9 hours" on 50+ cores. This compilation cost is non-trivial for deployment scenarios where workloads change.

**5. Element-wise Operations Are Punted:**
Section 4.3 (page 7) explicitly states: "similar to previous work [47], element-wise operators can be efficiently fused with NMP operator's result merging stage" and "we leave non-GEMM operators to the centralized processor." This means LayerNorm, softmax, activations, and residual additions all run on the centralized processor and aren't accelerated by NMP. For some transformer variants with expensive element-wise operations, this could be a hidden bottleneck.

**6. The Attention Arithmetic Intensity Claim:**
They argue (Section 3.1) that GQA/MQA increases attention arithmetic intensity, making in-die NMP insufficient. But GQA/MQA also *reduce* the KV cache memory footprint—that's their whole purpose. The interaction between reduced memory traffic and reduced in-die NMP advantage is more nuanced than presented.

**7. No Discussion of Quantization Compatibility:**
Modern edge LLM deployment heavily uses quantization (INT4/INT8). Their FPUs are FP16 (Section 4.1, Figure 5-c). How would INT8 execution change the compute-bandwidth balance? Would you need different DSE results? This is completely unaddressed.

**8. Normal vs. NMP Channel Co-existence is Under-explained:**
Table 3 mentions "normal and NMP channels may co-exist due to system integration or resource utilization issues" [32, 39, 47], but their main experiments seem to use architectures where all channels can be NMP channels. The exploration of "2/4/6/8 NMP channels" (Table 5) suggests some channels are normal-only, but the implications for their data-centric binding when you have this heterogeneity aren't fully explored.