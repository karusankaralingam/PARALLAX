# Avant-Garde: A Deep Dive into GPU Microarchitecture for Scaled Numeric Formats

## Q1: Whiteboard Explanation

Alright, let me break this down like we're standing at a whiteboard.

**The Problem They're Solving:**

Modern DNNs are massive—GPT-3 has 175 billion parameters and needs 3×10²³ arithmetic operations to train (Section 1). Moore's Law is hitting walls, so we can't just shrink transistors anymore. The solution? Use smaller numbers—8-bit, 6-bit, even 4-bit representations instead of 32-bit floats. But here's the catch: if you just chop bits off, you lose too much precision and your model becomes garbage.

**Enter Scaled Numeric Formats:**

Think of it like scientific notation. Instead of storing "0.000000123" as a tiny float, you store "1.23" with a shared exponent "-7" for a whole group of numbers. These formats (FP8, MX4, MX6, MX9, HBFP) group numbers into "blocks" that share a "scaling factor."

The twist is **levels**:
- **Single-level**: One scaling factor for, say, 32 elements (like HBFP with 64 elements sharing one 8-bit exponent)
- **Multi-level (MX9)**: 16 elements share a first-level scaling factor, but within that, every 2 elements share a second-level 1-bit microexponent (Figure 1b)

**Why Current GPUs Suck at This:**

NVIDIA's H100 Tensor Cores can do FP8 natively, but anything fancier requires software gymnastics. Look at Figure 3—the instruction stream is *ugly*. To do one matrix multiplication with a scaled format, you need:
1. Load scaling factors into registers (ld.global R16-R19)
2. Multiply scaling factors together (mul)
3. Apply scaling factors to dot product results (mad instructions)

Figure 4 shows the damage: MX9 uses **1.38× more registers** and **2.14× more instructions** than plain INT8. All those extra instructions and registers mean fewer warps can run concurrently, killing performance.

**Avant-Garde's Solution—"Flattening":**

The core insight is: *convert everything to a single-level format once, then compute on that consistently*.

Here's the pipeline (Figure 6):
1. **Operand Transformer** (new hardware unit): Takes multi-level formats and "flattens" them. For MX9, this means multiplying the 1-bit second-level exponents into the elements, leaving just one scaling factor per block.
2. **Avant-Garde Tensor Core** (modified): Has an 8-bit fixed-point adder to combine scaling factors from both operands, plus a "Scaling Unit" that multiplies the dot product result by the combined scaling factor *before* accumulation (Figure 8).
3. **Store flattened operands**: Once flattened, keep them that way in registers and memory. No need to re-flatten on every operation.

The data flow (Figure 5):
- Block size ≤16? Flatten, then coalesce multiple blocks into one 32-element "flattened block"
- Block size = 32? One-to-one mapping
- Block size > 32? Split into multiple flattened blocks, each keeping the original scaling factor

**Why This Works:**

- Flattening is a **one-time preprocessing step** (weights flattened before inference, inputs flattened at ingestion)
- Tensor Core directly handles scaling factors in hardware—no CUDA Core detour
- Eliminates 52-66% of instructions (Figure 12)
- Result: **1.74× throughput improvement**, **44% reduction in inference time** (harmonic mean across benchmarks)

---

## Q2: The Key Insight

**The Real Innovation:**

The paper's central insight is deceptively simple but architecturally profound: *Multi-level scaled numeric formats can be losslessly transformed into a single-level "flattened" representation, and if you do this transformation in dedicated hardware once (not repeatedly in software), you can compute on all scaled formats using a unified Tensor Core datapath.*

This is **not** just "add hardware to speed up scaling operations." It's recognizing that:

1. **All scaled formats converge to a common computational substrate.** Whether you have MX9's two-level hierarchy or HBFP's large single-level blocks, after flattening, they all look the same: blocks of fixed-point elements with one shared scaling factor.

2. **The flattened representation is stable.** Once flattened, operands stay flattened through register file, memory, and back. This is key—you're not paying the flattening cost per-operation, you're paying it per-data-movement-boundary.

3. **Scaling factor arithmetic is trivially parallelizable.** Since scaling factors are exponents, combining them is just addition (one 8-bit add per block pair). The Scaling Unit then applies a single multiplication to the dot product result. This is much cheaper than per-element scaling.

**What Makes This Different from Prior Work:**

Previous accelerators (DBPS, FAST, Bucket Getter—Section 6) focused on single-level BFP formats. Microsoft's MSFP work introduced multi-level formats but didn't solve the GPU integration problem. Avant-Garde is specifically targeting **GPU Tensor Cores**, which have a very specific execution model (SIMT, warp-level operations, 128-byte warp registers).

