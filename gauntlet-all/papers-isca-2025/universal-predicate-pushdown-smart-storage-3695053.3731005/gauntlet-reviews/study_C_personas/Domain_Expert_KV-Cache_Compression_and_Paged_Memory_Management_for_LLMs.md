# Paper Deconstruction: UPP: Universal Predicate Pushdown to Smart Storage

## Q1: Whiteboard Explanation

Alright, let me explain what this paper actually does, stripped of the academic jargon.

**The Problem:** You have a database stored on an SSD. When you run a query like `SELECT * FROM orders WHERE date > '2024-01-01' AND status = 'shipped'`, the traditional approach is:
1. Read the *entire* table from SSD to CPU memory
2. CPU parses each row (finding delimiters like commas in CSV)
3. CPU evaluates the filter predicates
4. Most rows get thrown away

This is wasteful. The SSD has compute (an FPGA in "SmartSSD") sitting right next to the data. Why not filter *inside* the storage device and only send relevant rows to the CPU?

**The Classic ISP Problem:** Prior "In-Storage Processing" (ISP) approaches tried this but hit two walls:
1. **Variable-length columns are hell for FPGAs.** Parsing `"John,Doe,42"` vs `"Jane,Smith-Johnson,37"` requires sequential delimiter detection—something CPUs with out-of-order execution do well, but FPGAs do terribly (Figure 1a shows this bottleneck explicitly; §2.3 reveals a naive FPGA implementation takes *178 seconds* vs 8 seconds on CPU).
2. **Complex predicates don't fit.** Prior work (Table 1) supports only simple `=, <, >` on 2-4 fixed-length columns. Real queries have `dateadd()`, `LIKE '%pattern%'`, user-defined functions.

**UPP's Core Trick:** Don't parse the data at query time. Instead:

1. **At data ingestion (one-time cost):** Create a compact "fingerprint" for each row called a *row vector* (256 bits). Each column gets a slice of bits (e.g., 16 bits for 16 columns). For numeric columns, the fingerprint encodes which quantile bucket the value falls into. For text columns, it's a Bloom-filter-like encoding of frequent words.

2. **At query time:** The database compiles your WHERE clause into a *query vector*—the same 256-bit format—that represents "what bits should be on for this row to *possibly* match?"

3. **In-storage processing:** The FPGA does simple bitwise operations: `row_vector AND query_vector`. This is massively parallel (no parsing!), fixed-length (no variable-length headaches!), and can evaluate complex DNF predicates in one shot.

**The Catch:** This is *approximate filtering*. The FPGA might say "row 17 could match" when it doesn't (false positive), but it will *never* say "row 17 doesn't match" when it does (no false negatives). The CPU then does exact filtering on the smaller set of candidate rows. The paper claims 0-6 percentage points of false positives (§8.1).

Think of it like a bouncer with a checklist: the FPGA bouncer lets in anyone who *might* be on the guest list, and the CPU host inside does the actual ID check.

---

## Q2: The Key Insight

**The Real Innovation:** The paper's genuine contribution is the *abstraction layer* between SQL predicates and FPGA-friendly operations. Prior ISP work asked "how do we implement `LIKE` on an FPGA?" UPP asks "how do we *avoid* implementing anything complex on an FPGA?"

The key insight is the recognition that most filter predicates fall into two "primitive constructs" (Equation 1, §2.2):

1. **Type I:** `monotonic_function(numeric_column) ∈ [lower_bound, upper_bound]`
2. **Type II:** `contains(text_column, value).and(...)`

The genius is that *monotonicity* is exploited brilliantly. If `f(x)` is monotonically increasing, and you're asking "is `f(column) < threshold`?", you don't need to compute `f()` for every row. You just need to know which quantile bucket the column value is in, and check if that bucket's boundaries satisfy the condition. This reduces arbitrary function evaluation to a pre-computed lookup.

For text, they use a dictionary of frequent words (mined via SpaceSaving algorithm, §5.1) and encode presence as bits—essentially a per-row, per-column Bloom filter.

**The ISA is the Delta:** The UPP-ISA (Table 2) with its `INCL`, `OVLP`, `AND`, `OR` operations is the paper's concrete mechanism. It's a domain-specific instruction set for predicate pushdown:
- `INCL` (Inclusion): Do all required bits match? (For equality predicates)
- `OVLP` (Overlap): Do any required bits match? (For range predicates)

This lets complex DNF predicates (ORs of ANDs) compile down to a small number of bitwise operations that execute in constant time regardless of predicate complexity.

**What's NOT novel:** The idea of using metadata/fingerprints for filtering (zone maps, Bloom filters) is old. The idea of ISP for databases exists (Table 1 lists 10 prior systems). The novelty is the *specific representation* that makes arbitrary predicates—including user-defined functions—compile to fixed FPGA logic without reprogramming.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Real Hardware, Real System:** They implemented on actual Samsung SmartSSD hardware (Table 4), not simulation. The FPGA utilization numbers are real (Table 3: 50.1% LUTs, 52.1% BRAM). This is credibility gold in systems research.

2. **End-to-End Evaluation:** Figure 6 shows *end-to-end* TPC-H query latency, not just the filtering component. They separate storage-access latency from compute-only latency, which is honest—you can see exactly where the wins come from.

3. **False Positive Analysis is Transparent:** Figure 6 plots both the "true" filter ratio (empty diamonds) and UPP's filter ratio with false positives (filled diamonds). The gap is 0-6 percentage points. They don't hide this. §5.4 provides the theoretical false positive bounds (2/m for range, d/m for text).

4. **Energy Measurements:** Figure 7 reports actual power measurements via `ipmitool` and `xbutil`, showing 9-87% system-wide energy savings. This is increasingly important and often omitted.

