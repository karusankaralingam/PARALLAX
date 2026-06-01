## Q1: Whiteboard Explanation

Let me draw out how UPP actually works at the hardware level.

**The Problem UPP Solves:**

Existing in-storage processing (ISP) for databases has two fatal flaws:
1. They can only handle **fixed-length columns** (e.g., 4-byte integers padded to 16 bytes)
2. They support only **simple predicates** like `col = 5` or `col < 10`, not complex functions like `date_trunc()` or `LIKE '%pattern%'`

The naive approach to filtering variable-length CSV data on an FPGA is horrifically slow. Figure 1a shows why: you must sequentially scan byte-by-byte looking for delimiters (`|` and `\n`), buffer each column, then compare. The paper reports this takes **>178 seconds** for a simple query that the CPU handles in ~8 seconds (Figure 2).

**UPP's Core Trick: Pre-computed Bit Vectors**

Instead of parsing columns on-the-fly, UPP pre-computes a fixed-size "row vector" (256 bits) for each row during data ingestion. Each column gets an equal slice (e.g., 16 bits for a 16-column table).

For **numeric columns**: The column's value range is quantized into buckets. A value of 5M when the range is 0-16M would land in bucket 6 (out of 16), setting bit 6 in that column's 16-bit slice.

For **text columns**: Frequently occurring tokens are identified via SpaceSaving algorithm and hashed. If a cell contains "MAIL", the hash of "MAIL" determines which bit(s) to set.

**Query Execution (Figure 4, §6.1):**

At query time:
1. **UPP-DB** translates predicates into "query vectors" using the same hashing scheme
2. Two ISA operations suffice:
   - `INCL (Inclusion)`: Bitwise AND, then equality check — for exact matches like `col = 'MAIL'`
   - `OVLP (Overlap)`: Bitwise AND, then check if any bit is set — for range queries like `10 <= col <= 20`
3. **UPP-ISP** (the FPGA kernel) reads 256-bit row vectors from metadata, performs parallel bitwise operations against query vectors, and outputs a "valid row vector" bitmap
4. A **pruning kernel** uses this bitmap plus pre-stored row lengths to copy only matching rows to output

**The Key Data Path (Figure 5):**

```
SSD → FPGA DRAM (Meta-ISP: row vectors + row lengths)
                    ↓
         [Table Scan Kernel: UP-COMPs evaluate INCL/OVLP]
                    ↓
              Valid row bitmap + filter ratio
                    ↓
         [Pruning Kernel: extract valid rows from DB table]
                    ↓
              Host DRAM (filtered table)
```

The magic is that **all predicates become fixed-width bit operations**, regardless of the original column type or predicate complexity. A `date_trunc('year', timestamp) = 2023` becomes the same hardware operation as `quantity >= 10`.

---

## Q2: The Key Insight

**The Singular Clever Trick:**

UPP's insight is recognizing that **monotonic functions preserve bucket ordering**, which means you can evaluate `f(col) ∈ [lb, ub]` without actually computing `f(col)` at query time.

Here's the concrete mechanism (§2.2, §5.2):

If `f()` is monotonically increasing (e.g., `log()`, `date_trunc()`), and you know a column value falls in bucket `i` with boundaries `[B_low, B_high]`, then:
- `f(col)` is bounded by `[f(B_low), f(B_high)]`
- If the predicate's target range `[lb, ub]` doesn't overlap with `[f(B_low), f(B_high)]`, the row can be pruned **without ever parsing the actual column value**

The paper formalizes this into two "primitive constructs" (Equation 1):
- **Type I**: `mono_func(numeric_col) ∈ [lb, ub]`
- **Type II**: `contains(text_col, val).and(...)`

Any predicate expressible in these forms can be converted to bit-vector operations. The user-defined function example in Listing 2 shows this beautifully: a lunar calendar conversion becomes ISP-compatible by simply providing boundary-evaluation functions.

**Why This Matters Architecturally:**

This is fundamentally different from prior ISP work (Table 1) which implements individual operators in FPGA logic. Those approaches require:
- New RTL for each new function
- HLS recompilation (which can take minutes to hours)
- Limited predicate counts due to comparator count

UPP instead offloads the **semantic complexity to software** (the database layer generates query vectors using `m+1` function calls per column) while the **FPGA does only dumb bitwise ops**. This is the classic accelerator design principle: make the hardware simple and regular, push irregularity to software.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Real Hardware, Real System (§7, Table 4):** This isn't simulation. They run on actual Samsung SmartSSDs with Xilinx KU15P FPGAs. Table 3 shows concrete resource utilization (22.6% LUTs for kernels, 18.6% BRAM). The end-to-end integration with Apache Spark is genuine.

2. **Honest Latency Breakdown (Figure 6):** The paper separates "storage-access latency" from "compute-only latency," making it clear that gains come from both reduced data transfer AND reduced CPU computation. Q21's modest 1.2× speedup is transparently attributed to complex joins that UPP can't help with.

3. **False Positive Analysis (§5.4, Figure 6):** They explicitly show the gap between CPU filter ratios (diamonds without false positives) and UPP filter ratios (filled diamonds). For 256-bit hashes on 16 columns, the gap is 0-6 percentage points—empirically validating their theoretical bound of `2/m` for range queries.

