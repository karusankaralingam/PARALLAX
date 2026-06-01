# Evaluation Critique: PD Constraint-aware Physical/Logical Topology Co-Design for Network on Wafer

## Q1: Whiteboard Explanation

Let me draw this out for you.

**The Problem:** You have a wafer-scale chip (think: one giant silicon wafer, ~300mm diameter). You need to connect dozens of compute dies together for LLM training. The catch? You have two hard physical constraints:
1. **Area constraint:** Total wafer area ≈ 50,000mm² usable (after peripherals)
2. **Wiring constraint:** Die-to-die links must be <50mm, and you only get 3 metal layers for routing

**The Existing Solutions and Their Failures:**
- **Mesh topology** (used by Cerebras, Dojo): Great integration density (48 dies!), but communication becomes a nightmare—traffic jams at the center, and corner-to-corner takes √N hops. Figure 4 shows this clearly: center links hit 486GB/s while corners barely use bandwidth.
- **FRED (Fat Tree)**: Communication-optimal with small network diameter, but switches eat your wafer. Figure 5 shows compute dies occupy only **25.03%** of wafer area. You've traded compute power for communication speed.

**The Insight:** Neither extreme is optimal. Small-scale mesh actually performs similarly to fully-connected networks. So why not hybrid?

**Mesh-Switch Topology:**
```
[2x2 Mesh Group] ←→ [Central Switch] ←→ [2x2 Mesh Group]
     (local)           (global)              (local)
```

Each 2×2 mesh group handles local communication efficiently. A central switch creates a fully-connected network between groups. You get:
- **40 compute dies** (vs. 20 for FRED, 48 for mesh)
- **Small diameter** for inter-group communication
- **Area balance:** 41,800mm² compute + 5,606mm² switch

**The Logical Topology Co-Design:** Once you pick mesh-switch physically, you need matching communication algorithms. They propose **tree+ring**: tree pattern within mesh groups (aggregation), ring pattern across groups (distribution). This matches the hierarchical physical structure.

**Result:** 2.39× throughput improvement over mesh, 2.11× over FRED for LLM training.

---

## Q2: The Key Insight

The key insight is deceptively simple but frequently overlooked in wafer-scale chip design:

**"Physical topology determines the *ceiling* of achievable performance; logical topology determines how close you get to that ceiling. Optimizing one without the other leaves significant performance on the table."**

This is articulated in Figure 1's "ticktock" framework and explicitly stated in Section 3.1: *"existing studies often adopt orphan designs of physical or logical topologies, lacking the coordination."*

More specifically, the paper recognizes that the wafer-scale design problem is fundamentally a **resource allocation problem under hard physical constraints**, not just a network topology optimization problem. The total area equation (Equation 1):

$$A_{wafer} = A_{comp} + A_{switch} + A_{wire} + A_{others}$$

This captures the zero-sum tradeoff: every mm² devoted to switches is mm² stolen from compute dies. Mesh maximizes $A_{comp}$ at the cost of communication diameter. FRED maximizes communication at the cost of compute. The insight is that a **Pareto-optimal point exists in between**, and you need a DSE to find it.

The second layer of insight is that **small-scale mesh behaves almost identically to fully-connected networks** for collective operations. This is why the 2×2 mesh group size emerges as optimal across all hardware configurations (Figure 11, Figure 12)—you're getting "free" local connectivity without paying the switch area penalty.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Baseline Selection (Physical Topologies)**
The paper does something right that many papers fail at: they explain *why* they exclude certain baselines rather than silently ignoring them. Section 8.1 explicitly states that Dragonfly, Torus, and Flattened Butterfly are excluded because they **violate the 50mm wiring constraint or require >3 metal layers**. Figure 9(b) and 9(c) provide quantitative evidence: dragonfly requires 2389 wires/mm at 6×8 scale, far exceeding manufacturable density. This is honest evaluation methodology.

**2. Multi-Model Workload Diversity**
Table 4 shows five LLM benchmarks spanning:
- Dense models: Llama2 70B, GPT-3 175B, OPT 175B
- MoE models: GShard 137B-MoE, DeepSeekV3 671B
- Parameter range: 70B to 671B
- Architecture diversity: different attention heads (32-128), hidden sizes (4096-12288)

This is substantially better than papers that only show ResNet-50 or a single transformer model.

**3. Sensitivity Analysis Across Hardware Configurations**
Figure 11 tests the DSE across compute die capabilities from 128 TFLOPs to 1024 TFLOPs. This is critical because it shows the 2×2 configuration remains optimal even as future chips become more powerful—the result isn't an artifact of their specific hardware assumptions.

**4. Ablation Study with Clear Attribution**
Figure 24 breaks down the 2.39× improvement into components:
- Physical topology (mesh-switch vs. mesh): 1.38×
- Dual-granularity logical topology: 1.42×
- Parallelism optimization: 1.22×

This multiplicative breakdown (1.38 × 1.42 × 1.22 ≈ 2.39) allows readers to assess which contribution matters most.

### Weaknesses

**1. The "Cherry-Pick" Problem: Parallelism Configuration Selection**
This is the most significant methodological concern. Table 5 shows different parallelism configurations for each topology:
- Mesh: [24,2,4,1] for Llama2 70B
- FRED: [5,4,4,1]
- MS: [20,2,4,1]

The paper claims they "adopt the optimal parallelism configurations" (Section 8.1), but this means **each baseline is allowed its own best-case configuration**. While this sounds fair, it raises questions:
- How exhaustively was the search conducted for each baseline?
- Is FRED truly running at its optimal parallelism, or was more effort invested in optimizing mesh-switch?
- Section 7 describes their parallelism search strategy, but it's unclear if the same strategy was applied to baselines.

