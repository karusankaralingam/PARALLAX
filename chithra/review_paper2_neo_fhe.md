# Title: Neo — Accelerating FHE on GPU Tensor Cores

1. Whiteboard explanation — You're explaining this paper's mechanism to a smart colleague who hasn't read it. Walk through what they built and how it works.
FHE is a cryptographic algorithm that allows to perform computations on encrypted data without sacrificing privacy. Current implementations of FHE are prohibitively expensive on real world workloads, hence there is a growing need for optimized hardware implementations. Since the algorithm is rapidly evolving, ASIC implementation is not the best solution yet. GPGPU solutions are preferred due to the parallelism and flexibility offered. 
The paper profiles the memory access patterns of the KeySwitch operation in FHE to characterize the poor data reuse between the different operations. The paper proposes transforming the element wise multiplication within FHE operations to matrix multiplication and using the FP64 components within the TCU blocks of the GPGPUs instead of just relying on the CUDA cores which can be inefficient. Mapping matrix multiplication to FP64 has the advantages of representing the large polynomial coefficients inherent to FHE algorithms better with less memory accesses compared to INT8 components, and by making it easier to increase the Wordsize of the KeySwitching algorithm which reduces the algorithmic complexity.  
The paper achieves reducing algorithmic complexity and increasing data reuse within FHE operations to improve the performance of the CKKS scheme compared to the previous implementation on TCU by 3 times.  

2. What is the key insight that makes it work? (The "aha" — not what they did, but why it works)
FHE operations can be rearranged as dense matrix operations that fit with the strongest execution paths with the most data reuse within GPGPUs. FP64 components within the TCU helps accelerate FHE operations better compared to the INT8 components. 

3. What's the strongest aspect of the evaluation, and what's the weakest? (Methodology critique)
Strengths:
The paper does extensive profiling and experiments in the results section to show the improvement through the multiple optimizations they did.
It balances algorithmic complexity of the KLSS method with hardware implementation complexity through enough experimentation.
The optimizations done in the paper are with a very good understanding of the GPGPU architecture and is about data layout optimizations to make the bets use of the hardware.

Weaknesses:
The clarity of the paper is less as it introduces a lot of FHE terms and is difficult to follow easily for someone with only an architecture background. It requires good amount of background reading on FHE to understand the paper.
Performance per watt is an important metric especially for ISCA and is not evaluated in this paper. There is more utilization of TCUs compared to TensorFHE and the results section feels incomplete without the power analysis. 
There are several typos within the paper, especially in Table 3, relative work and the conclusion sections.

4. What did the authors not tell you? (Hidden assumptions, missing comparisons, unstated limitations)

The paper does not characterize error propagation, given how KeySwitch is reported to increase noise and how important it is to keep error propagation in check so as to recover back the plain text in FHE.
Performance data comparison with other ASIC and FPGA accelerators missing.

5. What's the connection to ideas outside this paper's scope? (Cross-domain links, broader implications)
Neo at heart emphasizes transforming complex, FHE computations into dense matrix multiplication so it can run on highly optimized existing hardwares that are meant for GEMM. The paper uses hardware that already exist in datacenters for AI workloads, to accelerate FHE. So the adoption of this method can be very easy.

