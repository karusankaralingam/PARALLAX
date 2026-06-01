# Study B — Rich Directive
**Paper:** 2512.11269v1 Cerium  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-06-01 09:16

---

Q1: Whiteboard Explanation

Imagine you want to run machine learning inference on sensitive data—medical records, financial information—but the cloud provider shouldn't see any of it. Fully Homomorphic Encryption (FHE) lets you compute directly on encrypted data without ever decrypting it. The problem? FHE is brutally slow—over 10,000× slower than plaintext computation—and has enormous memory requirements.

Cerium is a multi-GPU framework that makes FHE practical for large models like BERT and Llama3-8B. Let me walk through the three core problems it solves:

**Problem 1: Kernel Performance Gap**
FHE operations work on polynomial "limbs"—residues of large integers decomposed via the Residue Number System. Prior work either mapped each operation to a separate kernel (too much overhead) or hand-crafted fused kernels for specific applications (doesn't scale). Cerium introduces a Limb IR—an intermediate representation where the compiler reasons about fusion at the limb level. It performs horizontal fusion (combining independent operations across RNS bases into one kernel launch) and vertical fusion (combining dependent operations to use registers instead of global memory). The compiler checks for cycles and cross-thread-block dependencies to ensure correctness, then generates optimized CUDA code with lazy modular reduction, warp shuffling for NTTs, and careful register management.

**Problem 2: Terabyte-Scale Memory**
Here's the shocking part: BERT-Base requires 1.5TB of encoded weights, Llama3-8B needs 112TB. This comes from the diagonal packing required for matrix multiplication in FHE, which creates massive redundancy. Cerium's key insight is that when redundancy follows power-of-two strides, there's exploitable symmetry. After NTT transformation, the evaluation form has repeated values in contiguous blocks—Cerium compresses these by storing only unique values. This yields 96× compression for BERT (1.5TB → 16GB) and 119× for Llama3-8B.

**Problem 3: Multi-GPU Scaling**
Single GPUs can't close the gap with FHE ASICs. Cerium builds on Cinnamon's limb-level parallelism but adds compiler passes that merge aggregate-scatter followed by all-gather into single all-reduce operations, reducing communication calls. The runtime overlaps computation and communication using separate CUDA streams.

The result: bootstrapping in 7.5ms (first sub-10ms on real hardware), BERT in 8.8 seconds, and performance within 1-4.4× of purpose-built FHE ASICs—using off-the-shelf GPUs.

Q2: The Key Insight

The central insight is that **limb-level operations in RNS-CKKS form the correct abstraction for automated kernel fusion on GPUs**—neither ciphertext-level operations (too coarse, misses optimization opportunities) nor raw polynomial operations (too fine-grained, explosive search space).

Limbs have five properties that make them ideal: (1) they're the atomic building blocks of practical CKKS, (2) they're embarrassingly parallel, (3) they naturally partition into distinct kernel classes (NTT, base conversion, elementwise) that shouldn't be fused across, (4) they're data-independent across different RNS bases (enabling horizontal fusion), and (5) they're data-dependent within the same RNS base (constraining vertical fusion).

This abstraction enables the compiler to reason efficiently about fusion correctness (cycle detection via parent/child set intersection) and performance (respecting kernel class boundaries) without exploring an intractable space of possible GPU kernels. Prior work like Cheddar achieved strong performance but required hand-crafted, application-specific fusion—Cerium automates this while achieving comparable or better results.

The secondary insight—that power-of-two strided redundancies in plaintext packing create exploitable symmetry in the NTT domain—is what makes large models tractable at all. Without the 100× compression, Llama3-8B would require two orders of magnitude more memory than exists in any practical system.

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive benchmark coverage**: The evaluation spans small (bootstrapping, ResNet-20), medium (BERT-Base), and large (Llama3-8B) workloads, demonstrating generality. Critically, they report end-to-end accuracy (91.4% ResNet-20, 69.3% BERT on GLUE RTE), validating that the approximations don't break model quality.

2. **Fair ASIC comparisons**: Normalizing to Cinnamon-8 and comparing across CraterLake, ARK, and Cinnamon provides meaningful context. The claim of matching CraterLake (1.06×) is specific and verifiable.

3. **Detailed ablation studies**: Figure 11 breaks down contributions of horizontal fusion (1.84-2.04×), vertical fusion (1.43-1.68×), CudaGraphs (7-14%), compression (96-119×), and memory scheduling (2.3-2.5×). Each technique's isolated impact is quantified.

4. **Multi-GPU scaling analysis**: Table I shows scaling across 1-8 GPUs on three GPU generations (A100, H100, B200), and Section V-E5 isolates the contribution of scheduling vs. communication optimization passes.

**Weaknesses:**

1. **Limited ASIC comparison scope**: The paper compares only bootstrapping, ResNet-20, and BERT against ASICs—not Llama3-8B. They note ASICs can't handle Llama3-8B due to memory constraints, but this conveniently sidesteps the comparison for their largest benchmark. A fairer analysis would project ASIC performance with hypothetical memory expansion.

2. **Cherry-picked GPU configurations**: Cheddar H100 results are excluded because they only have PCIe numbers, not SXM. This is reasonable but worth noting—the 1.21× speedup over Cheddar on A100 is modest; the gap might narrow on H100.

3. **Compilation time concerns**: 11 minutes for Llama3-8B compilation seems acceptable for one-time cost, but the paper doesn't discuss how this scales with model size or whether incremental recompilation is possible. For iterative development, this could be problematic.

4. **Missing energy and cost analysis**: ASIC comparisons focus solely on performance, but ASICs would likely have dramatically better performance-per-watt. A DGX B200 with 8 GPUs costs roughly $400K and consumes ~10kW; the economic argument for GPUs vs. ASICs is incomplete without this.

5. **Single-token Llama3-8B limitation**: They generate only the first token from a 128-token prompt. Autoregressive generation would require repeated inference, potentially compounding overheads. The 134-second latency for one token makes interactive use cases infeasible.

6. **Sparse plaintext compression constraints**: The 100× compression requires power-of-two stride redundancy in packing. This works for BSGS matrix multiplication but may not generalize to all FHE algorithms. The paper doesn't quantify what fraction of operations can exploit this.

Q4: What the Authors Didn't Tell You

**The accuracy story is incomplete**: They report 69.3% BERT accuracy on GLUE RTE, matching "plaintext accuracy." But BERT-Base typically achieves ~66-70% on RTE, which is near random for a binary task. The accuracy claim validates their polynomial approximations don't catastrophically fail, but doesn't demonstrate the model is actually useful. They should have evaluated on easier tasks where BERT clearly outperforms baselines.

**The Llama3-8B evaluation is a proof-of-concept, not a practical system**: 134 seconds per token is roughly 50,000× slower than standard inference. For a 100-token response, you're looking at 4+ hours of GPU time on $400K hardware. The paper frames this as a breakthrough (first FHE Llama inference), but the practical utility is near-zero without orders-of-magnitude improvement.

**Memory compression has hidden costs**: The sparse plaintext encoding requires preprocessing to compute the compressed representation, and the indexing transformations during code generation add computational overhead. The paper quantifies memory savings but not the compute overhead of working with compressed representations.

**Multi-GPU scaling is underwhelming**: Going from 1 to 8 B200 GPUs improves bootstrapping from 14.5ms to 7.5ms—only 1.93× speedup for 8× the hardware. For BERT it's 3.2× (28.3s → 8.8s). This suggests communication overhead dominates at scale. The 44% communication reduction from their optimization passes implies the baseline was severely bottlenecked.

**The comparison to Cinnamon is philosophically awkward**: Cinnamon is from the same research group (Jayashankar is first author on both). Cerium builds directly on Cinnamon's parallel keyswitching algorithms. Claiming "Cerium is 4.4× slower than Cinnamon-8" understates that Cinnamon is a hypothetical ASIC while Cerium runs on real hardware today.

**Evaluation key storage is glossed over**: They mention evalkeys are 10-100GB but don't detail how this scales with model size or security parameters. For Llama3-8B, keeping evalkeys pinned in GPU memory while shuttling 982GB of weights creates complex orchestration that isn't fully characterized.

**No comparison to hybrid HE/MPC approaches on latency-sensitive metrics**: They mention BOLT requires 91s for BERT on LAN, vs. 8.8s for Cerium. But MPC approaches have interactive communication—Cerium's advantage may diminish in WAN settings or for batched queries where MPC can amortize setup costs.