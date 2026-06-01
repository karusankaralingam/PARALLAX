## Q1: Whiteboard Explanation

Let me walk you through what Lumina actually does at the hardware level.

**The Problem They're Solving:**
3D Gaussian Splatting (3DGS) renders scenes by projecting millions of Gaussian "blobs" onto a screen, sorting them by depth, then integrating their colors pixel-by-pixel. On a mobile Volta GPU, this achieves only 5-21 FPS on real-world scenes (Section 2.2, Figure 2b), far below the 90 FPS VR requirement.

**The Pipeline (Figure 1):**
1. **Projection**: Filter Gaussians outside the view frustum, project survivors onto screen tiles
2. **Sorting**: Per-tile radix sort by depth (23% of execution time)
3. **Rasterization**: For each pixel, iterate through sorted Gaussians and accumulate color using: `C(p) = Σ Γᵢαᵢcᵢ` where `Γᵢ = Π(1-αⱼ)` (67% of execution time)

**The Two-Pronged Attack:**

**Optimization 1: Sorting-Shared (S²) Algorithm (Section 3.1)**
- Predict future camera pose using velocity extrapolation: `Sₖ = Tₖ + v × (N/2)Δt`
- Pre-sort Gaussians at predicted pose with an *expanded viewport* (extra tiles around edges)
- Reuse that sorting result for N consecutive frames (sharing window = 6 by default)
- Only recompute Spherical Harmonic colors per frame (view-dependent)
- This hides Sorting latency entirely through speculative execution

**Optimization 2: Radiance Caching (RC) (Section 3.2)**
- Key insight: Two rays hitting the same sequence of "significant" Gaussians (α > 1/255) yield nearly identical pixel values
- Cache tag = concatenation of first k significant Gaussian IDs (k=5 default)
- Cache value = RGB color
- During rasterization: compute first ~5 significant Gaussians, query cache, if hit → skip remaining color integration
- Result: 55% computation avoided in color integration

**The Hardware: LuminCore (Section 4, Figure 17)**
- 8×8 Neural Rendering Units (NRUs), each with 4 Processing Elements (PEs)
- **Frontend-Backend Split**: PEs compute transparency (all Gaussians), Backend handles color integration (only significant Gaussians via FIFO)
- **LuminCache**: 4-way set-associative, 52KB, indexed by lower bits of Gaussian IDs, tag = upper bits concatenated
- **Sparsity-Aware Remapping**: When cache hits leave PEs idle, reconfigure all PEs within an NRU to collaborate on single cache-miss pixels

---

## Q2: The Key Insight

**The Magic Trick:** The paper exploits one elegant geometric fact—if two rays intersect the *same sequence of small Gaussians*, they must have nearly identical directions and will produce nearly identical colors. This reduces 3DGS's expensive color integration (iterating 1000+ Gaussians per pixel) to a simple cache lookup after examining only ~5 Gaussians.

