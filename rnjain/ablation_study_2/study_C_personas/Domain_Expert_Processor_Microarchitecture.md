# Decoding Avant-Garde: A Deep Dive

## Q1: Whiteboard Explanation

Let me sketch this out for you like we're at a conference hallway whiteboard.

**The Problem Setup:**
Modern DNNs are hungry beasts—GPT-3 needs 3×10²³ operations (Section 1). Moore's Law is wheezing. The answer? Cram more operations into smaller data representations. Enter "scaled numeric formats" like FP8 and Microsoft's Microscaling (MX).

Think of scaled numeric formats like scientific notation for groups of numbers. Instead of storing each number with its own exponent, you share an exponent (scaling factor) across a "block" of numbers. MX9, for example, groups 16 elements under one 8-bit scaling factor, then further divides those into subsets of 2 elements with a 1-bit "micro-scale" factor (Figure 1b). This is a *two-level* hierarchy.

**The Current Pain:**
Here's where current NVIDIA GPUs choke. The H100's Tensor Cores can do matrix-multiply-accumulate (MMA) beautifully on FP8/INT8, but they have *no idea* what to do with these per-block scaling factors. So what happens? Look at Figure 3—it's a crime scene.

The GPU has to:
1. Load the elements with `wmma.load.a` and `wmma.load.b`
2. Do the MMA on Tensor Cores
3. **Then** separately load scaling factors (`ld.global R16, R17, R18, R19`)
4. Multiply scaling factors together (`mul R20, R16, R18`)
5. Apply them to each partial result using CUDA Cores (`mad R8, R0, R20, R8`)

This software scaffolding for scaling factors increases instructions by 2.14× and register usage by 1.38× compared to plain INT8 (Figure 4). The Tensor Cores sit idle while CUDA Cores play janitor.

**The Avant-Garde Solution:**
The core trick is "flattening." Before computation, convert any multi-level scaled format into a *single-level* internal representation:

```
Multi-Level (MX9):    [Block Scale] → [Subset Scale] → [Elements]
                            ↓ (multiply scales into elements)
Flattened:            [Block Scale] → [Modified Elements]
```

Figure 5 shows this visually. The "Operand Transformer" (Figure 7) is a small hardware unit with 16 FP8/INT8 multipliers that pre-bakes the inner scaling factors into the elements. Now you have 32 elements + 1 scaling factor per warp—nice and uniform.

