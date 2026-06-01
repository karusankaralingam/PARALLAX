## Q1: Whiteboard Explanation

Let me walk you through what Hybe actually *does* at the hardware level.

**The Core Problem (Figure 1 & Figure 2):**
LLM inference has two distinct phases with radically different computational profiles:
- **Prefill stage**: Process all input tokens at once → GEMM operations → Compute-bound
- **Decode stage**: Generate one token at a time → GEMV operations → Memory-bound

The roofline in Figure 2(a) shows this starkly: H100 GPU sits in the compute-bound region for prefill (32.7% utilization) but crashes to 0.13% utilization during decode. The GPU's 1,979 TFLOPS of tensor cores become a liability—they draw power but do nothing useful during GEMV.

**The Architectural Split (Figure 4):**
Hybe's "trick" is phase disaggregation with hardware specialization:
1. **GPU handles prefill only**: Uses existing H100 with tensor cores for GEMM-heavy attention over the full input context
2. **Custom NPU handles decode only**: A lightweight chip with *exactly* enough compute to saturate memory bandwidth—nothing more

**The NPU Architecture (Figure 6 & Figure 10):**
The NPU is deliberately "underpowered" by design:
- **32 MAC trees** × **64-element vectors** × **1 GHz** = **4 TFLOPS (FP16)**
- **3.35 TB/s HBM3 bandwidth** (same as H100)
- Arithmetic intensity for GEMV is ~0.5 OPS/byte, so 3.35 TB/s × 0.5 = 1.68 TFLOPS needed
- The 4 TFLOPS provides headroom for non-GEMV operations (softmax, layernorm)

The chip area is **0.84 mm²** (83.2 mm² with HBM PHY)—tiny compared to H100's ~814 mm² die. The MPU (the actual MAC trees) is only **0.22 mm²** consuming **0.12W**.

**Memory Mapping (Figure 5):**
The dataflow is output-stationary with careful HBM channel mapping:
- Weights/Keys/Values are tiled in chunks of (d_vec × n_MT) = 64 × 32 = 2048 elements
- Each MAC tree is permanently bound to specific HBM channels
- This enables burst reads that stream directly into compute without buffering

**The KV Transfer Protocol (Figure 7 & Figure 8):**
The "fine-grained KV transmission" is really about *when* and *how* KV moves:
1. GPU generates QKV in groups (by attention heads)
2. As each group completes, KV is immediately DMA'd to NPU via PCIe
3. An **on-the-fly reshaper** in the GPU transposes data to match NPU's memory layout
4. Previous group's KV memory is freed, reducing GPU memory footprint from 3m to m (Figure 7)

**The Pipelining (Figure 9):**
Stage-wise pipelining with two recovery mechanisms:
- **Overloading**: When input is short (GPU finishes early), GPU starts next request's prefill before NPU finishes current decode
- **Offloading**: When output is short (NPU finishes early), NPU can do GEMV-style prefill for next request

---

## Q2: The Key Insight

**The Single Clever Observation:**
*For million-token context windows, the KV cache is so large (128GB+ for Llama-3 1M) that batching becomes impossible, which means GPUs will always run decode at GEMV with ~0.13% utilization—so you might as well use cheaper hardware that matches compute to bandwidth.*

The paper's key formula (Section 4.2):
```
d_npu/d_gpu = (c_gpu/c_npu) / (n_in/n_out)
```

This isn't just device allocation—it encodes the fundamental insight that when KV cache dominates memory, the problem becomes entirely memory-bound, and the optimal compute is whatever exactly saturates your memory bandwidth. The NPU achieves this with:
- **Compute**: 4 TFLOPS
- **Bandwidth**: 3.35 TB/s
- **Ratio**: ~1.2 OPS/byte (matching GEMV arithmetic intensity)

**Why this matters architecturally:**
The H100's 1,979 TFLOPS / 3.35 TB/s = 591 OPS/byte ratio is grotesquely mismatched to GEMV's ~0.5-1 OPS/byte requirement. The NPU's 4/3.35 ≈ 1.2 OPS/byte ratio is precisely tuned.

**The "delta" vs. baseline:**
- Traditional approach: Use GPU for everything, accept underutilization
- Splitwise approach: Use different GPUs (still both over-provisioned for decode)
- NeuPIMs/IANUS: NPU+PIM, but PIM handles only attention (interleaving problem)
- **Hybe**: Complete phase separation—GPU sees only prefill, NPU sees only decode

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**S1: Real Silicon Numbers (Figure 10)**
The NPU is synthesized in Samsung 4nm with actual place-and-route. They report:
- 0.84 mm² core area
- 0.29W chip power, 117.8W with HBM
- Gate-level power using actual activation vectors

This is far more credible than most architectural papers that stop at RTL simulation.

**S2: Fair Device Count Comparison (Table 1)**
They compare 1 GPU + 5 NPUs vs. 6 GPUs (or 3×2 GPU sets). The total device count is equal, making the efficiency comparison meaningful. The 127:1 input-output ratio (Section 7.2) is justified by citing Google Gemini 1.5 Pro's actual configuration.

