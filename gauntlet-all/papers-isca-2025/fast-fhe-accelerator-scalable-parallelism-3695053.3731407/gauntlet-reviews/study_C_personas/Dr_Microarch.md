## Q1: Whiteboard Explanation

Let me draw this out for you. FAST is tackling the key-switching bottleneck in FHE accelerators, which accounts for ~80% of execution time in bootstrapping (Section 3.1).

**The Core Problem:**
FHE ciphertexts have a "level" ℓ that drops as you do multiplications. There are two key-switching methods:
1. **Hybrid method:** Uses 36-bit word lengths, decomposes limbs into β groups of α limbs each, does ModUp → KeyMult → ModDown (Figure 1a)
2. **KLSS method:** Uses 60-bit word lengths, reorganizes limbs differently, does Double Decomp → KeyMult → Recover Limbs → ModDown (Figure 1b)

**The Key Observation (Figure 2):**
Neither method wins everywhere. Looking at the "Quantitative Line" in Figure 2(a):
- At ℓ = 5-12: Hybrid is 23.5% better
- At ℓ = 25-35: KLSS is 15.2% better

Why? Figure 2(b) shows the breakdown. At low levels, KLSS doesn't reduce NTT operations enough to offset its KeyMult overhead. At high levels, KLSS's NTT reduction dominates.

**The Hardware Challenge (Figure 4):**
A 60-bit multiplier costs 2.9× the area and 2.8× the power of a 36-bit multiplier. Prior accelerators picked one word length and stuck with it—either wasting silicon when precision isn't needed (60-bit for hybrid) or losing parallelism when it is needed (36-bit for KLSS).

**FAST's Solution:**
1. **Aether/Hemera framework** (Figure 5): Offline tool analyzes which key-switching method to use at each level, considering computation cost AND evaluation key transfer time. Generates a ~1KB config file.

2. **Tunable-Bit Multiplier (TBM)** (Figure 6): Three 36-bit base multipliers that can either:
   - Process two 36-bit multiplications in parallel (for hybrid), OR
   - Process one 60-bit multiplication using Booth-like decomposition (for KLSS)

The TBM uses the formula: A₆₀ × B₆₀ = (a₀ + a₁x) × (b₀ + b₁x), computed via three 36-bit products and clever recombination (Section 4.2).

---

## Q2: The Key Insight

**The "Magic Trick":** The TBM's Booth-variant decomposition that achieves 60-bit multiplication with only 3 (not 4) 36-bit multiplier invocations.

Standard Booth decomposition of 60-bit × 60-bit into 36-bit multipliers requires 4 multiplications. FAST exploits the algebraic identity shown in Figure 6's formula box:

```
A₆₀ × B₆₀ = (a₀x² + a₁) × (b₀x + b₁)
          = p₀x² + ((a₀+a₁)(b₀+b₁) - p₀ - p₁)x + p₁
```

Where p₀ = a₀×b₀, p₁ = a₁×b₁, and the middle term uses the Karatsuba-like trick: (a₀+a₁)(b₀+b₁) - p₀ - p₁.

This is the classic Karatsuba multiplication trick applied to hardware. Three multipliers (M-A, M-B, M-C) compute the three products simultaneously, and three combiners (C-A, C-B, C-C) aggregate the partial products (Figure 6).

**Why this matters architecturally:**
- When running hybrid key-switching (36-bit): M-A and M-B each process independent multiplications, achieving 2× parallelism
- When running KLSS (60-bit): All three multipliers collaborate on one 60-bit product

The "shared path" (red lines in Figure 6) handles 36-bit mode; the "additional path for 60-bit" (blue lines) activates for KLSS. The paper claims only 28% area overhead compared to a pure 60-bit design (Section 4.2), though they're effectively embedding the Karatsuba recombination logic into the critical path.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Cycle-accurate simulation with RTL synthesis** (Section 6.1): They synthesized to TSMC 7nm PDK and validated component timing. This is more rigorous than pure analytical models.

2. **Comprehensive workload coverage** (Section 6.2): Bootstrap, ResNet-20, HELR256/1024 represent both inference and training workloads. Bootstrap alone consuming 87.73% of execution time (Section 7.2) validates their focus.

3. **Fair performance/area comparison** (Table 6): The Tmult,a/s metric normalizes for slot count, showing 17.6× speedup over F1 and fair comparisons against ARK, CLake, and SHARP at 2^15 slots.

