# Study B — Rich Directive
**Paper:** 3695053.3731005  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-06-01 09:27

---

Q1: Whiteboard Explanation

Let me walk you through UPP as if we were at a whiteboard.

**The Problem Setup:**
Imagine you have a massive CSV file (say, 75GB) sitting on an SSD, and you want to run a SQL query with a WHERE clause like `WHERE price > 100 AND category LIKE '%electronics%'`. Today, you read the entire 75GB over PCIe to host DRAM, then the CPU parses every row character-by-character to find delimiters, extracts columns, and evaluates predicates. This is wasteful—you might only need 5% of the rows.

**Why Existing ISP Solutions Fall Short:**
Prior in-storage processing (ISP) approaches tried to push filtering into the SSD's embedded FPGA, but they hit two walls:
1. **Data generality**: They only work with fixed-length columns. Variable-length data (like strings in CSV) requires sequential delimiter parsing, which FPGAs do terribly—one byte per cycle, serially.
2. **Filter generality**: They hardcode a handful of simple comparators (=, <, >). Complex predicates with functions like `date_trunc()` or user-defined functions? Impossible without reprogramming the FPGA.

**UPP's Core Insight:**
Instead of teaching the FPGA to understand every possible predicate and data format, UPP introduces an abstraction layer. At data ingestion time, you compute a compact bit-vector representation (a "row vector") for each row. At query time, you compile predicates into "query vectors." The FPGA's job becomes trivially simple: bitwise AND/OR operations between fixed-length vectors.

**How Row Vectors Work:**
- For numeric columns: Quantize the value range into buckets (e.g., 16 buckets for 16 bits). Each value maps to exactly one bit.
- For text columns: Hash frequently-occurring tokens from a pre-built dictionary into bit positions. Multiple tokens = multiple bits set.

**How Query Vectors Work:**
- Equality (`col = 5`): Set the single bit corresponding to bucket 5.
- Range (`col BETWEEN 3 AND 7`): Set bits 3-7.
- Text contains (`col LIKE '%gift%'`): Set the bit for token 'gift'.

**The ISA:**
UPP defines four operations: INCL (inclusion—all query bits must be present), OVLP (overlap—any query bit present), AND, OR. A complex DNF predicate compiles into a sequence of these operations.

**Execution Flow:**
1. FPGA loads row vectors (metadata) and query vectors.
2. UP-COMP units perform parallel bitwise comparisons across many rows simultaneously.
3. Rows passing the test are marked; a pruning kernel copies only those rows to output.
4. Host CPU receives pre-filtered data and runs the original query (no false negatives, possible false positives).

**Why This Works:**
- Fixed-size vectors enable massive parallelism (both inter-row and intra-row).
- No delimiter parsing needed—we never look at the raw CSV during filtering.
- New predicates = new software logic to generate query vectors, not FPGA reprogramming.

---

Q2: The Key Insight

The key insight is that **predicate evaluation can be decoupled from data representation through pre-computed, fixed-length hash signatures**, enabling FPGA-friendly parallel bitwise operations regardless of the complexity of the original predicate or the variability of the data format.

This is genuinely novel in the ISP-for-databases literature. Prior work assumed the FPGA must directly process the data and directly evaluate the predicate, leading to a combinatorial explosion of FPGA logic for different operators, data types, and formats. UPP recognizes that for the common case of filter predicates (those expressible as monotonic functions over numeric columns falling in a range, or text containment checks), you can represent the satisfiability question as a bit-vector membership test.

The specific mechanism—quantizing numeric values into histogram buckets and hashing dictionary tokens for text—is not individually new (zone maps, Bloom filters, dictionary encoding all exist). What's new is:
1. **Unifying these into a single ISA** (INCL/OVLP/AND/OR) that handles arbitrary DNF combinations.
2. **Designing this specifically for ISP constraints**: The row vector length is fixed and independent of column count or data variability, enabling deterministic FPGA resource usage and predictable throughput.
3. **Making it extensible via software**: User-defined functions work by overriding a base class that computes bucket boundaries—the FPGA logic never changes.

The paper correctly identifies that the real bottleneck in naïve ISP isn't predicate evaluation—it's delimiter parsing. By sidestepping parsing entirely (evaluating metadata instead of raw data), UPP solves the fundamental problem rather than optimizing the wrong thing.

---

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Real hardware implementation**: This isn't a simulation. They implement on Samsung SmartSSD with actual FPGA synthesis (50% LUT utilization reported). The end-to-end numbers are credible.

2. **Comprehensive TPC-H evaluation**: All 22 queries evaluated with breakdown of storage-access vs. compute latency. The 1.2×–7.9× speedup range is honest—they don't cherry-pick the best cases.

3. **False positive analysis is rigorous**: They provide theoretical bounds (2/m for range, d/m for text) and empirical validation showing 0-6 percentage points of false positives with 256-bit hashing. This directly addresses correctness concerns.