**S3: Utilization Data is Compelling (Figure 15)**
The decode stage utilization numbers are specific:
- H100: ~20% bandwidth utilization, ~12% core utilization
- Hybe NPU: ~90% bandwidth utilization, ~75% core utilization

**S4: Scalability Analysis (Figure 16)**
They show H100 tensor parallelism scaling at only 1.3× for 2 GPUs (should be 2×), while NPU model parallelism achieves 3.91× for 5 devices—demonstrating that the NPU's overlap of computation and communication (Section 5.2) actually works.

### Weaknesses

**W1: The HBM3 Assumption is Heroic**
The NPU has "equal HBM specification" to H100 (Section 7.1)—80GB HBM3 with 3.35 TB/s bandwidth. This means each NPU needs a 5-stack HBM3 configuration. The paper states PHY area is 82.4 mm² (83.2 - 0.84), which is >98% of the total! They don't discuss:
- HBM3 procurement challenges
- Interposer/2.5D packaging costs
- Whether Samsung 4nm is actually HBM3-qualified

**W2: PCIe Bandwidth Saturation (Section 8.2)**
They claim 331.76 MB/s for KV transmission + 8.57 MB/s for NPU sync = ~340 MB/s, well under PCIe Gen5's 64 GB/s. But they're running **5 NPUs** sharing that bus. With 5 concurrent transfers during pipelining (Figure 9d), that's still only ~1.7 GB/s—but what about:
- PCIe switch topology?
- Protocol overhead?
- Contention during overlapping KV transfers?

**W3: Prefill Performance is Worse (Table 2)**
TTFT for Llama-3-8B: GPU system 270.66s vs. Hybe 424.09s—**1.57× slower**. They bury this in a table. For latency-sensitive applications, waiting 7+ minutes for the first token is catastrophic, regardless of decode efficiency.

**W4: The "Optimal" Configuration is Fragile**
The 1 GPU : 5 NPU ratio works for 127:1 input-output ratio. But Figure 13(b) shows efficiency drops when ratio changes. They claim "efficiency is constant" but don't show what happens at 255:1 or 63:1 ratios where device allocation would be suboptimal.

**W5: No End-to-End System Measurement**
Section 7.1 admits: "For GPU-NPU communication, we simulate the PCIe DMA transfer using the OpenCL-based runtime protocol." The GPU runs on real H100, NPU runs in simulation, communication is simulated. They never ran the actual hybrid system end-to-end.

---

## Q4: What the Authors Didn't Tell You

**1. The NPU is Mostly HBM**
Figure 10 shows chip area of 0.84 mm². With PHY it's 83.2 mm². That means **99% of the "NPU" is memory interface**, not compute. They're essentially building a minimal controller chip to feed HBM to a network—the "NPU" is really an HBM controller with MAC trees bolted on.

**2. The Power Numbers are Misleading**
They report NPU chip power of 0.29W, but "average inference power" of 117.8W (Figure 10). Where's the other 117.5W? It's the HBM stacks (~100W) plus PCIe PHY. The "efficiency" gain comes from not powering tensor cores, not from clever NPU design.

**3. Bus Mastering Complexity is Glossed Over**
Section 4.1 states: "Hybe utilizes bus mastering that enables any device to control the bus." In a 6-device PCIe topology with dynamic bus mastering, arbitration becomes non-trivial. They don't discuss:
- Deadlock avoidance
- Priority inversion during FGKVT
- What happens when GPU and NPU both want bus master simultaneously

**4. The Reshaper Cost is Hidden**
Figure 4 shows a "KV Reshaper" in the GPU. Section 6.1 says "the reshaping is executed at runtime between transmission and reception." This is matrix transposition on the fly—potentially expensive. They modified vLLM CUDA kernels (Section 7.1) but don't report:
- Added GPU cycles for reshaping
- Whether this contends with prefill computation
- Memory bandwidth consumed by the reshape

**5. Model Parallelism vs. Tensor Parallelism**
Section 4.1 casually states: "GPUs are connected by NVLink...and NPUs are connected by PCIe. The GPUs and NPUs manage tensor parallelism and model parallelism, respectively."

This is a significant difference. Tensor parallelism (GPU) requires all-reduce at every layer. Model parallelism (NPU) partitions heads/FFN columns. They claim NPU synchronization is overlapped (Section 5.2), but tensor parallelism for GPU isn't—this asymmetry isn't analyzed.

**6. The Scheduler Assumes Predictable Latency**
Algorithm 1 estimates latency reduction using FLOPS ratios. But:
- Memory contention varies with KV cache occupancy
- HBM refresh timing isn't deterministic
- PCIe has variable latency under load

The "Hybe scheduler" is really a static allocation policy, not a dynamic scheduler.

**7. They Never Address the Chicken-and-Egg Problem**
Hybe requires: (1) custom NPU silicon, (2) modified vLLM, (3) custom compiler, (4) custom runtime with OpenCL extensions. For anyone wanting to reproduce this, you need to build a chip first. The paper is really a product pitch for HyperAccel's NPU, not a reproducible research contribution.