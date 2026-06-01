# Paper Deconstruction: REIS (ISCA '25)

**Note to the student:** I realize this paper has nothing to do with Homomorphic Encryption—it's about In-Storage Processing for Retrieval-Augmented Generation (RAG). My expertise as "Dr. Eva Noise" doesn't quite apply here, but let me recalibrate and give you a proper architectural systems deconstruction. The skills transfer: finding the real contribution versus the marketing, identifying where the bottlenecks hide, and checking whether the evaluation is honest.

---

## Q1: Whiteboard Explanation

Imagine you're asking ChatGPT a question about yesterday's news. The problem: ChatGPT only knows what it was trained on months ago. The solution is **Retrieval-Augmented Generation (RAG)**—you bolt a searchable database of documents onto the LLM. When a query arrives, you:

1. Convert the query into a high-dimensional "embedding" vector (768-8192 dimensions)
2. Search a database of millions of document embeddings to find the most similar ones (Approximate Nearest Neighbor Search, or ANNS)
3. Retrieve the actual document text associated with those embeddings
4. Feed both the query and retrieved documents to the LLM for generation

**The bottleneck the authors identify (Section 3.1, Figure 2):** For a 41.5M entry Wikipedia database, **84% of the RAG pipeline latency is just loading the dataset from the SSD to host memory**. The actual search and generation are fast; the I/O is killing you.

**REIS's core idea:** Instead of moving terabytes of embeddings from the SSD to the CPU for comparison, do the comparison *inside the SSD itself*. This is called **In-Storage Processing (ISP)**.

**How it works (Figure 6, Section 4.3):**

1. The query embedding is broadcast from the SSD controller's DRAM down into each NAND flash plane's **Cache Latch**
2. Database embeddings are read from flash into the **Sensing Latch**
3. The existing **XOR logic** (normally used for data randomization) computes the bitwise difference between query and database embeddings
4. The existing **Fail-Bit Counter** (normally used to verify programming success) counts the number of differing bits—this *is* the Hamming distance
5. Only the small set of "closest" embeddings and their associated document addresses bubble back up to the controller

**The key trick:** They use **Binary Quantization (BQ)**—each embedding dimension becomes a single bit. This lets them use purely bitwise operations (XOR + popcount) that already exist in flash dies for reliability checking. No new compute hardware needed.

---

## Q2: The Key Insight

