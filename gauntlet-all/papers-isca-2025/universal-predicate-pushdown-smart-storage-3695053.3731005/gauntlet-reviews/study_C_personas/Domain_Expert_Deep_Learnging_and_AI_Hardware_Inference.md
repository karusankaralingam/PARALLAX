# Paper Deconstruction: UPP: Universal Predicate Pushdown to Smart Storage

## Q1: Whiteboard Explanation

Imagine you're running a massive data warehouse query on Apache Spark. Your query says "find all orders from 2023 where the shipping method contains 'MAIL' and the quantity is between 10 and 20." The traditional approach reads **everything** from the SSD—terabytes of data—ships it all over PCIe to your host CPU, and *then* the CPU starts throwing away 80% of it because it doesn't match your filter.

**The problem UPP solves:** Can we make the SSD itself smart enough to throw away irrelevant rows *before* they ever leave the drive?

**The "Naïve ISP" trap:** Previous approaches tried to do exactly this using FPGAs embedded in SSDs (called "SmartSSDs"). But they hit two walls:
1. **Variable-length columns are a nightmare.** Real CSV data has columns like "gifts" (5 chars), "199" (3 chars), "1-10-21" (7 chars). To find column boundaries, you have to scan byte-by-byte looking for delimiters ('|', '\n'). FPGAs are terrible at this sequential work—Figure 2 shows naïve ISP taking **178+ seconds** where the CPU takes 8 seconds.
2. **Complex predicates don't fit.** Prior work (Table 1) only supported 3-5 simple predicates like `col = 5` or `col < 10`. Real queries have `date_trunc('year', timestamp_col) = 2023` or `LIKE '%MAIL%'`—things FPGAs can't easily compute.

**UPP's trick: Don't evaluate predicates—compare hashes.**

Here's the insight: Instead of parsing `"gifts"` and checking if it equals `"MAIL"`, UPP pre-computes a **256-bit "row vector"** for each row during data ingestion. This vector encodes:
- For numeric columns: which quantile bucket the value falls into (e.g., bit 3 = "value is in range 10M-11M")
- For text columns: which dictionary words appear (e.g., bit 7 = "contains 'MAIL'")

At query time, UPP compiles predicates into a **"query vector"**—also 256 bits—encoding "which bits must be on for this row to *possibly* match?"

Now the FPGA's job is trivial: **bitwise AND** the row vector with the query vector, check if the result matches expectations. This is massively parallel, fixed-width, and takes ~3 cycles per row versus 21 cycles for prior work (Section 8.3).

**The false positive trade-off:** This approach can't be exact. A row might hash to the same bucket as a matching value but actually be different. UPP accepts **false positives** (sending some irrelevant rows to the CPU) but guarantees **zero false negatives** (never drops a relevant row). The CPU then does a precise check on the reduced dataset. With 256-bit hashes, false positive rates are 0-6 percentage points (Section 8.1).

---

## Q2: The Key Insight

**The Delta (The Real Contribution):**

The genuine innovation is **reframing ISP-based database filtering from "compute predicates on data" to "compare pre-computed hash signatures."** This is a fundamental architectural pivot, not an incremental tweak.

Prior ISP work (Ibex [75], POLARDB [24], YourSQL [33]) tried to build increasingly complex predicate evaluation logic in FPGAs—essentially reimplementing database operators in hardware. This scales terribly because:
1. Every new function (`acos`, `date_trunc`, user-defined functions) requires new FPGA logic
2. Variable-length parsing is inherently sequential
3. Complex DNF predicates explode the comparator count

UPP inverts this: **the complexity moves to the software layer** (compiling predicates into query vectors), while the **hardware stays simple** (fixed-width bitwise operations). The paper explicitly states this in Section 3 (page 420): "Most previous work focuses on pushing more database operations into FPGAs. In contrast, UPP pursues an orthogonal direction."

