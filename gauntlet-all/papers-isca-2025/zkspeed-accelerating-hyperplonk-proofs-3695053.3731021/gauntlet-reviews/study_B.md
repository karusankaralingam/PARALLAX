# Study B — Rich Directive
**Paper:** 3695053.3731021  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-06-01 09:32

---

Q1: Whiteboard Explanation

Let me walk you through zkSpeed, an ASIC accelerator for HyperPlonk zero-knowledge proofs.

**The Problem Context:**
Zero-knowledge proofs (ZKPs) let a prover convince a verifier that a computation was done correctly without revealing the inputs. HyperPlonk is a recent ZKP protocol with desirable properties: universal trusted setup (run once, reuse forever), small proofs (~5KB), and O(n) prover complexity instead of O(n log n). But proving is still painfully slow—minutes to hours.

**Why HyperPlonk is Hard to Accelerate:**
Three challenges: (1) Massive bitwidths—255-bit and 381-bit modular arithmetic throughout. (2) Large polynomials—up to 2^24 degree, stored as MLE tables. (3) Heterogeneous kernels—some compute-bound (MSM), some memory-bound (SumCheck), with different dataflows and reuse patterns.

**The Protocol Structure:**
HyperPlonk has four main phases:
1. **Witness Commits**: Sparse MSMs to commit to witness polynomials
2. **Gate Identity**: SumCheck (ZeroCheck) to verify each gate computes correctly
3. **Wiring Identity**: SumCheck (PermCheck) plus MSMs to verify gate outputs route correctly to downstream inputs. Requires computing fraction MLE φ = N/D involving modular inversion.
4. **Polynomial Opening**: Final SumCheck (OpenCheck) plus a cascade of shrinking MSMs

**zkSpeed Architecture:**
Eight specialized units connected via a shared multi-channel bus with global SRAM for MLE storage:

- **SumCheck Unit**: Handles three SumCheck variants. Key insight: polynomials repeat across terms (e.g., f_z1 appears in every term of ZeroCheck). Instead of recomputing, evaluate once and reuse. Uses streaming approach—MLE tables stream from HBM, get updated, write back. Memory-bound.

- **MSM Unit**: Based on SZKP's Pippenger design with two improvements—reduced memory footprint (exploit Z=1 initially), faster bucket aggregation (parallel groups instead of serial).

- **Multifunction Tree Unit**: Hybrid DFS/BFS traversal for tree computations (Build MLE, MLE Evaluate, Product MLE). DFS at top levels for memory efficiency, BFS at bottom for parallelism. Reuses hardware across multiple functions.

- **FracMLE Unit**: Novel batched modular inversion using Montgomery batching. Batch size 64 optimal—amortizes expensive 509-cycle inversion across 64 elements using multiplier trees.

**Key Design Decisions:**
- Streaming SumCheck: Can't store all intermediate MLEs on-chip (grows 100x after round 1), so stream from HBM. Makes SumCheck bandwidth-bound.
- MLE Compression: Input MLEs are sparse (controls are binary, witnesses 90% 0/1). Compress to save 10-11x storage.
- Resource sharing: Unified SumCheck PE handles all three variants (48.9% area savings). MLE Combine shares multipliers (41% savings).

**Performance:**
At 366mm² with 2TB/s HBM3 bandwidth, achieves 801x geomean speedup over CPU. MSMs are compute-bound (scale with PEs), SumChecks are memory-bound (scale with bandwidth).

---

Q2: The Key Insight

The central insight is that HyperPlonk's heterogeneous kernel mix—compute-bound MSMs and memory-bound SumChecks with vastly different characteristics—requires a **streaming architecture with rate-matched units rather than a monolithic accelerator**.

The critical technical observation is that SumCheck's intermediate MLE tables cannot fit on-chip (expanding 100x in data width from round 1 to round 2), forcing a streaming approach. But this streaming approach creates a memory-bound kernel that benefits dramatically from HBM3-scale bandwidth. Meanwhile, MSMs remain compute-bound. This bandwidth/compute duality across kernels is the fundamental constraint shaping the architecture.

**Why prior approaches don't work:** NoCap uses a vector architecture for Spartan's SumChecks, but HyperPlonk's SumChecks have more terms of varying degrees with repeated polynomials across terms—the communication patterns wouldn't map efficiently to a Beneš network. SZKP accelerates Groth16 but relies on NTT+MSM, not SumCheck+MSM.

**The key enabler:** The streaming SumCheck PEs that (1) compute all evaluations for each polynomial once before reusing across terms (eliminating redundant computation from the CPU baseline), and (2) can be rate-matched with upstream/downstream units to maintain throughput while hiding HBM latency. Combined with the hybrid DFS/BFS tree traversal that eliminates massive intermediate storage needs, this enables fully-pipelined execution across protocol phases.

This insight—that the protocol's data access patterns and kernel characteristics necessitate streaming with careful rate-matching rather than the batch processing typical of prior accelerators—is the paper's distinctive architectural contribution.