**2. Simulator-Only Evaluation**
The entire evaluation uses ASTRA-SIM (Sections 3.2, 5.4, 8.1). While ASTRA-SIM is a respected simulator, there's no silicon validation, no RTL synthesis, no physical layout verification. The paper claims they "performed back-end design" for mesh-switch (Section 5.3), but this is only mentioned for area estimation—no timing closure, power numbers, or actual fabrication.

For a paper making claims about **physical design constraints**, the absence of actual physical design results is notable. The 50mm wiring constraint and 3-layer metal constraint are assumed correct based on prior work [14, 24, 84, 104], not validated.

**3. Communication Microbenchmark vs. End-to-End Training**
Figure 22 shows all-reduce bandwidth for data sizes 1MB-512MB. This microbenchmark is valuable, but:
- Real LLM training involves multiple collective types: all-reduce, all-gather, reduce-scatter, all-to-all
- The paper focuses heavily on all-reduce but MoE workloads (GShard, DeepSeekV3) are dominated by all-to-all for expert routing
- Section 6.2 mentions tree+tree vs. tree+ring, but the evaluation doesn't isolate performance on different collective operations

**4. Scalability Claims Without Multi-Wafer Evidence**
Section 5.5 describes "Multiwafer Cluster Architecture" using switchless dragonfly, but:
- All experimental results (Figures 20, 21, 23) use a 4-wafer cluster maximum
- Figure 15(b) shows DSE for 300,000mm² glass panels, but this is just DSE runtime—not actual performance at that scale
- The claim "excellent scalability" (Section 5.5) is unsupported by experimental evidence beyond 4 wafers

**5. Baseline Fairness for Logical Topology**
The paper compares against TTO [56], TACOS [100], DBT [79], and 2D Ring [95] for collective algorithms (Figure 22). However:
- TTO was designed specifically for mesh topology—applying it to mesh-switch may be unfair
- The 2.58× improvement over 2D Ring is comparing against an algorithm not designed for hierarchical topologies
- A fairer comparison would implement hierarchical versions of these baselines on mesh-switch

**6. Missing Error Bars and Statistical Significance**
None of the bar charts (Figures 6, 7, 11, 12, 20, 21, 23) show error bars or confidence intervals. For simulation-based evaluation, this means:
- No indication of variance across different random seeds (if any stochasticity exists)
- No sensitivity to input data ordering or scheduling decisions
- The claimed 2.39× could be 2.39× ± 0.5× for all we know

---

## Q4: What the Authors Didn't Tell You

**1. The Real Cost of Central Switch Failure**
The mesh-switch topology places all inter-group communication through a **single centralized switch** (Figure 8(c)). Section 5.1 states they position the switch centrally to "address the data exchange bottleneck." But what happens when the switch dies? In a mesh, you lose one die. In mesh-switch, you lose **all inter-group communication**. The paper mentions fault tolerance as an advantage of fat tree (Section 2.3) but never discusses fault tolerance for mesh-switch. For a 40-die wafer, this is a significant reliability concern that's completely unaddressed.

**2. The In-Network Computing Assumption**
Figures 20 and 21 show MS_IC (mesh-switch with in-network computing) substantially outperforming MS. The tree+tree logical topology requires in-network computing switches (Section 6.2: "A switch with in-network computing acts as the root node"). But:
- What's the area overhead of in-network computing capability?
- The switch area in Table 2 (685mm²) doesn't specify whether this includes in-network computing
- FRED_IC vs. FRED comparisons exist, but the paper never quantifies the additional area cost

**3. The Workload Assumptions May Not Hold for Inference**
The entire paper targets LLM **training** workloads. Section 5.4 briefly mentions inference: *"For dynamic applications such as inference, software strategies [85] can fully address the issue."* This hand-waves away a critical concern:
- Training has predictable, repeating communication patterns
- Inference has variable batch sizes, varying sequence lengths, and potentially different collective patterns
- The 2×2 mesh group configuration is optimal for training—is it still optimal for inference?

**4. The HBM Placement Story**
Section 3.2 states "Four HBM3 are integrated near each compute die." Figure 8(a) shows "DRAM" as part of the die. But:
- HBM requires significant interposer area for PHY and TSV
- The 785mm² die area (Table 2) supposedly includes this, but the breakdown isn't provided
- HBM placement affects thermal constraints and routing congestion—neither discussed

**5. What About Power and Thermal?**
The paper is entirely silent on power consumption and thermal constraints. For wafer-scale chips:
- Cerebras WSE-2 consumes ~15kW
- Power density directly constrains how many compute dies can run simultaneously
- The mesh-switch central switch is a potential thermal hotspot

The only mention of power is in Section 2.1 regarding communication: "decreasing communication latency and power consumption." No quantitative analysis exists.

**6. The DSE Search Space is Actually Small**
Section 5.3 claims the DSE is comprehensive, but examine Figure 10: the search iterates through mesh group sizes 1×1 to 6×8, factors each into shapes, and checks port die positions. This is at most ~50-100 configurations. The "15.85ms" DSE time (Figure 15(a)) confirms this is not a computationally intensive search. The paper presents this as a feature ("strong scalability"), but it also suggests the search space is small enough that exhaustive enumeration is trivial.

**7. The Comparison to Real Systems is Indirect**
The paper references Cerebras WSE [62, 63] and Tesla Dojo [91] as existing wafer-scale systems, but never directly compares against them. The mesh baseline is configured as "6×8" (Table 3), while Dojo uses a different die count and configuration. The paper simulates "Dojo D1 chip specifications" (Section 3.2) but doesn't simulate the actual Dojo system architecture—just a mesh of D1-equivalent dies.