The insight that flattening aligns naturally with the 32-thread warp structure (Figure 5 shows flattened blocks sized to warps) is the architectural glue that makes this practical.

**The Mechanism vs. The Policy:**

- **Mechanism**: Operand Transformer hardware (16 FP8/INT8 multipliers + 32 temporary registers, Figure 7), modified Tensor Core with scaling adder and scaling unit
- **Policy**: Block sizes coalesce to warps, flattening happens at data ingestion, results stay flattened or convert back based on API flags (wmma::matrix_a/b vs wmma::accumulator, Section 3.2)

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Solid Baseline and Fair Comparison (Mostly)**

They use NVIDIA H100 as baseline (Table 1), which is the current state-of-the-art with native FP8 support. They don't compare against some strawman—they implement software-based scaled format handling using the actual WMMA API and measure real register/instruction overhead (Figure 4). The 1.38× register overhead and 2.14× instruction overhead numbers come from actual nvcc compilation analysis (Section 2.2).

**2. Comprehensive Workload Selection**

Table 3 shows a mix of microbenchmarks and real DNN models (ViT-Base/Large, BERT, GPT-2) across vision and NLP. They're not cherry-picking one model where their technique shines.

**3. Format Coverage**

They test HBFP (single-level, 64-block), MX9 (two-level), and MXFP8 (single-level, 32-block)—covering the major emerging scaled formats (Table 2). This isn't a one-trick pony.

**4. Accuracy Validation**

Table 4 shows flattened MX9 matches non-flattened MX9 and deviates <0.2% from FP32 on real models. This is critical—if flattening introduced significant quantization error, the whole premise collapses.

**5. Area/Power Overhead is Reasonable**

Section 3.3: 1.4% area, 1.2% power overhead for the whole modification. The Operand Transformer is 1.2% area/1.7% power per SM; modified Tensor Core is 3.9% area/3.1% power. These are synthesized numbers (FreePDK 45nm), which is better than hand-waving.

### Weaknesses

**1. Simulation-Only Evaluation**

All performance numbers come from Accel-Sim (Section 4). They don't have silicon, and they don't have an FPGA prototype. The accuracy numbers (Table 4) come from Microsoft's MX emulator, not the actual hardware datapath. This is a common limitation, but it means they can't validate:
- Real timing closure at target frequency
- Actual memory system behavior under their modified data layout
- Whether the 2-cycle Operand Transformer latency (Section 3.3) holds under realistic conditions

**2. FP8 Handling is Suspicious**

Quote: "As Accel-Sim does not support FP8, we modify the simulator to compute a scaling factor so that FP8 operations execute with the same latency as INT8" (Section 4). This is a modeling assumption, not a validated fact. NVIDIA's actual FP8 Tensor Core may have different characteristics.

**3. Sensitivity Study is Thin**

Section 5.6 claims they tested up to 4 scaling levels and block sizes up to 512, but then says "As the overall performance across configurations shows minimal variation, we omit a plot for this analysis." That's convenient. Show the data. What happens at 3 or 4 scaling levels? What's the actual breakdown of flattening overhead vs. compute time?

**4. Memory Traffic Analysis is Missing**

They claim flattened blocks can be stored in memory (Figure 5), but don't analyze:
- The memory footprint increase from flattening (flattened formats may be larger than original compressed formats)
- The impact on memory bandwidth for weight/activation storage
- Whether the "optimized data layout" (Section 3) actually helps or hurts memory coalescing

**5. Training Evaluation is Weak**

They mention unflattening for training (Section 3.2), but all their benchmarks are inference-only. The unflattening API "leverages CUDA cores" and "introduces a long latency"—how long? What's the training iteration time impact? Section 3.2 says "since unflattening occurs infrequently, its overhead has minimal impact on overall GPU performance," but they don't quantify this.

**6. Workload Representativeness**

ViT-Base (86M), BERT (110M), GPT-2 Small (124M) are relatively small models. Modern LLMs are 7B-175B+ parameters. The authors acknowledge "performance gains slightly diminish with increasing model size" (Section 5.1)—ViT-Large shows 6% less improvement than ViT-Base. Does this trend continue for truly large models?

**7. No Real Comparison Against NVIDIA's Native FP8**

They say "Avant-Garde supports FP8 in the same manner as the baseline" and show that for FP8 workloads, Avant-Garde adds only 0.1% energy overhead due to leakage (Section 5.4). But this misses the point—the interesting comparison would be: *Is MX9 on Avant-Garde better than FP8 on baseline H100?* They show MX9 gives 1.93× throughput improvement over software-based MX9, but not against native FP8.