---

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive design space exploration**: The Pareto frontier analysis across 7 bandwidth levels and thousands of configurations is thorough. Figure 9 clearly shows that HBM3-scale bandwidth (>1 TB/s) is necessary for high-performance designs beyond 300mm². This is concrete, actionable guidance.

2. **End-to-end protocol coverage**: Unlike many ZKP accelerator papers that cherry-pick kernels, zkSpeed implements all protocol phases. Table 1's profiling of 12 functions with arithmetic intensity gives a complete picture.

3. **Real workload validation**: Table 3 shows speedups on actual ZKP applications (Zcash, Zexe, etc.) rather than only synthetic benchmarks. The 720-862x speedup range with consistent scaling is credible.

4. **Honest comparison with alternatives**: Table 4 acknowledges that SZKP+ (optimized Groth16) achieves 6x better proving time but requires circuit-specific setup. NoCap has 10x less area but 1000x larger proofs. The paper correctly frames these as different points in a design space, not strictly inferior.

5. **Detailed area/power breakdown**: Table 5 provides full accounting. The 0.46 W/mm² power density being comparable to CPU validates thermal feasibility.

**Weaknesses:**

1. **Synthetic benchmark reliance**: Section 6.2 states "there is no publicly available compiler to generate real workloads" for HyperPlonk. The synthetic workloads assume 10% dense scalars—but this is acknowledged as a pessimistic upper bound. The sensitivity to actual workload distributions is unclear.

2. **No RTL synthesis of full chip**: The paper uses HLS for units, analytical models for SumCheck, and cycle-accurate simulation for MSM. There's no evidence of full-chip RTL integration. The 1 GHz clock target at 7nm is plausible but unvalidated.

3. **HBM PHY area accounting is inconsistent**: Figure 9 includes PHY costs, but Section 7.3 excludes them for CPU comparison "since the AMD EPYC processor has its own separate die for I/O." This is a weak justification—zkSpeed requires HBM; the CPU baseline doesn't.

4. **Limited sensitivity analysis on sparsity**: The Sparse MSM performance depends heavily on the 90% sparse / 10% dense assumption. What happens at 80% sparse? 95%? This affects witness commit speedups directly.

5. **SumCheck utilization gap unexplained**: Figure 13 shows SumCheck unit at only 15.26% area utilization. Given SumCheck is memory-bound and the paper argues for streaming, why provision so much SumCheck area? The area allocation seems suboptimal.

6. **Verifier time increase ignored**: Table 4 shows HyperPlonk's verifier is 26ms vs Groth16's 4.2ms—a 6x slowdown. For consensus systems with many verifiers, this aggregate cost could offset prover speedups. Not discussed.

---

Q4: What the Authors Didn't Tell You

**Implementation Gaps:**
The paper relies heavily on HLS-generated RTL and analytical modeling. There's no full-chip physical design, no actual HBM integration, and no power validation beyond estimates. The critical path analysis mentions "381-bit PADD unit at 1.05ns" but this is pre-scaling; the 1 GHz at 7nm claim uses generic scaling factors. Real HBM3 PHY integration at 2 TB/s would be a significant engineering challenge that's glossed over.

**The Jellyfish Elephant:**
Section 8 briefly mentions Jellyfish, a HyperPlonk variant with higher-arity gates that would reduce MLE table sizes. This is a significant omission—if Jellyfish becomes the preferred variant (and high-degree constraints are useful for many applications), zkSpeed's MLE storage assumptions and SumCheck datapath would need redesign. The paper waves this off with "we leave this for future work."

**Protocol Evolution Risk:**
The ZKP space evolves rapidly. The paper acknowledges this in Section 5 but understates the risk. HyperPlonk's adoption is mentioned (industry implementations, academic research) but no concrete deployment numbers. If the ecosystem shifts to folding schemes (Nova, Protostar—cited but not discussed) or different polynomial commitment schemes, zkSpeed's specialization becomes a liability.

**The 801x Number:**
This geomean speedup is against a single-threaded CPU baseline (32-core AMD EPYC 7502). HyperPlonk's CPU implementation is parallelizable—SumCheck iterations can run across cores, MSMs can be parallelized. A multi-threaded baseline would reduce speedups by roughly 10-30x depending on parallelization efficiency. The paper doesn't provide this comparison.

**Memory Bandwidth Assumptions:**
The 2 TB/s HBM3 bandwidth is achievable per JEDEC specs, but sustained bandwidth is typically 60-70% of peak. The paper appears to use peak bandwidth in modeling. At 1.2-1.4 TB/s sustained, the Pareto analysis shifts, and some "optimal" design points become bandwidth-constrained.

**Compression Tradeoffs:**
Section 4.6 claims 10-11x compression for MLE storage, but doesn't detail the decompression overhead. Address translation units are mentioned but not characterized for area or latency. If decompression adds cycles to the critical path, the bandwidth savings may be partially offset.

**What Would Break This:**
If a competing protocol achieves HyperPlonk's properties (universal setup, small proofs) without requiring modular inversion or with simpler SumCheck structures, zkSpeed's FracMLE and specialized SumCheck units become wasted silicon. The paper doesn't analyze which architectural components are protocol-general versus HyperPlonk-specific.