**The Delta (what's actually new):**

The paper has three genuine contributions, but one stands out:

**Primary Insight:** Graph-based ANNS algorithms (HNSW, DiskANN) used by prior ISP works [178, 299] have **irregular, sequential access patterns** that destroy SSD parallelism. The Inverted File (IVF) algorithm has **streaming, predictable access patterns** that perfectly exploit the massive internal parallelism of modern SSDs (Section 4.2, Figure 5).

Prior works like NDSearch [299] tried to accelerate HNSW inside storage, but graph traversal is inherently sequential—you can't know which vertex to visit next until you've processed the current one. IVF, by contrast, organizes embeddings into clusters. Once you identify which clusters to search, you simply scan through them contiguously. This is a perfect fit for NAND flash, which has enormous bandwidth when accessed sequentially.

**Secondary Insight (Section 4.1.3):** They cleverly repurpose the **Out-of-Band (OOB) area** of flash pages—normally reserved for ECC metadata—to store pointers linking embeddings to their associated document chunks. This eliminates the need for a separate lookup table and ensures that when you read an embedding page, you automatically have the document addresses.

**Tertiary Insight (Section 4.1.2):** Using **Enhanced SLC Programming (ESP)** mode achieves zero bit-error-rate without ECC, which is critical because they're doing computation inside the flash dies *before* error correction would normally occur.

**What's NOT new:** The idea of ISP for ANNS exists (NDSearch, ICE, VStore). Binary quantization for ANNS exists. Using flash's internal logic for computation exists (Flash-Cosmos [224]). The novelty is in the *composition* and the observation that algorithm choice fundamentally changes ISP viability.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Honest bottleneck characterization (Section 3.1, Figures 2-3):** The authors actually measure end-to-end RAG pipelines and show that I/O dominates. The 84% I/O overhead claim is reproducible—they specify the hardware (A100 GPU, Xeon Gold 5118, Samsung PM9A3) and datasets (HotpotQA, wiki_en). This is good experimental hygiene.

2. **Fair baseline (Table 3):** They compare against a genuinely powerful system: a 256-core AMD EPYC 9554 with 1.5TB of DDR4 DRAM. This isn't some strawman single-threaded comparison.

3. **Multiple recall points (Figures 7-8, 10):** They sweep Recall@10 from 0.90 to 0.98, showing performance at different accuracy tradeoffs. This is important because ANNS speedups are meaningless if recall tanks.

4. **Comparison to prior ISP works (Section 6.4, Figures 10-11):** They compare against ICE [106] and NDSearch [299] using appropriate configurations, showing 7-24× speedup over ICE and 1.7-2.6× over NDSearch.

5. **Energy efficiency numbers (Figure 8):** The 55× average energy efficiency improvement is meaningful because they measure CPU power via AMD μProf and model SSD power from real characterization data [224].

### Weaknesses

1. **The "No Hardware Modifications" claim needs scrutiny (Section 4.4.2, Table 2):** While they don't add computational units, they *do* require:
   - New NAND flash commands (IBC, XOR, GEN_DIST, RD_TTL in Table 2)
   - Modified flash die control logic (a new finite state machine)
   - Multi-Plane Input Broadcasting (MPIBC) requiring "dedicated Multiplexer logic" (Section 4.3.4)
   
   These are firmware and control logic changes. Calling this "no hardware modifications" is technically true but somewhat misleading—you can't deploy this on existing SSDs without SSD vendor cooperation.

2. **ESP reliability assumptions (Section 4.1.2):** They claim ESP achieves "zero BER" citing Flash-Cosmos [224]. But this is under specific conditions (1-year retention, 10k P/E cycles). Real-world deployments may face different thermal conditions, retention requirements, or higher cycle counts. Section 7.2 mentions this but doesn't quantify the safety margin.

3. **Dataset loading is not eliminated, just shifted (Table 4):** In their end-to-end breakdown, "Search (and retrieval)" drops to 0.02-0.15%, but the documents still need to be transferred to the host for LLM generation. The 9GB of documents for wiki_en (mentioned in Section 3.2) still move across PCIe. The speedup comes from not moving the *embeddings* (5GB after BQ), not the documents.

4. **Sensitivity to SSD configuration (Figures 7-8):** REIS-SSD2 (16 channels, 4 planes/die) outperforms REIS-SSD1 (8 channels, 2 planes/die) by 2.6×. The approach's benefits scale with internal parallelism. Budget SSDs with fewer channels will see proportionally smaller gains.

5. **The billion-scale comparison is thin (Figure 11):** The comparison to NDSearch uses SIFT-1B and DEEP-1B, which are image descriptor datasets with 128-dimensional vectors—not the 768-8192 dimensional text embeddings that are the paper's target domain. The 1.7-2.6× speedup over NDSearch is less impressive than the 13× over CPU-Real.

6. **Garbage collection and wear-leveling implications (Section 7.2):** The authors acknowledge REIS needs to "prioritize maintenance tasks over RAG operations" and operates exclusively in RAG-mode or normal-mode. This means real deployments face mode-switching overhead and potential tail latency from deferred maintenance.

---

## Q4: What the Authors Didn't Tell You

1. **Index construction cost is ignored:** The IVF algorithm requires offline clustering (indexing stage). For 41.5M embeddings, this is non-trivial. They mention "IVF_Deploy()" in Table 1 but never report indexing time or cost. If your knowledge base updates frequently, this overhead matters.

2. **The query embedding still comes from outside:** Before REIS can search, the user query must be encoded into an embedding by a model like all-roberta-large-v1 (Section 3.1). This encoding runs on the GPU and takes time. For single-query scenarios, this latency may dominate. The 13× speedup is for the *retrieval* stage alone.

3. **Memory pressure on the SSD controller:** The R-DB, R-IVF, and TTL data structures live in the SSD's internal DRAM (typically 0.1% of capacity, so 1GB per TB). Section 4.2.1 states R-IVF costs 15 bytes × number_of_clusters. For 16,384 clusters (used in Figure 5), that's 240KB—fine. But the TTL structures hold 10k embedding entries during search (Section 4.3.2), each with DIST, EMB, RADR, DADR fields. For 1024-dimensional binary embeddings, that's ~130 bytes per entry × 10k = 1.3MB. Multiple concurrent queries could strain this limited DRAM.

4. **Reranking still reads INT8 embeddings from TLC (Section 4.3.2, step 7):** The binary quantization search is fast, but reranking fetches the top-10k embeddings in INT8 precision from the TLC partition. This incurs the slower TLC read latency (~75-100μs vs. 22.5μs for ESP-SLC) and ECC overhead. The paper doesn't break down how much of the final latency is reranking vs. initial search.

5. **Distance filtering threshold selection is empirical (Section 4.3.3):** They experimentally find that 99% of documents can be filtered for HotpotQA with k=10. But this threshold "weakly depends on dataset size" and is determined offline. New datasets may require recalibration. If the threshold is too aggressive, you lose recall; too conservative, you lose the speedup.

6. **The comparison to ICE may be unfair:** ICE [106] stores data in a format tolerating errors without ECC, incurring 8-32× storage overhead. The comparison in Figure 10 includes this overhead in ICE's performance calculation. But REIS's ESP approach also has overhead—it stores binary embeddings in SLC (1 bit/cell vs. TLC's 3 bits/cell), a 3× density penalty. Section 6.3.1's comparison to "REIS-ASIC" (4.1-6.5× slowdown without ESP) reveals how much of REIS's advantage comes specifically from ESP vs. algorithmic improvements.

7. **Concurrent query handling is unclear:** The paper evaluates single-query throughput (QPS). But RAG systems serve many users simultaneously. With multiple queries, does the XOR logic support concurrent operations? Do the TTL structures support multiple queries? Section 7.2 mentions "one core for Quicksort and reranking, while the other cores (3 out of 4) are still available for regular SSD operations"—but not for additional REIS queries.

8. **The acknowledged authorship dispute (Acknowledgments):** The authors explicitly state that "Andreas Kosmas Kakolyris" made "very significant contributions" but was "not allowed to be a co-author" due to ISCA policy, which they "wholeheartedly disagree with and find very problematic and unethical." This is unusual to see in a published paper and suggests significant post-submission work occurred during rebuttal—work important enough to merit authorship credit.