**The mechanism that makes this work:** The paper's key technical contribution is the **two primitive constructs** (Equation 1, Section 1):
- **Type I:** `mono_func(numeric_col) ∈ [lb, ub]` — Any monotonically increasing/decreasing function on a numeric column with range bounds
- **Type II:** `contains(text_col, val).and(...)` — Text containment with additional conditions

The insight (Section 2.2) is that monotonic functions preserve ordering, so checking bucket boundaries is sufficient—you don't need to evaluate `log10(col)`, just check if the column's quantile could *possibly* satisfy the range. For text, pre-extracted frequent tokens serve as a lossy "contains" check.

**The UPP-ISA (Table 2):** Two operations—`INCL` (all bits in query must be present in row) and `OVLP` (any bit overlap)—combined with `AND`/`OR`, can express arbitrary DNF predicates. This is the paper's core architectural abstraction.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Real Hardware, Not Simulation:**
This is implemented on actual Samsung SmartSSD hardware (Table 4: Xilinx KU15P FPGA, 3.84TB NVMe SSD, PCIe Gen3 x4). The resource utilization is reported (Table 3: 50.1% LUTs, 52.1% BRAM). This is not a "we simulated a magical future chip" paper—it's deployable today.

**2. End-to-End, Industry-Standard Benchmark:**
They run full TPC-H queries (not microbenchmarks) on 100GB datasets through Apache Spark (Figure 6). The speedups of 1.2×–7.9× are **end-to-end latency**, including Spark overhead, PCIe transfers, and CPU post-processing. This is the metric that matters.

**3. Honest About the Filter Ratio Dependency:**
Figure 2 explicitly shows that UPP only wins when filter ratio < 0.8. They don't hide this—they build a two-stage bypass optimization (Section 5.4) that falls back to CPU when ISP wouldn't help. The paper shows both the "Filter ratio (by CPU w/o false positive)" and "Filter ratio (by UPP-ISP w/ false positive)" in Figure 6, making the false positive overhead transparent.

**4. Strong Coverage Analysis (Table 5):**
They collected 6.4 million SQL queries from GitHub and compared UPP's predicate coverage to POLARDB. UPP supports 7/7 numeric comparisons, 6/6 math functions, 5/5 pattern matching (with caveats), versus POLARDB's 6/7, 0/6, 0/5. This isn't cherry-picked—it's systematic.

**5. Reasonable Baseline Comparison:**
The comparison to POLARDB in Section 8.3 uses the same hardware assumptions (4-byte items, 64-byte rows, 3 conjunctions with 5 predicates each) and shows UPP achieves 2.3× higher scanning throughput. They explain the cycle-level reasoning: 21 cycles vs. 3 cycles per row.

### Weaknesses

**1. The Core:SmartSSD Ratio is Generous:**
The paper uses 4 CPU cores with 1 SmartSSD (4C:1S). At 40C:1S (more typical of real servers), they admit UPP's advantage shrinks to 37% (Section 8.2). The recommended deployment—24 SmartSSDs per dual-socket 16-128 core server—requires buying a *lot* of specialized hardware. Figure 8a shows that at 8C:1S, speedup drops to 2.5×.

**2. PCIe Gen3 x4 Bottleneck:**
SmartSSD uses PCIe Gen3 x4 (theoretically ~4 GB/s, but they measure 3.3 GB/s read bandwidth). The paper acknowledges (Section 8.1) that "SSD and FPGA communicate via a PCIe interface... SmartSSD cannot fully exploit the SSD's internal bandwidth advantages." Modern datacenter SSDs are PCIe Gen5 x4 (14 GB/s). The internal bandwidth argument (Benefit B3 in Section 2.1) is largely moot on this hardware.

**3. Preprocessing Overhead is Non-Trivial:**
Generating data hash for the 15GB ORDERS table takes 142 seconds (Section 8.1)—almost entirely (135.5s) spent reading/parsing. For the 74GB LINEITEM table, this would be ~10 minutes. While "one-time," this matters for frequently updated data. The paper waves this away for HTAP (Section 1, page 420) but doesn't demonstrate it.

