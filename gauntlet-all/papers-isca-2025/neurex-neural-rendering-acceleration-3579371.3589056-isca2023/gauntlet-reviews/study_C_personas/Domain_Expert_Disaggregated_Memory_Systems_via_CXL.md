## Q1: Whiteboard Explanation

Let me walk you through what NeuRex actually does, stripping away the marketing language.

**The Problem They're Solving:**

Neural rendering (specifically NeRF and its successors) works by querying a neural network millions of times to render a single image. The original NeRF used a big MLP and took forever. The state-of-the-art approach (Instant-NGP) made a clever trade: shrink the MLP dramatically, but add 16 learnable hash tables that encode spatial features at different resolutions. The MLP becomes tiny (~3 layers), but now you're doing hash table lookups constantly.

Here's the catch: those hash tables are 2MB each (16 tables × 2MB = 32MB total), and you need to do 8 lookups per sample point per resolution level. For each lookup, you only grab 4 bytes, but you're thrashing through these tables with seemingly random access patterns because—well, that's what good hash functions do.

**What Goes Wrong on GPUs:**

On a high-end GPU (RTX 3070 with its 4MB L2 cache), you can fit one hash table at a time, so you process all sample points level-by-level. It works, but hash encoding still eats 40%+ of rendering time (Figure 6).

On an edge GPU (Jetson Xavier NX with 256KB L2 cache), you're completely hosed. A single hash table doesn't fit. Every lookup potentially goes to DRAM. You're burning bandwidth fetching 64-byte cache lines to use 4 bytes.

**The NeuRex Trick (Restricted Hashing):**

The core algorithmic insight is simple but effective: instead of letting the hash function scatter accesses across the entire table, partition the 3D scene into subgrids. Each subgrid only accesses a *portion* of the hash table (a "subtable"). 

So if you divide the scene into 64 subgrids (4×4×4), each subtable is 1/64th of the full table. Now you can:
1. Load just that subtable into on-chip memory
2. Process all sample points in that subgrid
3. Move to the next subgrid

This converts random global access into sequential streaming of subtables + local random access within a small buffer.

**The Pipelining Win:**

The original GPU flow serializes hash encoding (ENC) and MLP computation. You can't start MLP until you've built the full feature matrix from all 16 hash tables. 

With restricted hashing, NeuRex processes batches independently. While batch N is going through the MLP, batch N+1 is doing hash lookups. The hardware overlaps these operations because they use different resources (Figure 8).

**The Hardware:**

NeuRex adds an "Encoding Engine" to a standard TPU-like systolic array. The key components:
- **Grid Cache (64KB):** For coarse resolution levels where accesses are highly localized (many samples share the same voxel vertices), they coalesce 8 vertex features into one cache line.
- **Subgrid Buffer (128KB, double-buffered):** For fine levels, holds one subtable at a time. Stream subtables from DRAM while processing.
- **Index Generation Unit:** Computes hash indices and interpolation weights in parallel.
- **Interpolation Compute Unit:** Weighted sum of 8 vertex features.

---

## Q2: The Key Insight

**The Real Contribution:**

The paper's genuine innovation is the **observation that hash table access patterns differ dramatically across resolution levels**, and the **restricted hashing algorithm** that exploits this to achieve performance portability.

Specifically:

1. **Coarse levels (L=0,1,2...):** Accesses are highly localized. Many sample points fall in the same voxel, so they share vertex lookups. Figure 7(a) shows Level 1 has hot spots with 8000+ accesses to certain entries while most entries are untouched.

2. **Fine levels (L=13,14,15):** Accesses are evenly distributed—essentially random. Figure 7(b) shows Level 13 with ~30-40 accesses per entry, spread across the table.

**Why This Matters:**

The authors realized you need *different caching strategies* for these two regimes:
- For coarse levels: Use a small, specialized cache (Grid Cache) that coalesces the 8 vertices of a voxel into a single entry—this is clever because NeRF *always* needs all 8 vertices for trilinear interpolation.
- For fine levels: Don't cache at all—instead, *restrict* which entries can be accessed by partitioning the scene, then stream subtables.

The restricted hashing is the enabler. By constraining hash accesses to a subtable, they:
1. Eliminate the need for multi-MB on-chip caches
2. Convert unpredictable random access into predictable sequential streaming
3. Enable pipelining between ENC and MLP (because batches are now independent)

**What's NOT the contribution:**