4. **Fair comparison methodology**: The 4:1 CPU-core-to-SmartSSD ratio is justified by vendor cooling recommendations and prior work configurations. They also show sensitivity across different ratios.

5. **Coverage analysis against POLARDB**: Using 6.4M real SQL queries from GitHub to demonstrate predicate coverage is a strong methodological choice that goes beyond synthetic benchmarks.

**Weaknesses:**

1. **Selectivity modification obscures real-world applicability**: The authors admit they modified TPC-H literals to achieve ~20% filter ratios. The asterisked queries (Q1*, Q2*, etc.) dominate the results. For unmodified queries with high filter ratios, UPP provides minimal benefit (as Figure 2 shows, UPP loses at FR > 0.8). The paper doesn't characterize what filter ratios are typical in production workloads.

2. **Preprocessing cost is underemphasized**: Generating data hash for the 74GB LINEITEM table takes 142 seconds. For a one-time cost this is acceptable, but the paper doesn't address:
   - What happens when data is appended/updated?
   - How does dictionary staleness affect text column accuracy?
   - What's the interaction with data freshness requirements?

3. **SmartSSD's internal bandwidth limitation weakens the argument**: The paper acknowledges that SSD-to-FPGA-DRAM bandwidth (3.3 GB/s via PCIe) is far below FPGA DRAM bandwidth (15.4 GB/s). This means the FPGA is often waiting for data, not compute-limited. The claimed 2.3× throughput advantage over POLARDB assumes both are memory-bound, which may not hold.

4. **Limited ISA scalability analysis**: The current UP-COMP supports 3 OR-connected conjunctions with 4 predicates each. What happens with more complex queries? The paper claims this is "easily adjustable" but provides no resource scaling data.

5. **Energy measurement methodology is coarse**: System-wide power via IPMI doesn't isolate memory controller, PCIe, and other component contributions. The 9-87% savings claim conflates reduced runtime with reduced power draw.

6. **Missing comparison with CPU-side metadata filtering**: The paper acknowledges that CPUs could also use the row vectors for filtering, potentially with SIMD. Why not compare against this? It would isolate the benefit of ISP from the benefit of the metadata abstraction.

---

Q4: What the Authors Didn't Tell You

**The dictionary problem is harder than presented:**
The paper breezily describes mining "frequently used tokens" via SpaceSaving on 1,000 random 4KB blocks. But real text columns have Zipfian distributions—rare tokens dominate the long tail. If a query searches for a token not in the dictionary, UPP cannot help (it falls back to host processing). The paper provides no analysis of dictionary coverage rates on real workloads, only TPC-H's artificially clean data.

**Update handling is an open problem:**
The entire architecture assumes append-only or batch-reload scenarios. For OLTP workloads with frequent updates, you'd need to regenerate row vectors incrementally. The HTAP databases they cite as beneficiaries (§1) have exactly this challenge. The paper handwaves this as "generating metadata when recently written data are moved into a read-optimized view" but provides no mechanism or overhead analysis.

**The 4:1 ratio assumption deserves scrutiny:**
Modern servers can have 64-128 cores with 8-16 NVMe slots. The paper uses 4 cores with 1 SmartSSD, which is an unusually ISP-favorable configuration. At 40:1, they claim only 37% improvement—this is buried in one sentence. Production deployments likely operate at higher core-to-storage ratios.

**FPGA resource headroom is limited:**
At 50% LUT and 52% BRAM utilization for just 2 UP-COMPs, there's not much room for expansion. Adding more comparators or supporting more complex DNFs will hit resource ceilings quickly. The paper doesn't discuss what happens when you need more parallelism.

**The "no false negative" guarantee has caveats:**
It holds only for predicates that fit their primitive constructs. Non-monotonic functions (sin, modulo), substring operations with wildcards at arbitrary positions, and correlated predicates silently fall back to host processing. The coverage analysis (Table 5) shows important gaps: subqueries (0/3), string-integer conversion (0/5).

**PCIe Gen3 is outdated:**
SmartSSD uses PCIe Gen3 x4 (≈3.9 GB/s). Modern NVMe SSDs use Gen4 or Gen5 with 2-4× higher bandwidth. The balance between ISP compute and I/O bandwidth will shift significantly with newer interfaces—potentially making the FPGA even more underutilized, or requiring proportionally faster FPGA logic.

**The comparison with POLARDB is on unequal terms:**
POLARDB uses a different FPGA (Kintex UltraScale+), different SSD, likely different table formats (their "custom format"), and wasn't designed for CSV-like variable-length data. The 2.3× throughput claim is based on analytical assumptions about their recursive comparator, not empirical measurement. A true apples-to-apples comparison would require implementing POLARDB's approach on the same hardware.