4. **Sensitivity Studies (Figure 8):** The Core:SmartSSD ratio sweep (8C:1S to 1C:1S) and hash length sweep (64b to 256b) reveal the design space. The "Ideal" bar at 0% false positive provides a theoretical ceiling.

5. **Coverage Comparison (Table 5, §8.3):** The 6.4M real SQL query analysis against POLARDB is substantive. UPP covers math functions, datetime functions, and pattern matching that POLARDB cannot.

### Weaknesses

1. **Cherry-Picked Filter Ratios (§7, explicitly acknowledged):** The authors state they "set literals to have filter ratios around 20%." Figure 2 already shows that at 80%+ filter ratio, UPP provides no benefit. The TPC-H results (Figure 6) thus represent a **favorable operating point**, not worst-case or average-case behavior. Queries marked with asterisks (*) were modified.

2. **PCIe Gen3 x4 Bottleneck (§8.1, Table 4):** The SmartSSD uses PCIe Gen3 x4, giving 3.3 GB/s read bandwidth between SSD and FPGA DRAM. The FPGA DRAM itself provides 15.4 GB/s. The paper admits this "currently restricts UPP from fully leveraging (B3)"—i.e., the SSD internal bandwidth advantage is largely wasted. This is a hardware limitation, but it means the reported speedups are pessimistic relative to what better hardware could achieve.

3. **Metadata Storage Overhead (§8.1):** 5-7% storage overhead for metadata is non-trivial at scale. For a 100TB data warehouse, that's 5-7TB of metadata. The preprocessing time (142s for a 15GB table) suggests metadata generation for 100TB would take ~26 hours.

4. **Single SmartSSD Evaluation:** All TPC-H results use 1 SmartSSD. While §8.2 extrapolates to different ratios, there's no multi-SSD evaluation showing scalability or coordination overhead.

5. **Limited Predicate Complexity in FPGA (Table 2, §7):** Each UP-COMP evaluates "up to three OR-connected conjunctions, with each conjunction comprising up to four predicates." For queries exceeding this (TPC-H Q19 in Listing 1 is the most complex shown), it's unclear if multiple ISP iterations are needed or if those predicates are simply not offloaded.

---

## Q4: What the Authors Didn't Tell You

### The Hidden Hardware Costs

1. **Row Length Storage Tax:** To enable the pruning kernel (Algorithm 2), UPP stores the byte length of every row. For billion-row tables, this is another several GB of metadata on top of the row vectors. The paper bundles this into "5-7%" but doesn't break it down.

2. **Dictionary Memory Footprint:** Each text column gets a 1000-word dictionary (§5.1). For a table with 50 text columns, that's 50K words (~300KB assuming 6 bytes/word). This lives in FPGA BRAM or must be fetched per query.

3. **SpaceSaving Preprocessing Assumption:** The frequent-token mining uses "1,000 randomly chosen 4KB blocks" (§5.1). This assumes data is relatively uniform. If your data has skewed token distributions across different regions of the table (e.g., time-partitioned logs), the dictionary may be suboptimal for certain queries.

### Architectural Assumptions They Glossed Over

4. **64B Granularity Limitation (Algorithm 2, lines 13-16):** The pruning kernel reads/writes in 64-byte chunks. If valid rows are sparse and short, the kernel may do significant wasted work assembling partial rows across chunk boundaries. The paper doesn't quantify this overhead.

5. **Two-Kernel Overhead:** UPP splits ISP into Table Scan and Pruning kernels (§5.4). This means two kernel invocations per chunk, each with XRT API overhead. The paper claims this enables "selective kernel bypassing" but doesn't measure the invocation cost.

6. **INCL/OVLP Ratio is Hardcoded:** The paper states "one INCL and three OVLPs" per UP-COMP (§7). If a query has many equality predicates and few range predicates (or vice versa), this ratio may be suboptimal. "Easily adjustable through parameters" means **recompiling the FPGA bitstream**, which takes hours.

### What They Quietly Admit

7. **CPU-Side Filtering Still Required:** UPP guarantees **no false negatives** but **allows false positives** (§5.3). The host CPU must re-evaluate predicates on the filtered data to get exact results. For the 6% false positive rates observed, this means ~6% of rows are filtered twice.

8. **Pattern Matching Limitations (Table 5 footnote):** The "5*/5" for pattern matching includes the asterisk: "If extracted tokens appear." This means `LIKE '%rare_word%'` where `rare_word` isn't in the dictionary simply cannot be accelerated. The SpaceSaving algorithm explicitly biases toward frequent tokens.

9. **No Join Acceleration:** Unlike AQUOMAN [77] and others in Table 1, UPP explicitly does not accelerate joins. For join-heavy queries (like Q21's 1.2× speedup), UPP's value proposition weakens significantly.

10. **Timestamp Format Fragility (§8.3):** The paper admits that direct FPGA computation "does not work if data is stored slightly differently (e.g., 2023/11/30T23:59:59 vs 2023-11-21 23:59:59)." UPP's hashing approach sidesteps this, but it means the metadata generation must handle format variations—a complexity not discussed.