The systolic array for MLP is completely standard (Section 4.7 admits it's "similar to conventional DNN accelerators"). The pipelining concept is also standard—the novelty is that restricted hashing *enables* it by making batches independent.

The insight that "hash encodings take 40% of time and are serialized with MLP" (Section 3.3-3.4) is the observation; restricted hashing is the solution.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive workload coverage:** Table 3 shows they evaluated NeRF on 5 diverse scenes (synthetic and real-world) at varying resolutions (576p to 1080p), plus SDF and image approximation tasks (Section 6.6, Figure 21). This isn't cherry-picking one favorable benchmark.

2. **Honest quality analysis:** Figure 15 shows PSNR drops of 0.7-3.9% with restricted hashing at default table size. They don't hide this—and importantly, Figure 16 shows visual comparisons where the drop is imperceptible. They also show you can recover quality by using 4× larger tables (Ours-LT) since their scheme makes table size less impactful on performance.

3. **Fair ablation study:** Figure 17 isolates contributions from Grid Cache (GC) and Restricted Hashing (RH) separately. They show what each component adds rather than presenting only the final combined result.

4. **Appropriate baseline selection:** They compare against Xavier NX (edge) and RTX 3070 (consumer high-end)—the actual target deployment platforms. They acknowledge the technology node difference (28nm vs 8nm/12nm) and appropriately suggest comparing energy efficiency rather than raw speedup for fairness.

5. **GPU optimization attempt:** Figure 20 shows they actually tried implementing restricted hashing + pipelining on GPUs. It doesn't work well due to CUDA scheduling limitations (Section 6.6)—this strengthens the case for custom hardware rather than just comparing against a naive GPU baseline.

**Weaknesses:**

1. **Simulation-based evaluation:** The accelerator is evaluated via RTL synthesis + cycle-level simulation (Section 5). They never built real silicon or even an FPGA prototype. The DRAM timing comes from Ramulator. While this is standard practice, real memory system behavior (especially under the streaming access patterns of restricted hashing) might differ.

2. **Technology node handicap cuts both ways:** They claim NeuRex at 28nm achieves 2.88-9.17× speedup over GPUs at 8nm/12nm (Section 6.1). But then say "it would become even more attractive if fabricated with more advanced technology" (Section 6.5). This is hand-waving—they should have provided a normalized comparison or projected numbers.

3. **Single hash table configuration:** All evaluations use 16 levels, 2^19 entries per level, 2-byte features (Table 2). What happens if future models use different configurations? The Grid Cache hit rates and subgrid buffer sizing are tuned to these specific parameters. Section 6.4 sensitivity study only varies batch size and grid cache size—not the fundamental hash table parameters.

4. **No training evaluation:** The entire paper focuses on inference/rendering. Section 3.2 mentions "training" but all experiments are inference-only. Instant-NGP's claim to fame includes fast *training*—can NeuRex accelerate training too? The backpropagation through hash tables is never discussed.

5. **Questionable pipelining overhead model:** Section 6.6 (RH+PP discussion) says GPU pipelining fails due to "synchronization overheads." But NeuRex must also synchronize between ENC and MLP stages—they don't quantify this overhead for their hardware. The double-buffered design helps, but what's the actual pipeline stall rate?

6. **Limited scalability analysis:** Both designs (Edge/Server) are evaluated independently. What about a multi-chip or multi-accelerator scenario for higher resolutions or VR/AR frame rates? No discussion of how NeuRex would scale.

---

## Q4: What the Authors Didn't Tell You

**1. The Quality Impact is Scene-Dependent and Poorly Characterized:**

Figure 15 shows PSNR across 5 scenes, but the variance is large (0.7% to 3.9% drop). The authors never explain *why* some scenes suffer more. My hypothesis: restricted hashing reduces effective hash table capacity because entries are now dedicated to specific spatial regions. Scenes with uneven spatial complexity (e.g., detailed center, sparse edges) might have subgrids where the subtable is too small and suffers more collisions. The "Ours-LT" (4× larger table) recovery suggests this—but they don't analyze which subgrids are under-provisioned.

**2. The 64-Subgrid Choice is Never Justified:**

Footnote 6 (Section 6.1) casually states "We use 64 subgrids for restricted hashing in our evaluation." Why 64? How does this interact with scene complexity, image resolution, or the 16 hash table levels? This is a critical hyperparameter that affects both quality (finer subgrids = smaller subtables = more hash collisions) and performance (finer subgrids = more subtable loads = more DRAM traffic). No sensitivity analysis is provided.

**3. The Grid Cache Design Assumes 8 Vertices Per Lookup:**

Section 4.5 describes coalescing 8 vertices per cache line. This is perfect for NeRF's trilinear interpolation. But other applications (SDF, image approximation) mentioned in Section 6.6 might have different interpolation schemes. The paper claims general applicability but the hardware is clearly specialized for 8-vertex lookups. If a future model uses higher-order interpolation (e.g., 27 vertices for tricubic), the Grid Cache design breaks.

**4. Memory Bandwidth Requirements are Understated:**

With restricted hashing, you must stream subtables from DRAM. For 64 subgrids, 16 levels, and 128KB subtables (though only 32KB is used—Section 5), that's 64 × 16 × 32KB = 32MB per frame if all subgrids are visited. For 60fps, that's 1.92GB/s just for subtable loading—on top of the position data streaming. NeuRex-Edge uses LPDDR4-3200 (25.6GB/s), so this is ~7.5% of peak bandwidth. Seems fine, but they never characterize actual bandwidth utilization or show what happens when you're DRAM-bound.

**5. The TCE Utilization Story is Incomplete:**

Section 6.1 claims "TCE achieves higher compute utilization" than GPU tensor cores. But they never provide utilization numbers. The MLP is tiny (32→64→16 and 32→64→64→3 per Section 3.2/Figure 5). Even with fusion, the systolic array (32×32 = 1024 MACs) must be poorly utilized for these skinny matrix multiplications. A 32×64 matmul on a 32×32 array is only 50% efficient at best. What's the actual utilization?

**6. No Discussion of Precision:**

The entire paper assumes FP16 (2-byte features per Table 2). But recent work on neural rendering explores INT8 or even lower precision for inference. How would NeuRex's encoding engine handle quantization? The IGU does floating-point coordinate scaling—could this be simplified? The systolic array implications aren't discussed either.

**7. The "Long-Term Viability" Argument is Speculative:**

Section 6.6 claims hash encodings are like "positional encoding and word embedding used in Transformer-based models" and will be "widely adopted." This is a stretch. Transformer embeddings are learned lookup tables with *deterministic* indexing (token ID → embedding). Hash encodings have *stochastic* indexing with intentional collisions. They solve different problems. The analogy to justify long-term hardware investment is weak.

**8. Comparison to Software Optimization Alternatives is Missing:**

What if you just used a better cache replacement policy on GPUs? Or prefetched subtables in software? The CUDA ecosystem has explicit cache management (`cudaMemPrefetchAsync`, texture memory). The authors tried restricted hashing on GPU (Figure 20) but didn't try other software optimizations that might close the gap without custom silicon.