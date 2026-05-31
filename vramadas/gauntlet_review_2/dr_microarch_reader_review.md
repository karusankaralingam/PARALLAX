# LightML: The Whiteboard Explanation

Alright, let's cut through the marketing and figure out what this thing actually *does* at the hardware level.

## The Core "Magic Trick"

The fundamental insight here is **homodyne detection for multiplication**. Forget everything else for a moment. Here's the physics:

When you interfere two coherent light beams on a 50:50 beam splitter and use *differential* photodetection (subtract the two output intensities), you get:

```
I+ - I- = 2|x||y|sin(Δφ)
```

The key insight: **the common-mode terms (|x|² and |y|²) cancel out**, leaving only the *product* of the two field amplitudes. By controlling the phase difference Δφ to be π/2, you get a clean multiplication. Negative numbers? Encode them as a π phase shift (e.g., -y = |y|e^(jπ)).

**This is the entire paper in one equation.** Everything else is plumbing.

## The Data Flow (How MMM Actually Happens)

Here's how a 128×128 matrix multiplication works:

1. **Optical Fan-out:** A laser source is split into 2×128 beams using a tree of 50:50 splitters
2. **Modulation:** Each beam passes through a segmented Michelson interferometer modulator (MIM) that encodes both amplitude (via segment lengths) and sign (via π phase shift)
3. **Crossbar Intersection:** At each of the 128×128 crosspoints, two beams (one from row, one from column) meet at a 3dB directional coupler
4. **Temporal Accumulation:** The differential photocurrent is integrated on a 15fF capacitor over 1024 pulses at 12 GHz (≈85ns)
5. **ADC Readout:** 8×128 ADCs operating at 0.9 GHz read out the results in 16 rounds

**The critical realization:** Unlike resistive crossbars that do Matrix-Vector Multiplication (MVM) per column, this does true Matrix-Matrix Multiplication (MMM) because *every crosspoint* computes a dot product simultaneously. That's N² dot products vs N dot products per cycle.

## The "Aha!" Moment

The clever part is how they handle **non-linear functions** without leaving the optical domain:

Since optical signals inherently carry phase information, and the interference equation contains sin(Δφ), they can compute sin/cos *for free*. They then use **Fourier Series decomposition**:

```
f(x) = Σ aₖ·sin(2πkx/L) + bₖ·cos(2πkx/L)
```

So to compute tanh(x), sigmoid(x), or any other activation function:
1. First pass: Compute multipliers x, 2x, 3x, ... Nx using amplitude modulators
2. Second pass: Encode these as *phase* inputs, multiply by pre-stored Fourier coefficients

This is genuinely clever—they're exploiting the physics of interference to get non-linearities that would otherwise require dedicated digital hardware.

## The Skeptic's Check: Hidden Costs

Now let's look at what they're glossing over:

### 1. The ADC Problem
They claim 2.97W total on-chip power, but look at Table 3: **ADCs alone consume 1.93W** (65% of on-chip power). They're using 8×128 = 1024 ADCs at 0.9 GHz. The sensitive study in Table 4 shows that reducing ADCs tanks performance—this is the real bottleneck.

### 2. The Memory Wall
They need 3TB/s to saturate the crossbar (2×128 modulators × 12 GHz). HBM2E provides 920 GB/s. That's a 3.3× gap. Their solution? Tiling and pipelining. But look at Figure 13—memory utilization for convolutions is only 40-60%. The crossbar is starving.

### 3. The Precision Tax
They claim 5-bit precision (4 magnitude + 1 sign), but look at Section 3.2 carefully:
- Beam splitter errors accumulate through log₂(2N) splitters
- Phase alignment requires controlling optical path length to ~50nm
- They need "one-time calibration" and "laser trimming" per unit cell

The error model in Figure 3d shows relative error approaching 10⁻² only at 5-bit device precision. They're operating at the edge of what's physically achievable.

### 4. The HBM Elephant
When they include HBM power (2×8W = 16W), total system power jumps from 3W to 19W. Their 109 TOP/s/W efficiency drops to 17.1 TOP/s/W. Still good, but not the headline number.

### 5. Element-wise Operations Are Terrible
Look at Figures 12f-h: LightML is **8-10× slower than A100 for element-wise operations**. Why? They can only use 1/64 of the crossbar for these operations. For transformer attention (which is heavy on element-wise ops), this is a serious problem.

## The Structural Delta vs. Baseline

| Aspect | Resistive Crossbar (ReRAM) | LightML |
|--------|---------------------------|---------|
| Operation per cycle | MVM (N dot products) | MMM (N² dot products) |
| Weight storage | In-memory (resistive state) | Streamed from HBM |
| Precision | 2-4 bits typical | 5 bits claimed |
| Reprogramming | Slow, high-current writes | No reprogramming needed |
| Non-linear functions | External digital unit | In-crossbar via Fourier |
| Speed | ~100 MHz | 12 GHz |

The fundamental architectural difference: **weights are not stationary**. In ReRAM crossbars, weights are programmed into the resistive elements. Here, weights are *streamed* through modulators. This eliminates the slow reprogramming problem but creates the memory bandwidth bottleneck.

## Discussion Questions

1. **What happens when the L1 cache (input buffer) misses?** The double-buffer scheme assumes HBM can keep up. But with 97ns memory latency vs 85ns compute latency, any stall in the memory pipeline directly impacts crossbar utilization.

2. **How does this scale?** They show a 128×128 crossbar. Scaling to 256×256 would require 4× more photodetectors, 4× more capacitors, and the optical fan-out tree gets deeper (more accumulated splitting errors). The error model in Figure 3b suggests this could push them below 5-bit precision.

3. **What about the laser source?** They mention 120mW for the laser, but coherent photonic computing requires *very* stable laser sources. Temperature drift affects the refractive index of silicon waveguides. They claim "negligible temperature increases" at <20W, but in a data center environment, this needs active thermal management.

4. **The LLM results (Figure 14) show LightML is 2.2× slower than A100 for Llama.** They attribute this to "underutilization" with short sequences. But isn't this the exact use case (inference with variable-length inputs) that matters most for deployment?

The paper is solid engineering work, but the headline numbers (325 TOP/s at 3W) require careful contextualization. The real system is 19W with HBM, and the efficiency advantage over GPUs shrinks from 73× to 13.6× when you include memory power.