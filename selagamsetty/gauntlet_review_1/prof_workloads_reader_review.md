# Methodology Audit: CoopRT Evaluation

Let me dissect this ISCA '25 paper's experimental methodology with the skepticism it deserves.

## 1. Benchmark Selection Analysis

**What they used:** LumiBench suite with 13-15 scenes (out of 16), path tracing at 256×256 resolution, 1 sample-per-pixel.

**The Good:**
- LumiBench is a reasonable choice—it's designed specifically for hardware ray tracing evaluation
- Scene diversity exists: tree sizes range from 0.2MB (wknd) to 1.7GB (robot), depths from 7 to 18

**The Concerning:**
- They couldn't simulate 3 scenes at full resolution (car, robot at 128×128; park timed out entirely). This is a **selection bias red flag**. The largest, most complex scenes—precisely where memory bandwidth saturation matters most—are either downscaled or excluded.
- 256×256 resolution is **laughably small** for modern ray tracing. Real-time applications target 1080p, 1440p, or 4K. At 256×256, you have only 65,536 pixels versus 2+ million at 1080p. The divergence patterns and memory pressure scale non-linearly with resolution.
- 1 sample-per-pixel is minimal. Production path tracers use 64-1024 SPP for noise-free images.

**Quote from paper:** "The highest resolution we could simulate without simulations timing out or running out of memory is 256x256."

This is an honest admission, but it fundamentally limits the generalizability of their results.

---

## 2. The Baseline Validity Check

**Their baseline:** Vulkan-sim modeling RTX 2060 (SM75 configuration), 4-entry warp buffer in RT unit.

**Potential Issues:**
- RTX 2060 is a **2018 architecture** (Turing). We're now on Ada Lovelace (RTX 40 series). NVIDIA has likely already implemented optimizations that address some of these inefficiencies.
- The 4-entry warp buffer seems artificially constrained. They show in Figure 13 that simply increasing to 8 entries gives 1.45× speedup. Is 4 entries representative of real hardware, or is this a strawman?
- They compare against "baseline RT unit" but don't compare against **any prior work** on ray tracing acceleration (e.g., Treelet Prefetching from MICRO '23, Intersection Prediction from MICRO '21).

**Missing comparison:** Section 8 mentions related work but provides **zero quantitative comparison**. They cite Chou et al.'s Treelet Prefetcher but only say "CoopRT can be combined with a prefetcher" without showing actual numbers.

---

## 3. The "Gotcha" Graphs

**Figure 9 - The Y-axis starts at 1.0**, making the baseline look like zero improvement. This is standard practice but always worth noting.

**Figure 13 - The Critical Comparison:**
Look carefully at this figure. With a 32-entry warp buffer (no CoopRT), they achieve geometric mean speedups of 1.64×. With CoopRT on 4-entry buffer, they get 2.15×. But:
- 32-entry buffer + CoopRT gives only 1.99× (actually *worse* than 4-entry + CoopRT)
- This suggests **memory bandwidth saturation**—CoopRT is hitting the ceiling

**Figure 14 - Latency of Slowest Warps:**
This is actually a strong result (0.46× latency reduction), but notice they only show this for the 4-entry configuration. What happens to tail latency with larger buffers?

**Figure 16 - Cache Miss Rates:**
L1 miss rates **increase** with CoopRT (from ~40% to ~60% in several scenes). They spin this as "GPU latency hiding capability tolerating additional L1 misses," but this is concerning for real systems where L1 pressure affects other workloads.

---

## 4. The "Zero-Event" Reality Check

**The core claim:** Idle threads in ray tracing are abundant and can be repurposed.

**Does this actually happen in real workloads?**

Figure 4 shows thread status distribution, but this is for **path tracing only**. For AO and SH shaders (Figure 17), speedups drop to 1.42× and 1.28× respectively. The paper admits:

> "AO and SH rays do not diverge nearly as much as PT, leaving less speedup opportunity for CoopRT than PT rays."

**The problem:** Most real-time games use AO/SH, not full path tracing. Cyberpunk 2077's "Path Tracing Overdrive" mode (cited in their introduction) is the exception, not the rule. The 2.15× geometric mean is for a workload that represents a minority of actual ray tracing usage.

---

## 5. Missing Sensitivity Studies

**What I would have loved to see:**

1. **Resolution scaling:** How does speedup change from 256×256 → 512×512 → 1024×1024? The divergence patterns likely change.

2. **SPP scaling:** With 16 or 64 samples per pixel, does the idle thread ratio change?

3. **Scene complexity correlation:** Is there a predictable relationship between BVH depth/size and CoopRT benefit? They have the data but don't analyze it systematically.

4. **Memory bandwidth sensitivity:** What happens on a bandwidth-constrained mobile GPU? They show one mobile config (Figure 18) with 1.8× speedup, but don't explore the parameter space.

5. **Interaction with other optimizations:** What if you combine CoopRT with Treelet Prefetching? With Dynamic Warp Formation?

---

## 6. Area Overhead Claims

**They claim:** "less than 3.0% of the warp buffer area"

**The fine print:**
- This is 3% of the warp buffer, not 3% of the RT unit, not 3% of the SM
- They use FreePDK45 (45nm process) for synthesis. Modern GPUs are at 4-5nm. Area scaling is non-linear.
- The crossbar for min_thit updates (32×32 in full configuration) is glossed over. They mention subwarp configurations reduce this, but the 32-thread version's crossbar complexity isn't fully characterized.

---

## 7. Energy Claims

**Figure 15 shows EDP improvement of 2.29×**

But look at Figure 9: power increases by 2.02× while speedup is 2.15×. Energy is 0.94× (6% reduction). The EDP improvement comes almost entirely from the delay reduction, not energy efficiency.

For a mobile/embedded context where power is the constraint (not performance), CoopRT's value proposition weakens.

---

## Discussion Question

**If we ran CoopRT on a production workload—say, Unreal Engine 5's Lumen global illumination at 1440p with hybrid ray tracing (not pure path tracing)—do you think the 2.15× speedup would hold?**

Consider:
1. Lumen uses screen-space traces first, falling back to hardware RT only for misses
2. Resolution is 9× higher than their evaluation
3. Ray coherence is higher due to screen-space locality
4. Memory bandwidth is already contested by rasterization workloads

My hypothesis: The speedup would be closer to the AO/SH numbers (1.3-1.4×) than the path tracing numbers (2.15×), because the "idle thread" phenomenon they exploit is most pronounced in pure path tracing scenarios that don't represent typical game workloads.

---

## Summary Verdict

**Strengths:**
- Novel idea with clean algorithmic insight
- Honest about simulator limitations
- Reasonable area overhead analysis

**Weaknesses:**
- Evaluation at toy resolution (256×256)
- No comparison against prior acceleration techniques
- Workload (pure PT) doesn't represent dominant use case (hybrid RT)
- Missing critical sensitivity studies

**The paper proves CoopRT works for path tracing in simulation. It does not prove CoopRT is the right solution for real-world ray tracing acceleration.**