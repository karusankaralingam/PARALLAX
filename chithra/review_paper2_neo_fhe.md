# Title: Neo — Accelerating FHE on GPU Tensor Cores

## 1. Whiteboard explanation — You're explaining this paper's mechanism to a smart colleague who hasn't read it. Walk through what they built and how it works.

Fully Homomorphic Encryption (FHE) lets you compute on encrypted data without decrypting it, but real workloads run orders of magnitude slower than plaintext. Custom ASICs are the obvious answer, but the FHE algorithm stack is still evolving fast — by the time you tape out, the schemes have moved on. So Neo asks: can we make FHE fast on the accelerator that every datacenter already has — the GPU's tensor cores?

The bottleneck Neo targets is **KeySwitch**, the operation that dominates CKKS runtime. Structurally, KeySwitch is a pile of large element-wise polynomial multiplications across RNS limbs. Mapped naively to CUDA cores, each limb is touched once and streamed back to memory — almost no data reuse, so the GPU is bandwidth-bound and the tensor cores sit idle.

Neo's mechanism has two coupled moves:

1. **Reshape KeySwitch as GEMM.** Using the KLSS formulation, the element-wise multiplications across limbs are reorganized into dense matrix multiplications. Once it's a matmul, tiles can stay register- or shared-memory-resident and each operand is reused many times — the same data-layout trick that made transformers fast on GPUs.
2. **Run that GEMM on the FP64 tensor-core path, not INT8.** FHE polynomial coefficients are 60+ bits. On INT8 tensor cores you have to decompose each coefficient into many small limbs and stitch the results back, which destroys reuse and inflates memory traffic. FP64 tensor cores hold a coefficient in a single lane, so Neo can use a *larger* KeySwitch wordsize — which directly lowers algorithmic complexity (fewer KeySwitch decompositions) while keeping reuse high.

Across CKKS workloads, this delivers a 3× speedup over TensorFHE, the prior tensor-core implementation.

## 2. What is the key insight that makes it work? (The "aha" — not what they did, but why it works)

The "aha" isn't "use tensor cores" — TensorFHE already did that on INT8. It's that **the right precision flips the cost model.** On INT8, you fight the hardware: large FHE coefficients force decomposition, decomposition kills reuse, and reuse loss erases the tensor-core advantage. On FP64, the natural unit of FHE arithmetic (one big polynomial coefficient) maps cleanly onto one tensor-core lane, which simultaneously enables (a) reformulating KeySwitch as GEMM, (b) raising the wordsize to cut algorithmic work, and (c) keeping tiles register-resident. All three wins are gated on the same precision choice.

## 3. What's the strongest aspect of the evaluation, and what's the weakest? (Methodology critique)

**Strengths:**
- Memory-access profiling of KeySwitch motivates the GEMM reformulation empirically rather than asserting it — the bottleneck argument is grounded in measurement.
- The KLSS wordsize knob is swept end-to-end, so the algorithmic-vs-hardware-cost tradeoff is pinned down by experiment rather than chosen by hand.
- Optimizations are clearly tied to specific levels of the GPU memory hierarchy (register / shared / global), which makes the speedup attributable to identifiable architectural mechanisms instead of a black-box "we tuned it."

**Weakest Parts:**
- **No power or perf-per-watt numbers.** TCU utilization is reportedly higher than TensorFHE, so absolute power likely is too. For an ISCA-class evaluation that implicitly competes with FHE ASICs, the absence of an energy axis is a significant gap.
- **No comparison against FHE ASICs or FPGAs** (e.g., F1, CraterLake, BTS, ARK). Without it, the reader cannot tell whether "GPU is good enough" or "GPU still loses badly, just less badly."
- Presentation issues hurt evaluability: dense FHE terminology with little onboarding for an architecture audience, plus typos in Table 3, the related work section, and the conclusion.

## 4. What did the authors not tell you? (Hidden assumptions, missing comparisons, unstated limitations)

- **Hardware specificity.** FP64 tensor cores are a server-GPU feature (A100/H100-class). Consumer and many edge GPUs either lack them or have them heavily de-rated. The "use what's already in the datacenter" framing only holds for one slice of the GPU market.
- **Noise budget / error propagation.** KeySwitch is the noisiest CKKS operation, and the KLSS reformulation changes the rounding/decomposition path. The paper doesn't characterize how the new scheme affects the noise budget or the achievable multiplicative depth before bootstrapping is required.
- **Scheme generality.** Results are CKKS-only. Whether the same GEMM reformulation pays off for integer schemes (BFV/BGV) or for TFHE-style gate bootstrapping — which has a very different access pattern — is left open.
- **End-to-end framing.** Real CKKS workloads are dominated by bootstrapping, which itself contains KeySwitch but also rotations and hoisting. End-to-end bootstrapped-inference numbers would clarify how much of the 3× translates into user-visible speedup.

## 5. What's the connection to ideas outside this paper's scope? (Cross-domain links, broader implications)

- **"Express it as GEMM and ride the wave."** Neo is another data point in a now-familiar pattern: transformers, dense scientific simulation, GNNs, and now FHE all win by reshaping their compute into matmul so they inherit decades of GEMM optimization. Anything that *can* be GEMM-ified eventually is.
- **Confidential computing economics.** If FHE on commodity GPUs gets within roughly an order of magnitude of plaintext, privacy-preserving ML inference becomes a procurement question rather than a hardware-availability question — the same H100s used for LLM serving can also serve encrypted queries.
- **ASIC vs. commodity tension.** Neo strengthens the argument that fast-moving algorithmic domains (FHE, post-quantum crypto, MoE-style ML) are better served by flexible accelerators than fixed-function ASICs, at least until the algorithms stabilize — the same tension currently playing out in the AI accelerator market.