---

## Q4: What the Authors Didn't Tell You

**1. The Memory Footprint Expansion Problem**

Look carefully at Figure 5. When you flatten a multi-level format, you're "baking in" the sub-block scaling factors into the elements. MX9 has:
- 16 elements × 8 bits = 128 bits for elements
- 8 bits for first-level scaling factor
- 8 × 1 bit = 8 bits for second-level scaling factors
- **Total: 144 bits for 16 elements = 9 bits/element**

After flattening:
- 16 elements × 8 bits = 128 bits (now wider because scaling factors absorbed)
- 8 bits for the retained scaling factor
- **Total: 136 bits... wait, that's smaller?**

Actually, the paper is vague on what bitwidth the flattened elements have. Section 3.2 says Operand Transformer uses "FP8/INT8 multipliers"—if you're multiplying an element by a scaling factor, the result needs more bits to avoid overflow. The temporary registers are "each occupying 32 bytes" (Section 3.2). So a flattened block might be **wider** than the original format, increasing memory footprint and bandwidth requirements. The paper never addresses this.

**2. The Real Latency of Operand Transformer**

Section 3.3 says "introduces a latency impact of two cycles per warp due to iterative flattening for multi-level formats." Section 3.2 says "For a scaling level of N, Operand Transformer performs 2 × (N − 1) iterations."

So for MX9 (2 levels): 2 × (2-1) = 2 iterations × 2 cycles = 4 cycles? Or is it 2 cycles total? The paper is unclear. And with 16 multipliers handling 32 elements (Figure 7), you need "reuse 16 multipliers twice"—that's another 2 cycles minimum per iteration.

They claim this is hidden by warp interleaving, but GPU Tensor Cores are already deeply pipelined. Adding a non-trivial preprocessing stage that can stall the operand path is a real concern. The sensitivity study (Section 5.6) hand-waves this: "operand transformation accounts for less than 1% of total execution time." Show the numbers.

**3. The Unflattening Elephant**

For training, you need to write gradients back in the original scaled format. The unflattening API (Section 3.2) does this on CUDA Cores—the same CUDA Cores the paper criticizes for being slow at scaling operations!

Quote: "As these operations are performed on CUDA cores, they introduce a long latency. However, since unflattening occurs infrequently, its overhead has minimal impact on overall GPU performance."

"Infrequently" for inference (once at the end). For training, you unflatten after **every** gradient computation. This could negate the entire benefit. The paper doesn't evaluate training workloads.

**4. The API Complexity Tax**

Figure 9 shows the Avant-Garde API. Notice how the programmer must:
1. Declare the scaled format type (scaled mx9)
2. Call mx9.getSize() for memory allocation
3. Explicitly call flatten() on weights and inputs
4. Configure wmma fragments with the format parameter

Compare this to native FP8 on H100, where you just... use FP8. The burden is shifted to the programmer. The paper assumes "programmers understand the data layout" (Section 3), but this is non-trivial for multi-level formats.

**5. The Block Size Constraint**

The paper fixes the flattened block size to warp size (32 threads). But what if your format has blocks that don't divide evenly? HBFP uses block size 64 (Table 2)—so two flattened blocks per original block. MX9 uses block size 16—so two original blocks coalesce into one flattened block.

What about block size 17? Or 33? The paper doesn't discuss misalignment. Real-world formats might not be nicely warp-aligned.

**6. The Energy Numbers are Execution-Time Dominated**

Figure 13 shows 40-49% energy reduction. But this is primarily because execution time dropped 44% (Section 5.2) while power increased only 1.2% (Section 3.3). This is standard: shorter runtime = less energy. The paper doesn't show energy efficiency (performance/watt), which would be a more meaningful metric.

**7. What About Sparsity?**

Modern DNN acceleration increasingly relies on sparsity (NVIDIA A100/H100 have structured sparsity support). Scaled numeric formats + sparsity = complex interaction. The paper doesn't mention sparsity at all. If your model is 50% sparse, does flattening break sparsity patterns? Does the Avant-Garde Tensor Core support structured sparsity?

**8. The Baseline Software Implementation Might Be Naive**

Figure 3 shows their software implementation of scaled format handling. But NVIDIA's cuBLAS/cuDNN have highly optimized FP8 handling. Is the 2.14× instruction overhead (Figure 4) representative of what a production-quality library would produce, or is it their own unoptimized implementation?

The paper says they "implement a GPU program that performs MMA operations" and "compile the program with nvcc"—this sounds like hand-written CUDA, not optimized library calls. A fair baseline would use the best available software implementation, not a research prototype.