**Why This Works (and why it's specific to 3DGS):**

The authors characterize in Figure 4 and Figure 11 that despite pixels iterating over ~1000-2000 Gaussians, only **10.3%** are "significant" (α > 1/255), and of those, **over 99% of pixel color comes from just 1.5% of Gaussians**. This extreme sparsity means:
1. The first few significant Gaussians almost uniquely identify a ray's trajectory
2. The remaining Gaussians contribute negligibly to final color

Figure 12 validates this: pixels sharing the same first 5 significant Gaussians differ by <0.5 RGB units (out of 255).

**The Structural Delta from Baseline:**

Standard 3DGS: Every pixel iterates all tile Gaussians → check α → conditionally integrate → repeat until transmittance threshold

Lumina: Every pixel iterates until finding k significant Gaussians → hash their IDs → cache lookup → if hit, terminate immediately with cached value

This transforms an O(N) sequential dependency into O(k) where k<<N, but *only* for cache hits (~55% of pixels).

**The Hardware Enabler:**

The frontend-backend NRU split (Figure 17) is the real hardware insight. GPUs suffer warp divergence because threads processing different pixels integrate different Gaussian counts. By *decoupling* the "filter significant Gaussians" phase (frontend, all PEs parallel) from "integrate color" phase (backend, sparse, shared), they achieve full utilization despite sparsity. The shift registers between frontend and backend act as a load-balancing FIFO.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Honest Baseline Measurement (Section 5):** They directly measure on real mobile Volta GPU (Xavier SoC) including kernel launch times, not just theoretical peak throughput. Power metrics come from built-in measurement, not estimation.

2. **User Study with IRB Approval (Section 5, Figure 19):** 30 participants, 2IFC procedure, randomized order—73% noticed no difference, and among those who did, it was a 50-50 split. This is proper perceptual validation, not just PSNR hand-waving.

3. **Ablation Discipline (Section 6.2, Figure 22):** They carefully separate S²-GPU, RC-GPU, NRU+GPU, S²-Acc, RC-Acc, and full Lumina. This reveals that **RC-GPU actually slows things down** (1.2× slower) due to warp divergence overhead, proving the accelerator is necessary—not just a speedup multiplier.

4. **Real Hardware Synthesis (Section 5):** RTL → Synopsys synthesis → Cadence P&R → TSMC 16nm → DeepScaleTool to 12nm. SRAM from Arm Artisan compiler with PrimeTime-PX power. This is not HLS or analytical modeling.

5. **Comparison Against GSCore (Section 6.4, Figure 25):** They incorporate GSCore's CCU and GSU units and still show 9.2× advantage for their baseline NRU design alone, demonstrating the frontend-backend split outperforms prior work's architecture fundamentally.

### Weaknesses

1. **Dataset Limitations (Section 5):** They explicitly state they *cannot* evaluate on MipNeRF360 and DeepBlending—the two most challenging datasets in the original 3DGS paper—because those contain individual images, not continuous video sequences. This is honest but undermines claims of generality.

2. **Trajectory Prediction Assumed (Section 3.1, Equation 2-3):** They use simple linear velocity prediction `v = (Fⱼ - Fⱼ₋₁)/Δt` borrowed from Cicero [25]. They acknowledge this but don't evaluate prediction accuracy or failure modes. Rapid head rotation would break S²—they mention IMU-based disabling (Section 8) but don't quantify the frequency.

3. **Cache Replacement Overhead Hidden:** Section 4 mentions the cache is shared across 2×2 tile groups and requires "saving current cache data to memory, flushing the entire cache, and loading data related to the new batch." They claim double-buffering hides this, but no breakdown of memory bandwidth consumption is provided.

4. **Real-World FPS Still Borderline (Section 6.2):** On T&T real-world scenes, Lumina achieves 97.9 FPS average—barely above the 90 FPS target with no margin for variance. The baseline was 30 FPS video, not 90 FPS VR trajectories.

5. **Scale-Constrained Loss Tradeoff (Section 3.3, Figure 21b):** Adding L_scale improves quality but *decreases* cache hit rate. They don't quantify the retraining cost or whether this works for pre-trained models (fine-tuning required).

6. **Area Claim Questionable:** They claim 1.05mm² is "negligible" compared to 350mm² Xavier SoC (Section 5), but Xavier includes CPU cores, memory controllers, etc. Comparing to GPU die area alone would be more appropriate.

---

## Q4: What the Authors Didn't Tell You

### The Hidden Hardware Tax

1. **176KB Feature Buffer + 52KB LuminCache + 6KB Output Buffer (all double-buffered):** That's ~468KB of SRAM dedicated to this accelerator. They don't discuss the power cost of keeping this SRAM active. The 25:1 DRAM:SRAM energy ratio they cite (Section 5) cuts both ways—SRAM still costs energy.

2. **Cache Coherence with GPU is Handwaved:** Section 4 states "there is no direct interaction between LuminCore and the GPU. LuminCore only reads data from DRAM through DMA." This means *every frame*, the GPU must write sorted Gaussian lists to DRAM, then LuminCore reads them back. The DRAM round-trip for potentially millions of Gaussians per frame isn't accounted for in their "double buffering hides memory latency" claim.

3. **The 10-byte Cache Tag:** Each cache entry uses 10 bytes for tags (5 Gaussian IDs × 16 bits from positions 3-18). With 4×1024 entries, that's 40KB just for tags in a 52KB cache. The storage efficiency is poor.

### What "4.5× Speedup" Really Means

Looking at Figure 22a carefully:
- S²-GPU alone: 1.2× speedup (software-only, skipping sorts)
- NRU+GPU (no algorithms, just hardware): 1.9× speedup
- S²-Acc: 3.1× speedup
- Lumina (everything): 4.5× speedup

This means the *algorithms* (S² + RC) contribute ~2.4× speedup, and the *architecture* (NRU frontend-backend + LuminCache) contributes ~1.9×. The accelerator alone, without their algorithmic contributions, only provides 1.9×.

### The "55% Computation Avoided" Asterisk

Section 3.2 claims RC avoids 55% of color integration computation. But Figure 21b shows cache hit rates of 50-70% depending on scene. More critically, a cache hit still requires:
1. Computing transparency for initial Gaussians until finding k=5 significant ones
2. LuminCache lookup (4-way associative comparison)
3. Potential cache miss handling

The 55% is for *color integration* specifically, not total Rasterization computation.

### Scale-Constrained Training Creates a Catch-22

Section 3.3 introduces L_scale to penalize large Gaussians that break the "sufficiently small Gaussian" assumption of radiance caching. But Figure 13 shows these large Gaussians exist in real trained models. This means:
- Pre-trained 3DGS models need fine-tuning to work with Lumina
- The fine-tuning reduces cache hit rate (Figure 21b: ~10% drop)
- They don't report fine-tuning time or whether quality matches original training

### The Warp Divergence Claim Needs Context

Section 2.2 claims "threads remain masked over 69% of the time" due to sparse color integration. But this is *inherent to 3DGS's algorithm*—even prior work like GSCore faces this. The authors frame their frontend-backend split as novel, but the real comparison should be against GSCore's approach to the same problem, not against raw GPU. Section 6.4 finally does this, but the 9.6× vs 3.2× comparison uses different baselines than the main 4.5× claim.