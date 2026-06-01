# Paper Analysis: UPP: Universal Predicate Pushdown to Smart Storage

## Q1: Whiteboard Explanation

**The Core Problem:**
In-storage processing (ISP) promises to filter data *inside* the SSD before sending it to the host CPU, saving bandwidth and cycles. But existing ISP solutions have two crippling limitations:

1. **Fixed-length columns only** – Real CSV data has variable-length strings (e.g., "DELIVER IN PERSON"), but prior ISP designs require fixed 4-16 byte records because parsing delimiters sequentially on FPGAs is painfully slow (Figure 1a shows >178 seconds just for ISP processing).

2. **Simple predicates only** – Prior work handles `col = 5` or `col < 10`, but not `trunc('year', timestamp_col) = 2023` or `shipmode LIKE '%MAIL%'` combined in DNF form.

**UPP's Solution – The Hashing Trick:**

Instead of parsing columns byte-by-byte inside the FPGA, UPP pre-computes *metadata* offline:

- **Data Hash (Row Vectors):** For each row, generate a fixed 256-bit vector. Each column gets ~16 bits. Numeric columns encode which quantile bucket the value falls into. Text columns encode which dictionary words appear (via SpaceSaving to mine frequent tokens).

- **Query Hash (Query Vectors):** At query time, compile predicates into bit vectors representing "which buckets/words must be present?"

**The ISA (Table 2):**
- `INCL`: Row vector contains ALL bits set in query vector (equality-like)
- `OVLP`: Row vector contains AT LEAST ONE bit set in query vector (range-like)
- `AND`/`OR`: Combine results for DNF evaluation

**Execution Flow (Figure 5):**
1. **Table Scan Kernel:** Stream row vectors through UP-COMPs (parallel bitwise comparators), produce "valid row vector" + filter ratio
2. **Pruning Kernel:** Use row lengths (also in metadata) to extract only valid rows from raw data
3. **Host Processing:** Spark processes the smaller, pre-filtered table

**Key Insight:** Because row/query vectors are *fixed-length*, you get massive parallelism – both intra-row (evaluate all predicates simultaneously via bitwise ops) and inter-row (pipeline multiple rows through UP-COMPs).

---

## Q2: The Key Insight

The pivotal insight is encapsulated in **Equation (1), Section 1** – the definition of "primitive constructs":

> **(Type I)** `mono_func(numeric_col) ∈ [lb, ub]`  
> **(Type II)** `contains(text_col, val).and(...)`

The authors recognize that **most real-world filter predicates can be reduced to these two forms**, and critically, both forms can be evaluated using **fixed-size bit vector comparisons** rather than parsing actual column values.

**Why this matters:**
- Monotonic functions (log, exp, dateadd, floor, etc.) preserve bucket boundaries – if you know which 1/16th quantile a value falls in, you can determine satisfiability of `f(col) ∈ [a,b]` by checking only the bucket boundaries (Section 2.2 explains this with concrete examples).
- Text containment can be approximated via dictionary-based hashing – if "MAIL" is a frequent word, we hash it to a bit position, and checking `contains(col, 'MAIL')` becomes checking if that bit is on.

This insight enables UPP to compile **arbitrary DNFs of user-defined functions** into a handful of bitwise operations (Section 5.2, Listing 1 → Step 2 in Figure 4), without reprogramming FPGA logic. Prior work (Table 1) required implementing each operator individually, which doesn't scale.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**S1: Real Hardware, Not Simulation**
The entire evaluation runs on actual Samsung SmartSSDs with Xilinx KU15P FPGAs (Table 4). This is critical – they report real HLS synthesis results (Table 3: 50.1% LUT utilization, 52.1% BRAM) and measured power via `xbutil` and `ipmitool`. No cycle-accurate simulator approximations here.

**S2: Honest Baseline Comparison**
Figure 2 shows "Naïve ISP" taking >178s for what UPP does in ~2s. They don't cherry-pick – they implement the obvious FPGA design (Figure 1a) and show why it fails. This builds credibility.

**S3: End-to-End TPC-H with Spark Integration**
Figure 6 shows full query processing on 100GB TPC-H, including joins, aggregations, and subqueries that UPP doesn't accelerate. The 1.2×–7.9× speedups are measured against *Spark's actual query runner*, not a microbenchmark.

**S4: Coverage Analysis Against Real-World Queries**
Section 8.3 analyzes 6.4 million SQL queries from GitHub to quantify predicate coverage (Table 5). UPP handles 7/7 comparison types, 6/6 math functions, 5/5 pattern matching (if tokens appear) – concrete evidence of generality.