**4. The Modified Queries Problem:**
The asterisks in Figure 6 (Q1*, Q2*, etc.) indicate **modified queries**. Section 7 admits: "The literals... are set to have filter ratios around 20%." Standard TPC-H queries have varying selectivities—some filter almost nothing. The 1.2×–7.9× speedup range is valid, but the distribution across *unmodified* TPC-H would look different.

**5. False Positive Analysis is Thin:**
Section 5.4 gives theoretical bounds (2/m for range, d/m for text), but the empirical validation is just "0–6 percentage points" from Figure 6. There's no breakdown by query type or column cardinality. High-cardinality string columns with many tokens could behave much worse.

**6. No Energy Baseline Breakdown:**
Figure 7 shows system-wide energy savings of 9%–87%, but doesn't separate FPGA energy from "avoided CPU energy." We can't tell if the SmartSSD itself is energy-efficient or if savings come entirely from reduced CPU work.

---

## Q4: What the Authors Didn't Tell You

**1. This Only Works for Read-Heavy Analytical Workloads:**
The entire approach assumes data is written once and queried many times. The metadata generation (142s for 15GB) amortizes over queries, but for transactional workloads with frequent updates, you'd need to regenerate hashes constantly. The paper mentions HTAP (Section 1) but provides zero evaluation.

**2. The Dictionary Mining is a Hidden Complexity:**
Section 5.1 describes using SpaceSaving algorithm on "1,000 randomly chosen 4KB blocks" to build per-column dictionaries. What happens when queries use words *not* in the dictionary? UPP falls back to no filtering for that predicate (Section 5.1: "UPP's pruning is employed only when text predicates involve those mined dictionary words"). Adversarial queries—or simply rare but important filter values—get zero benefit.

**3. The "User-Defined Function" Support is a Bait-and-Switch:**
Listing 2 shows a Python class where users define `column()` and `argument()` methods. But this requires users to *mathematically prove* their function is monotonic and provide an inverse mapping. The paper claims (Section 5.2) this is "common for Spark," but the cognitive load is significant. A user who writes `sin(col) < 0.1` gets nothing—UPP explicitly won't offload non-monotonic functions.

**4. The Comparison to POLARDB is Apples-to-Oranges:**
POLARDB (reference [24]) is a production cloud database with a full software stack, transactional support, and years of optimization. UPP is a research prototype on Spark. The "2.3× scanning throughput" comparison (Section 8.3) isolates just the predicate evaluation loop under specific assumptions. In reality, POLARDB's ISP is integrated into a commercial system with different trade-offs.

**5. The FPGA Resource Headroom is Concerning:**
Table 3 shows 50.1% LUT and 52.1% BRAM utilization for just 2 UP-COMPs handling 3 conjunctions each. Scaling to more complex queries would require more UP-COMPs, but the FPGA is already half-full. The paper doesn't discuss what happens with 10-way OR predicates.

**6. Variable-Length Column Support is Metadata-Dependent:**
UPP handles variable-length columns by storing row sizes in metadata, not by parsing them. This means you must generate metadata *before* you can query. If your data arrives as a raw CSV and you need to query it immediately, you're back to CPU parsing. The "no format change required" claim (Abstract) is true for the *data*, but you absolutely need the auxiliary metadata files.

**7. The SmartSSD Product is Niche:**
Samsung SmartSSD is not widely deployed. The paper's acknowledgments thank "AMD-Xilinx HACC" (Heterogeneous Accelerated Compute Cluster)—an academic access program. Real datacenter adoption would require significant ecosystem support that doesn't exist today.

**8. Q21's 1.2× Speedup Reveals the Ceiling:**
The paper admits (Section 8.1) that Q21's poor speedup is "mainly due to the complex join and aggregation operations on multiple tables." This is the honest truth: UPP only accelerates filtering. For join-heavy or aggregation-heavy queries—which dominate real analytical workloads—the benefits plateau.