5. **Coverage Analysis is Novel:** Table 5's comparison against POLARDB using 6.4 million real SQL queries from GitHub is excellent methodology. It shows UPP covers datetime functions, math functions, and pattern matching that POLARDB cannot—a meaningful practical advantage.

6. **Sensitivity Study is Useful:** Figure 8 shows performance across different Core:SmartSSD ratios (8:1 to 1:1) and hash lengths (64b to 256b). This helps readers understand when UPP helps.

### Weaknesses

1. **The Baseline is Weak (Critical):** They compare against "Apache Spark's regular query processing" (§7). Spark is notoriously slow for row-oriented CSV processing. The 1.2×-7.9× speedups would shrink dramatically against:
   - A columnar format (Parquet) with predicate pushdown
   - A properly tuned system like DuckDB or Polars
   - Even Spark with Parquet instead of CSV
   
   The paper acknowledges in §9 "this work focuses on... CSV-like row-wise formats" but this is a significant scope limitation buried late.

2. **Filter Ratio Manipulation is Concerning:** §7 states: "The literals... are set to have filter ratios around 20%." The queries marked with asterisks (Q1*, Q2*, etc.) have modified constants. Figure 2 already showed that at filter ratio 0.8, UPP barely beats the baseline. By cherry-picking 20% filter ratios, they maximize their advantage. What's the filter ratio distribution in real workloads? Unknown.

3. **PCIe Gen3 x4 Bottleneck Acknowledged but Unresolved:** §8.1 admits "SmartSSD cannot fully exploit the SSD's internal bandwidth advantages—despite its FPGA DRAM bandwidth of 15.4 GB/s—over external bandwidth." The SSD-to-FPGA link is only 3.3 GB/s. This means benefit (B3) from §2.1 is not achieved.

4. **Preprocessing Cost is Non-Trivial:** §8.1 reveals metadata generation for a 15GB table takes 142 seconds (135.5s reading/parsing + 6.5s hashing). For the 74GB LINEITEM table, this would be ~700 seconds—nearly 12 minutes. They claim "one-time cost" but for frequently updated tables, this adds up. The 5-7% storage overhead is acceptable, but the time cost needs comparison against alternatives.

5. **Complex Joins Still on CPU:** Q21 shows only 1.2× speedup because "complex join and aggregation operations... cannot benefit from UPP" (§8.1). For complex analytical queries where joins dominate, UPP's filtering wins get amortized away.

6. **Hash Collision Analysis is Incomplete:** §5.4's false positive analysis assumes independence and uniform hashing. Real data has correlations. The 0-6% false positive claim in Figure 6 is empirical on TPC-H—what happens with skewed real-world distributions?

7. **No Time-to-First-Row Latency:** The paper reports total query latency but never discusses latency variance or tail latencies. For interactive analytics, P99 latency matters.

---

## Q4: What the Authors Didn't Tell You

1. **The "Naïve ISP" Strawman (Figure 2):** The 178-second "Naïve ISP" number is for their own broken implementation of delimiter parsing on FPGA. No sane ISP system would do this. The comparison is designed to make UPP look revolutionary against an absurd baseline. The real comparison should be: "UPP vs. POLARDB on the same hardware" or "UPP vs. CPU-based filtering with proper SIMD optimization."

2. **CSV is a Dying Format for Analytics:** The entire paper is built around the premise that people store analytics data in CSV. In 2025, serious analytics workloads use Parquet, ORC, or Delta Lake—all columnar, all with built-in predicate pushdown at the file format level. The paper's §9 "Discussion" mentions UPP could extend to columnar "by encoding column value sizes in metadata," but this is hand-waving—no implementation, no evaluation.

3. **The 4:1 Core:SmartSSD Ratio is Suspicious:** §7 justifies this with vendor recommendations, but it's convenient that at 8:1, the speedup is only 2.5× (Figure 8a). Modern servers have 64-128 cores. At 40:1, they admit UPP is only 37% faster. For well-provisioned cloud analytics, the benefit shrinks dramatically.

4. **User-Defined Functions Aren't Really "Supported":** §5.2's Listing 2 shows a user-defined `ToLunarDate` function. But look closely—the user must manually implement `column()` and `argument()` methods that explicitly convert to Unix epochs. This isn't "automatic support for UDFs"—it's "users must re-implement their UDFs in our framework." The paper's claim of "generality" masks significant programmer burden.

5. **Dictionary Size vs. Hash Bits Trade-off is Glossed Over:** §5.1 claims "the number of words in the dictionary (e.g., 1K or 10K) is independent of the hash bits (e.g., 16)." But then admits "only when words in the same cell are hashed into the bit we are looking at, false positives occur." With 16 bits and 10K dictionary words, collisions are nearly guaranteed. The "1,000 words per column" limit (§8.1: "18KB for three dictionaries") seems chosen for convenience, not optimality.

6. **SmartSSD is a Niche Product:** The paper is implemented on Xilinx/Samsung SmartSSD, which has limited market adoption. Competing approaches like CXL-attached accelerators or GPU-based filtering might be more practical paths. The paper doesn't discuss deployment considerations or total cost of ownership.

7. **The "2.3× over POLARDB" Claim is Theoretical:** §8.3's performance comparison is based on "reasonable assumptions" and cycle counting, not actual measurements against POLARDB. Given POLARDB is deployed at Alibaba scale, this is a missed opportunity for real comparison.

8. **What Happens When Metadata is Stale?** For HTAP workloads (mentioned in §1 as applicable), newly inserted rows won't have row vectors. The paper hand-waves this as "generating metadata when recently written data are moved into a read-optimized view" but provides no mechanism or evaluation for mixed read-write workloads.