The redesigned Tensor Core (Figure 8) then handles this elegantly:
1. Compute dot products on the flattened elements (business as usual)
2. **In hardware**, add the scaling factors (they're exponents, so addition = multiplication)
3. Apply the combined scaling factor to the dot product result via a "Scaling Unit"
4. Accumulate

No CUDA Core intervention. No extra instructions. The scaling factor bookkeeping is baked into the Tensor Core pipeline itself.

**The Pipeline Change:**
Figure 6 shows the modified pipeline. Between "Read Operands" and "Execute," they insert an "Operand Transform" stage. This is conditionally used only for scaled formats—vanilla operations skip it.

---

## Q2: The Key Insight

**The Real Innovation:** The paper's genuine contribution is recognizing that *all* scaled numeric formats—regardless of their block sizes or scaling hierarchies—can be normalized to a single internal representation before computation.

This is not a trivial observation. The diversity of formats (HBFP with block size 64, MX9 with two-level scaling and block size 16, MXFP8 with block size 32) would normally suggest you need different hardware paths for each. Instead, the authors observe that:

1. **Multi-level formats can be flattened** by absorbing inner scaling factors into elements (a one-time preprocessing cost)
2. **Different block sizes can be handled** by either coalescing small blocks or splitting large blocks to match the warp size (32 threads)
3. **The flattened format is uniform enough** that a single Tensor Core design with a scaling factor adder + scaling unit suffices

The magic isn't the hardware components—an 8-bit adder and a multiplier are trivial. The magic is the *architectural insight* that format diversity doesn't require hardware diversity if you normalize at the operand preparation stage.

**What Makes This Non-Obvious:**
A naive approach would be to build configurable Tensor Cores that understand each format natively. That's a hardware nightmare—different datapaths, more control logic, longer verification cycles. Avant-Garde sidesteps this by pushing complexity to a simpler preprocessing stage (Operand Transformer) that only needs multipliers, not the complex systolic array logic of Tensor Cores.

**The Secondary Insight:**
The flattened representation can persist throughout execution (Section 3.1: "operands can remain in this representation for the duration of a workload's execution"). Weights are flattened once before inference. Activations stay flattened between layers. This amortizes the flattening cost dramatically—you don't pay it per-operation, you pay it once at data ingestion boundaries.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Solid Baseline Choice:**
They use NVIDIA H100 (Table 1), which is the current state-of-the-art for DNN acceleration and does support FP8 natively. This isn't comparing against some strawman from 2018. The baseline implements scaled formats via the documented software approach (Section 4: "we implement a DNN model that handles the scaling factor in software").

**2. Real Workloads:**
Table 3 shows ViT-Base (86M), ViT-Large (307M), BERT (110M), and GPT-2 (124M)—all legitimate production models. They're not hiding behind only synthetic microbenchmarks, though they include one for controlled analysis.

**3. Multiple Scaled Formats:**
They test HBFP (single-level), MX9 (two-level), and MXFP8 (single-level with FP8 elements)—covering the breadth of emerging standards. This validates their claim of format-agnostic design.

**4. Accuracy Validation:**
Table 4 shows flattened MX9 maintains accuracy within 0.2% of both original MX9 and FP32. This is crucial—if flattening destroyed precision, the whole approach would be academic. They used Microsoft's MX emulator for functional validation (Section 5.5), which adds credibility.

**5. Silicon Overhead is Disclosed:**
Section 3.3 reports 1.4% area and 1.2% power overhead. They synthesized using FreePDK 45nm. This is refreshingly honest—many papers hide area costs.

### Weaknesses

**1. Simulation-Only, No Silicon:**
All results are from Accel-Sim (Section 4). While Accel-Sim is validated against real GPUs, we have no real silicon to prove the claimed throughput/energy numbers. The 45nm synthesis numbers don't directly translate to the 4nm H100 baseline they're comparing against.

**2. Throughput vs. Actual Speedup Disconnect:**
Figure 10 shows up to 1.93× throughput improvement, but Figure 11 shows only 44% execution time reduction (harmonic mean). Why the gap? Because the throughput metric is "operations per clock cycle" which doesn't account for flattening overhead or memory effects. The execution time is the real metric, and it's less impressive than the headline "74% throughput improvement" in the abstract.

**3. Single-SM Workload Characteristics:**
The benchmarks are all transformer-based models. What about CNNs with smaller tensor dimensions where block sizes may not align well with the 32-element flattened block? What about sparse workloads? The sensitivity study (Section 5.6) only varies scaling levels and block sizes on ViT-Large, not workload diversity.

**4. Memory Traffic Not Analyzed:**
The flattened format stores elements + scaling factors side by side. For MX9 with 32 elements per block, that's 32 bytes of elements + 1 byte of scaling factor per block. The paper doesn't quantify whether this layout increases memory traffic compared to the baseline's separate storage of scaling factors. They claim "optimized data layout" but don't show memory bandwidth utilization.

**5. Training Support is Handwavy:**
Section 3.2 describes an "unflattening API" for training that "leverages CUDA cores" and "introduces a long latency." They wave this away with "since unflattening occurs infrequently, its overhead has minimal impact." But training involves constant weight updates! Where's the training performance data? All experiments are inference-only (Table 3 descriptions, Section 5.1: "a single inference pass").

**6. Missing Comparison with Alternative Approaches:**
What about NVIDIA's upcoming Blackwell architecture? What about Google TPUs which handle bfloat16 with different scaling approaches? What about dedicated NPUs? The only comparison is software-on-H100 vs. Avant-Garde-on-H100.

**7. Energy Numbers Lack Component Breakdown:**
Figure 13 shows 40-49% energy reduction, but we don't see where the savings come from. Is it reduced CUDA Core activity? Shorter execution time at constant power? Reduced register file accesses? The breakdown would help validate the claimed mechanism.

---

## Q4: What the Authors Didn't Tell You

**1. The Flattening Overhead is Larger Than They Admit for Training:**
The paper buries a critical detail in Section 3.2: "These operations are performed on CUDA cores, they introduce a long latency." For inference, flattening weights once is fine. For training, every backward pass produces gradients that must be accumulated into weights, which then need re-quantizing and potentially re-flattening before the next forward pass. The "unflattening" → gradient update → "re-flattening" cycle per training iteration could be substantial. Their complete absence of training benchmarks is telling.

**2. The Block Size = 32 Constraint:**
Figure 5 and Section 3 reveal that Avant-Garde fixes the flattened block size at 32 to match GPU warp size. But what if the DNN's tensor dimensions don't divide evenly by 32? They mention "coalescing smaller blocks" but don't discuss padding overhead or utilization loss when tensor dimensions are irregular (e.g., embedding dimensions of 768 in BERT-base ÷ 32 = 24, so the last block is incomplete).

**3. The Scaling Factor Precision Matters:**
Table 2 shows MX9 uses an 8-bit first-level scaling factor but only 1-bit second-level. When they flatten, they're absorbing a 1-bit scale into 8-bit elements—that's a power-of-2 shift, not precision loss. But what about formats with larger sub-block scaling factors? The generalization claim (Section 3.6: "up to four scaling levels") assumes the math works out nicely, but wider scaling factors at lower levels would increase element bitwidth during flattening, potentially overflowing the 8-bit fixed-point representation.

**4. The Accumulator Precision Problem:**
Figure 8 shows the scaling unit applies combined scaling factors before accumulation. But MMA operations accumulate many partial products. If you're doing 16×16×16 MMA (256 products), the accumulated value can far exceed the individual product magnitudes. The paper doesn't discuss how the accumulator handles overflow or precision—they just say "The result of scaling unit then proceeds to the accumulator." What's the accumulator precision? FP32? FP16? This matters enormously for numerical stability in training.

**5. Memory Layout Change is a Software Ecosystem Problem:**
The paper proposes storing flattened blocks in memory (Section 3.1). This means model weights aren't stored in the original MX9/HBFP format anymore—they're in Avant-Garde's proprietary flattened format. If you want to deploy the same model on a non-Avant-Garde GPU (cloud heterogeneity is real), you need to convert back. The paper doesn't discuss model portability or the toolchain changes needed.

**6. The Real Competition is NVIDIA's Roadmap:**
NVIDIA's Blackwell architecture (announced 2024) supposedly has native support for "microscaling formats." The paper was submitted to ISCA 2025, published June 2025—by which time Blackwell might be shipping. If NVIDIA's solution is comparable, this architecture becomes a research curiosity rather than a production necessity. The "Related Work" section (Section 6) conspicuously avoids mentioning any unreleased NVIDIA architectures.

**7. Security Implications Unaddressed:**
Adding a new pipeline stage (Operand Transformer) and modifying Tensor Core behavior introduces new microarchitectural state. Post-Spectre/Meltdown, any new pipeline element is a potential side-channel vector. The paper has zero discussion of security implications—an oversight that would concern any production GPU vendor.

**8. The 2-Cycle Operand Transform Latency:**
Section 3.3 mentions the Operand Transform stage "introduces a latency impact of two cycles per warp." With 114 SMs, 4 warp schedulers per SM, and potentially deep warp queues, this might indeed be hidden by warp interleaving—but for compute-bound kernels where warps are tightly synchronized (common in MMA operations), even 2 cycles per warp could bubble the pipeline. Their sensitivity study claims <1% overhead, but that's on transformer models with ample parallelism. Smaller batch sizes or latency-sensitive inference would suffer more.

---

**Bottom Line:**

This is a well-constructed ISCA paper that identifies a real problem (software overhead for scaled numeric formats), proposes a clean solution (normalize everything to a flattened single-level format), and demonstrates substantial improvements (44% execution time reduction). The insight about format-agnostic processing through flattening is genuinely valuable.

However, the evaluation is inference-only despite claiming training support, the silicon overhead numbers are from 45nm synthesis applied to a 4nm baseline, and the competitive landscape with NVIDIA's own roadmap is conveniently ignored. If you're reading this to understand *whether to build this*, look carefully at whether NVIDIA/AMD will simply ship native MX support that makes this unnecessary. If you're reading to understand *how to think about scaled numeric formats in hardware*, this is an excellent tutorial disguised as a research paper.