**S5: Transparent About False Positives**
Figure 6 explicitly shows the gap between "FR (CPU w/o false positive)" and "FR (UPP-ISP w/ false positive)" – 0-6 percentage points. They don't claim perfect filtering; they show the overhead is bounded.

### Weaknesses

**W1: The 4:1 Core:SmartSSD Ratio is Aggressive**
Section 7 justifies 4 CPU cores per SmartSSD by citing [1] (Supermicro server supporting 24 SmartSSDs with 16-128 cores). But that's 0.67-5.3 cores/SSD – a wide range. Figure 8a shows gains drop significantly at 8C:1S (2.5× vs 4.3×). The sensitivity study is welcome, but the "headline" results assume a ratio favorable to UPP.

**W2: TPC-H Literal Modifications Are Acknowledged but Pervasive**
Section 7 admits: *"The literals inside a predicate are set to have filter ratios around 20%."* This affects 17 of 22 queries (marked with asterisks in Figures 6-7). While adjusting literals for evaluation is "common in the literature," it means the ~20% filter ratio is *designed*, not observed. Real-world filter ratios could be higher (less benefit) or lower (more benefit).

**W3: Metadata Preprocessing Costs Are Non-Trivial**
Section 8.1 reports 142 seconds to generate data hash for a 15GB table (135s reading/parsing + 6.5s hashing). For a 100GB dataset, extrapolating naively gives ~15 minutes. They claim "one-time cost," but for frequently updated tables or ad-hoc analytics on new data, this matters.

**W4: PCIe Gen3 ×4 Bottleneck Limits Gains**
Section 8.1 notes: *"SSD and FPGA communicate via a PCIe interface with a maximum read bandwidth of 3.3 GB/s... cannot fully exploit the SSD's internal bandwidth."* The FPGA DRAM provides 15.4 GB/s, but the SSD→FPGA path is 4.6× slower. This architectural limitation constrains (B3) from Section 2.1.

**W5: No DRAM Refresh or Endurance Modeling**
The SmartSSD has 4GB DDR4-2400 FPGA DRAM. For metadata (row vectors + row lengths), they report 5-7% storage overhead (Section 8.1). For 100GB tables, that's 5-7GB of metadata – exceeding FPGA DRAM capacity. They must stream from SSD, but this isn't explicitly quantified.

**W6: User-Defined Functions Require Python Wrapper**
Listing 2 shows users must implement `ToLunarDate(UPPNumericFilter)` with `column()` and `argument()` methods. While not requiring FPGA reprogramming, it's still programmer effort – not truly "universal" for arbitrary SQL UDFs.

---

## Q4: What the Authors Didn't Tell You

**1. The SmartSSD's SSD Controller is a Black Box**
They use a Samsung V-NAND® SSD (Table 4), but never discuss how the SSD controller interacts with their workload. GC pauses, wear leveling during metadata writes, and read amplification from small pruning output blocks are not characterized. The 3.3 GB/s read bandwidth is a *peak* spec, not a sustained measurement.

**2. FPGA Frequency and Timing Closure**
Table 3 shows resource utilization, but Section 7 only mentions "300 MHz" in passing (comparing to 3.4 GHz CPUs in Section 2.3). Whether they achieved timing closure at 300 MHz, or what the critical path looks like, is unstated. HLS-generated RTL often struggles with timing; this is a known issue.

**3. Cold Start and Kernel Loading Latency**
Section 3.2 describes loading `.xclbin` binaries into FPGA, but the measured latency for kernel loading is never reported. For interactive analytics, this one-time cost could dominate short queries.

**4. The Dictionary Mining is Fragile**
Section 5.1 claims 1,000 random 4KB blocks suffice for dictionary construction. But the statistical argument assumes uniform distribution of words – skewed data (e.g., one customer name dominating a column) could cause frequent tokens to be missed. They never validate dictionary quality on non-TPC-H datasets.

**5. Pruning Kernel Write Amplification**
Algorithm 2 (Section 6.2) writes 64B chunks to FPGA DRAM. If valid rows are scattered, output buffer fill rates vary wildly. They mention "multiple sub-iterations" but don't quantify how often partial blocks are written, which affects effective bandwidth.

**6. No Comparison to CPU-Side Bloom Filters**
The data hash mechanism is conceptually similar to Bloom filter-based pruning, which databases like Spark already support for partition pruning. A comparison showing UPP's advantage over CPU-evaluated Bloom filters on the same metadata would strengthen the novelty claim.

**7. The "Simulation is Doomed to Succeed" Irony**
They criticize prior ISP work for proprietary designs and limited comparisons (Section 8.3), then compare against POLARDB using theoretical analysis rather than actual POLARDB numbers. The 2.3× throughput claim assumes 4-byte items and specific predicate counts – a simulation of sorts.