4. **Sensitivity analysis** (Section 7.7, Figure 13): Testing memory capacity (180MB-300MB) and cluster count (2-8) reveals that off-chip bandwidth—not just compute—limits performance gains from additional memory.

**Weaknesses:**

1. **Evaluation key storage glossed over**: Table 4 shows 281MB on-chip memory, but Section 3.1/Figure 3(b) reveals KLSS requires up to 295MB at level 35. The paper claims they use KLSS selectively to avoid this (Section 5.6), but doesn't quantify how often KLSS is actually used vs. avoided due to capacity constraints.

2. **Power comparison is incomplete**: Table 7 shows FAST at 120-160W average, while footnote 3 admits they "assume" SHARP consumes 94.7W uniformly. FAST's 1.7× higher power consumption (Section 7.2) undermines the efficiency narrative.

3. **TBM overhead understated**: Section 4.2 claims "only 28% area overhead" but then admits "19% additional control logic." The actual overhead is closer to 50% when you combine both, and the combiner unit latency in the 60-bit path isn't characterized.

4. **Hoisting evaluation key explosion not addressed**: Figure 3(b) shows hoisting with multiple evaluation keys causes memory explosion (up to 295MB for 8 evks at high levels), but the paper doesn't show how often Aether must fall back to non-hoisting modes.

5. **Single HBM bandwidth assumption**: All comparisons use 1TB/s bandwidth (Table 4). The sensitivity study in Figure 13 shows 44.3% of time is HBM-bound, suggesting the claimed speedups would compress with higher bandwidth baselines.

---

## Q4: What the Authors Didn't Tell You

**1. The TBM critical path penalty:**
The paper doesn't disclose the latency of the Combiner units (C-A, C-B, C-C) in Figure 6. Karatsuba-style recombination requires additional adders and subtracters. At 1 GHz operation (Section 6.1), squeezing 60-bit recombination into one cycle likely requires either aggressive pipelining (adding cycle latency) or a longer critical path (reducing frequency). The claim that "all components operate fully pipelined at 1 GHz" hides this.

**2. The Aether configuration file is static:**
Section 4.1.1 describes Aether as "offline preprocessing." This means the key-switching method selection is fixed at compile time based on predicted level values. If runtime level consumption differs from predictions (due to noise accumulation variations), the selection may be suboptimal. There's no dynamic adaptation—Hemera only manages key prefetching, not method reselection.

**3. The 245MB on-chip memory is mostly SRAM:**
Table 3 shows Register Files at 123.9mm² (44% of total area). That's not "register files" in the traditional sense—it's massive SRAM. The "RF Organization" section (5.6) admits they're using "lane-group-wise counters" to manage sequential access, meaning this is really a scratchpad disguised as register files.

**4. The KLSS method's evaluation key format is incompatible:**
Section 2.1.3 notes KLSS uses 60-bit word lengths for evaluation keys while hybrid uses 36-bit. The paper doesn't explain how they store both key types simultaneously. Either they duplicate keys (doubling storage), or they convert on-the-fly (adding latency). This "key management" complexity is hidden in Hemera's "Evk Pool" abstraction (Section 4.1.2).

**5. The 4-cluster interconnect overhead:**
Figure 7 shows four clusters connected via "lane-wise NoC," but Table 3 lists NoC at 20.6mm² and 27W—7% of area and 8% of power. Section 5.1 says the "global data distribution policy mirrors SHARP and ARK" but doesn't disclose the bisection bandwidth or latency. The "inter-lane-group transpose" between NTT phases (Section 5.2) requires cluster-wide wire connections, which at 1024 lanes must be expensive.

**6. The DSU is borrowed wholesale:**
Section 5.7.1 admits the Double-Prime Scaling Unit "employs the same DSU design as SHARP"—four multipliers, two adders, two modulo units. This is a non-contribution that's bundled into the "Auxiliary Execution Module" to inflate the novelty.

**7. The evaluation key generator (EKG) hides bandwidth:**
Section 5.7.2 claims that storing only part "a" of evaluation keys and generating part "b" via PRNG "significantly reduces key storage cost." But PRNG execution adds latency, and generating "b" requires modular arithmetic. This latency isn't included in their cycle-accurate simulation results, or it's amortized somewhere they didn't disclose.