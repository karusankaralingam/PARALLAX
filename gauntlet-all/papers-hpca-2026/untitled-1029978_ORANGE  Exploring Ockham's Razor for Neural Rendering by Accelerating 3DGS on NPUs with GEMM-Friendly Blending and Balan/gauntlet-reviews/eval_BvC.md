# Ablation Evaluation -- Study B vs Study C
**Paper:** 1029978 ORANGE  Exploring Ockham's Razor for Neural Rendering by Accelerating 3DGS on NPUs with GEMM Friendly Blending and Balan
**Model:** gemini-3.1-pro-preview
**Generated:** 2026-06-01 10:15

---
## Run 1 -- temperature=0.2  |  A=Study B, B=Study C

| Dimension | Analysis A (1-5) | Analysis B (1-5) |
|-----------|:----------------:|:----------------:|
| 1. Mechanistic Accuracy | 5 | 5 |
| 2. Insight Depth | 5 | 5 |
| 3. Critical Rigor | 5 | 5 |
| 4. Breadth of Perspective | 4 | 4 |
| 5. Calibration | 5 | 5 |
| 6. Usefulness | 5 | 5 |

**Overall preference:** B somewhat

**Justification (3-5 sentences):** 
Both analyses are exceptional, featuring devastating and highly specific architectural critiques that go far beyond surface-level complaints. Analysis A brilliantly catches the memory capacity mismatch (a 72KB Gaussian working set versus a 64KB scratchpad), while Analysis B correctly identifies that mapping a K=6 GEMM onto a 32x32 systolic array will result in terrible hardware utilization (~19% row activation). Analysis B earns a slight preference for its punchier formatting, its identification of the strawman baseline in the hybrid evaluation, and its sharp observation that the scalar `exp()` bottleneck still persists on the vector units despite the GEMM reformulation.

---
## Run 2 -- temperature=0.3  |  A=Study C, B=Study B

| Dimension | Analysis A (1-5) | Analysis B (1-5) |
|-----------|:----------------:|:----------------:|
| 1. Mechanistic Accuracy | 5 | 5 |
| 2. Insight Depth | 5 | 4 |
| 3. Critical Rigor | 5 | 4 |
| 4. Breadth of Perspective | 4 | 4 |
| 5. Calibration | 5 | 5 |
| 6. Usefulness | 5 | 4 |

**Overall preference:** A clearly

**Justification (3-5 sentences):** 
Analysis A stands out due to its exceptional microarchitectural critique, specifically identifying that a K=6 GEMM will severely underutilize a standard 32×32 systolic array and that the scalar `exp()` operations remain a massive, potentially unhidden bottleneck on the vector units. While Analysis B is also strong and correctly identifies valid memory bandwidth and dynamic scene concerns, Analysis A's precise mathematical breakdown of the mechanism is sharper. Analysis A perfectly balances explaining the authors' clever algebraic trick with rigorously dismantling the unstated hardware inefficiencies of their implementation, making it the superior preparation document.

---
## Run 3 -- temperature=0.3  |  A=Study B, B=Study C

| Dimension | Analysis A (1-5) | Analysis B (1-5) |
|-----------|:----------------:|:----------------:|
| 1. Mechanistic Accuracy | 5 | 5 |
| 2. Insight Depth | 4 | 5 |
| 3. Critical Rigor | 4 | 5 |
| 4. Breadth of Perspective | 4 | 4 |
| 5. Calibration | 5 | 5 |
| 6. Usefulness | 4 | 5 |

**Overall preference:** B clearly

**Justification (3-5 sentences):**
Analysis B stands out due to its exceptional critical rigor, specifically identifying deep microarchitectural realities that Analysis A misses. Pointing out that a K=6 GEMM dimension will yield poor utilization on a standard 32x32 systolic array, and calculating the massive number of residual `exp()` operations that remain bottlenecked on the vector units, demonstrates expert-level architectural understanding. While Analysis A makes a strong point about scratchpad memory limits, Analysis B's systematic dismantling of the paper's baselines (e.g., the strawman hybrid NPU, asymmetric area comparisons) and its sharper articulation of the core mathematical insight make it the superior preparation document.

---
## Summary Across 3 Runs

### Overall Preferences

- Run 1 (temp=0.2): **Study C somewhat**
- Run 2 (temp=0.3): **Study C clearly**
- Run 3 (temp=0.3): **Study C clearly**

### Average Scores (Study B vs Study C)

| Dimension | Study B (avg) | Study C (avg) | Delta |
|-----------|:--------------:|:--------------:|:-----:|
| Mechanistic Accuracy | 5.0 | 5.0 | +0.0 |
| Insight Depth | 4.3 | 5.0 | -0.7 |
| Critical Rigor | 4.3 | 5.0 | -0.7 |
| Breadth of Perspective | 4.0 | 4.0 | +0.0 |
| Calibration | 5.0 | 5.0 | +0.0 |
| Usefulness | 4.3 | 5.0 | -0.7 |
| **Overall mean** | **4.5** | **4.8** | **-0.3** |
