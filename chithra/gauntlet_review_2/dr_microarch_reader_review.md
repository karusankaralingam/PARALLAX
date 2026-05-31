# Neo: Decoding the Tensor Core FHE Acceleration Trick

## The Whiteboard Explanation

Let me walk you through what this paper actually does, stripped of the cryptographic jargon.

**The Problem Setup:**
Fully Homomorphic Encryption (FHE) lets you compute on encrypted data. The CKKS scheme they're accelerating works with polynomials of degree N=65,536, where each coefficient is a 36-60 bit integer. The killer operation is **KeySwitch** - it's called constantly and involves:
1. **BConv (Base Conversion):** Transform polynomials between different modular representations
2. **NTT (Number Theoretic Transform):** FFT-like operation for fast polynomial multiplication  
3. **IP (Inner Product):** Multiply-accumulate with large evaluation keys

The baseline approach (TensorFHE) uses the **INT8 Tensor Cores** on A100 GPUs. They split 32-bit integers into 8-bit chunks, do matrix multiplications, then reassemble. Sounds clever, but there's a catch.

**The Data Flow Reality:**
```
Input: α polynomials × BatchSize × N coefficients
       (4 levels × 128 ciphertexts × 65536 coefficients)
       
BConv: Each coefficient gets multiplied by α' conversion factors
       Original: Load coefficient α' times → terrible reuse
       
IP: Each polynomial multiplied by β̃×β evaluation keys  
    Original: Load coefficient β̃ times → terrible reuse
```

The paper's insight: These are really **matrix multiplications in disguise**. BConv is `(BatchSize×N) × α × α'` and IP is `(BatchSize×N) × β × β̃`.

---

## The 'Aha!' Moment

The clever part is **not** using INT8 Tensor Cores. Here's why:

**The INT8 Fragment Shape Problem:**
- A100 INT8 Tensor Cores require fragments of 16×16×16, 32×8×16, or 8×32×16
- BConv has dimensions α=4, α'=8 (from their KLSS parameters)
- Mapping 4×8 to 32×8×16 means **75% of computation is padding waste**

**The FP64 Revelation (Figure 11):**
- FP64 Tensor Cores use 8×8×4 fragments
- 4×8 maps to 8×8×4 with **zero padding**
- FP64 mantissa is 53 bits → can represent integers up to 2^53 exactly

**The Booth Complexity Trade-off:**
For 36-bit integers:
- INT8 path: Split into 5 chunks → 5×5=25 partial products
- FP64 path: Split into 3 chunks (12 bits each) → 3 partial products

For 48-bit integers:
- INT8 path: 6 chunks → 36 partial products  
- FP64 path: 4 chunks → 4 partial products (since 48/12=4, and 2^36 × 2^12 × 16 < 2^53)

**Figure 3 shows the punchline:** FP64 is 1.65× faster than INT8 for 36-bit, 1.74× faster for 48-bit.

---

## The Skeptic's Check

### Hidden Overhead #1: Data Rearrangement
Look at Algorithms 2 and 4. Before matrix multiplication:
- BConv: Transpose from `α×BatchSize×N` to `N×BatchSize×α`
- IP: Transpose from `β×α'×BatchSize×N` to `N×α'×BatchSize×β`

After matrix multiplication: Transpose back.

They claim this is "fused" into the kernel using shared memory, but Figure 13 shows preprocessing/postprocessing is **not negligible** - it's visible in the breakdown. The paper doesn't quantify this overhead separately.

### Hidden Overhead #2: The KLSS Method Itself
They switched from the "Hybrid" KeySwitch to "KLSS" method. Table 2 shows KLSS adds a **Recover Limbs** step that Hybrid doesn't have: `2α'(l+α)` complexity.

The KLSS method also requires:
- New parameter `WordSize_T` (they pick 48 bits)
- New parameter `α'` constrained by security (Equation 4)
- More evaluation key storage: `β̃×β×α'` polynomial keys

### Hidden Overhead #3: The 80% Threshold Hack
Section 4.5.3 admits: "When the valid proportion calculated from the parameters exceeds 80%, the matrix multiplication steps of IP are mapped to the FP64 components in TCUs; **otherwise, they are mapped to the CUDA Cores**."

This means IP has **two completely different code paths** depending on the current ciphertext level `l`. As `l` decreases during computation, `β` and `β̃` shrink, and the valid proportion drops (Figure 12). They're dynamically switching between TCU and CUDA Core execution mid-application.

### Hidden Overhead #4: Memory Capacity Limits BatchSize
From Section 6.3: "Due to the limitations of GPGPU memory capacity, BatchSize cannot be increased indefinitely."

A100 has 40GB HBM. With N=65536, L=35, WordSize=36:
- One ciphertext ≈ 2 polynomials × 36 levels × 65536 coefficients × 8 bytes ≈ 37MB
- BatchSize=128 → 4.7GB just for input ciphertexts
- Evaluation keys for KLSS: `β̃×β×α'×N×8` bytes per key set

The 128 BatchSize isn't a performance choice - it's a memory constraint.

### The Comparison Fairness Question
Table 5 compares against TensorFHE, but:
- TensorFHE used WordSize<32 bits originally
- They "reimplemented TensorFHE with DS integrated" (footnote ‡)
- HEonGPU comparison uses different parameters (Set-E vs Set-C)

The 3.28× speedup claim is against their own reimplementation of TensorFHE with parameters TensorFHE wasn't designed for.

---

## The Actual Hardware Utilization

**A100 Peak Performance:**
- CUDA Core FP64: 9.7 TFLOPS
- TCU FP64: 19.5 TFLOPS  
- TCU INT8: 624 TFLOPS

**What Neo Actually Uses:**
- FP64 TCU for matrix multiplications (19.5 TFLOPS theoretical)
- CUDA Cores for modular reduction, transposition, scalar operations

The paper never reports achieved TFLOPS or memory bandwidth utilization. Given that:
- NTT is memory-bound (each coefficient touched once per butterfly)
- BConv/IP after transformation are compute-bound matrix multiplications

I'd estimate they're hitting maybe 30-50% of peak FP64 TCU throughput, limited by the preprocessing/postprocessing overhead and the small matrix dimensions (α=4, α'=8 means tiny matrices).

---

## Discussion Question

**Ask yourself:** The paper claims FP64 Tensor Cores are better than INT8 for FHE because of Booth complexity. But what happens when:

1. Future GPUs have larger INT8 fragment shapes that better match FHE parameters?
2. The security requirements force WordSize > 60 bits (exceeding FP64's 53-bit exact integer range)?
3. You need to support multiple FHE schemes (BFV, BGV) that have different coefficient bit-widths?

The "FP64 is better" conclusion is highly specific to: (a) A100 architecture, (b) CKKS scheme, (c) their chosen 36-48 bit WordSize range, and (d) their specific KLSS parameters. This is a point solution, not a general principle.