# Ablation Evaluation -- Study A vs Study B
**Paper:** 3695053.3731062
**Model:** gemini-3.1-pro-preview
**Generated:** 2026-06-01 10:16

---
## Run 1 -- temperature=0.2  |  A=Study B, B=Study A

| Dimension | Analysis A (1-5) | Analysis B (1-5) |
|-----------|:----------------:|:----------------:|
| 1. Mechanistic Accuracy | 5 | 5 |
| 2. Insight Depth | 5 | 4 |
| 3. Critical Rigor | 5 | 4 |
| 4. Breadth of Perspective | 5 | 4 |
| 5. Calibration | 5 | 5 |
| 6. Usefulness | 5 | 4 |

**Overall preference:** A clearly

**Justification (3-5 sentences):** 
Analysis A provides a significantly deeper architectural critique, particularly by identifying how CapChecker resolves CHERI's "intentional use" requirement through hardware provenance rather than instruction-level selection. Furthermore, A's critical rigor is outstanding—it correctly points out that "Fine" mode contradicts the paper's "unmodified black box" premise by requiring specific hardware interface designs, and it catches a subtle evaluation red herring regarding 128-bit copy speedups. While Analysis B is also highly accurate and well-calibrated, its critiques and broader connections (like ASIC vs. FPGA clock speeds) are slightly more generic compared to A's surgical dissection of the paper's capability-specific claims and its integration with the broader CHERI ecosystem (e.g., CheriBSD revocation).

---
## Run 2 -- temperature=0.3  |  A=Study B, B=Study A

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
Both analyses accurately describe the CapChecker mechanism and correctly identify the core insight of externalizing capability enforcement to an interposition layer. However, Analysis A demonstrates exceptional critical rigor that sets it apart. It identifies specific, substantive issues in the paper, such as the mathematical discrepancy between the 256-entry table size and the 56-buffer benchmarks across 8 accelerators, and astutely points out that "Fine mode" actually requires specific accelerator interface designs, which undermines the paper's "unmodified black-box" premise. Furthermore, Analysis A provides excellent systemic context by bringing up CheriBSD OS integration and cloud FPGA multi-tenancy, making it an incredibly useful preparation document.

---
## Run 3 -- temperature=0.3  |  A=Study B, B=Study A

| Dimension | Analysis A (1-5) | Analysis B (1-5) |
|-----------|:----------------:|:----------------:|
| 1. Mechanistic Accuracy | 5 | 4 |
| 2. Insight Depth | 5 | 4 |
| 3. Critical Rigor | 5 | 4 |
| 4. Breadth of Perspective | 5 | 4 |
| 5. Calibration | 5 | 4 |
| 6. Usefulness | 5 | 4 |

**Overall preference:** A clearly

**Justification (3-5 sentences):** 
Analysis A stands out for its exceptional specificity and deep architectural understanding of the CHERI ecosystem. It goes beyond surface-level critique by doing the math on capability table entries versus benchmark requirements, catching a specific red herring in the evaluation (the 128-bit copy speedup), and identifying the exact architectural consequences of the Coarse mode (reducing the address space to 56 bits). While Analysis B is solid, accurate, and well-written, it relies on slightly more generic critiques (e.g., "synthetic threat model", "FPGA vs ASIC") and misses the profound CHERI-specific tension of "intentional use" that Analysis A so brilliantly captures.

---
## Summary Across 3 Runs

### Overall Preferences

- Run 1 (temp=0.2): **Study B clearly**
- Run 2 (temp=0.3): **Study B clearly**
- Run 3 (temp=0.3): **Study B clearly**

### Average Scores (Study A vs Study B)

| Dimension | Study A (avg) | Study B (avg) | Delta |
|-----------|:--------------:|:--------------:|:-----:|
| Mechanistic Accuracy | 4.7 | 5.0 | -0.3 |
| Insight Depth | 4.0 | 5.0 | -1.0 |
| Critical Rigor | 4.0 | 5.0 | -1.0 |
| Breadth of Perspective | 4.0 | 4.7 | -0.7 |
| Calibration | 4.7 | 5.0 | -0.3 |
| Usefulness | 4.0 | 5.0 | -1.0 |
| **Overall mean** | **4.2** | **4.9** | **-0.7** |
