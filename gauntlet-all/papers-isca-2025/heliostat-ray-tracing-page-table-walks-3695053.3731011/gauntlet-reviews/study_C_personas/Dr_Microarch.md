# Heliostat: Reverse-Engineering the Mechanism

## Q1: Whiteboard Explanation

Let me walk you through what's actually happening in the silicon here.

**The Problem (Figure 5, Section 3):** GPU page table walks are bottlenecked not by TLB size, but by **PTW bandwidth**. The baseline GPU has 128 CUs but only 16 Page Table Walkers in the GMMU. When you look at Figure 5's breakdown, the dominant component of L2 TLB miss penalty is "Queuing" – requests are just sitting there waiting for a PTW to become available. The authors show in Figure 6 that many workloads have reuse distances of 2^9 to 2^16 pages – you'd need a massive TLB to help, and even then, you haven't fixed the throughput problem.

**The Core Insight:** Each CU already has a Ray Tracing Accelerator (RTA) sitting idle during non-RT workloads. RTAs are designed to traverse BVH trees via depth-first search. Page tables are also trees traversed via DFS. Both need:
1. Independent memory transaction capability
2. Tree traversal state tracking (stack)
3. Node decoding logic

**The Wiring (Figures 9, 10, 12):**

1. **PTE Decoding Unit (PDU):** This is the new operation unit added to the RTA pipeline (Figure 10). It slots into the existing "Operation Units" alongside Ray-Box and Ray-Triangle intersection units. The PDU contains:
   - Two comparators (PS bit check for large pages, level counter for 4KB pages)
   - One adder + one shifter (for calculating next-level PTE address: `base_addr + (VPN_index << 3)`)
   - Access control comparators (valid bit, rwx bits)

2. **RayPTWProperty (Figure 11):** Repurposes the existing 256-bit Ray Buffer entry with a smaller 112-bit structure: 64-bit VPN, 32-bit PID, 8-bit rwx, 8-bit level counter, 64-bit physical address result.

3. **RT-PTW Forwarding Unit (RFU, Figure 12b):** A new arbiter that monitors GMMU PTW availability. If GMMU is busy → route request to requester's local RTA via inter-CU NoC.

4. **L1S Cache Hijacking (Figure 13):** The L1 Scalar cache (shared per Shader Array, used for kernel constants) is 1-19% utilized. They reserve one way per set via an "access mask register" – a bit vector where unsetting a bit excludes that way from normal operations and dedicates it to page table caching.

**Heliostat+ Extension (Section 6):**
- Leverages the RTA's existing **secondary ray** mechanism (used for reflections/refractions)
- When PDU+ detects the lookahead address diverges from on-demand at a page table level, it forks a new thread in the same warp's Ray Buffer
- Lookahead results stored in reserved L1S space with 8 PTEs packed per 64B cache line (Figure 17)
- Cuckoo filters (32 filters, 2KB total) in RFU+ predict which Shader Array might have cached the translation

---

## Q2: The Key Insight

**The "magic trick" is recognizing that RTAs are general-purpose tree traversal engines that happen to be underutilized 100% of the time during non-RT workloads.**

The deeper insight is that **the hardware mismatch between GPU parallelism (128 CUs) and translation bandwidth (16 PTWs) can be solved with zero net area cost** by repurposing existing fixed-function units. This is not just "use idle hardware" – it's specifically exploiting the structural similarity between BVH traversal and radix-tree page table walks:

| BVH Traversal | Page Table Walk |
|---------------|-----------------|
| Tree structure | 4-level radix tree |
| DFS traversal | DFS traversal |
| Ray Buffer tracks state | RayPTWProperty tracks level, VPN |
| Mem Access FIFO issues loads | Same FIFO fetches PTEs |
| Node Decoder determines operation | Node Decoder checks PS bit |
| Traversal Stack | Level counter (simpler) |

The secondary insight for Heliostat+ is that **secondary ray semantics map directly to prefetch semantics**: both fork a new traversal path that shares upper-level state with the primary traversal. This lets lookahead translations skip shared PTE accesses (Figure 15 shows divergence only at level 3).

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **RTL-level synthesis (Section 8.10):** The authors synthesized PDU+ in Verilog using Synopsys DC and FreePDK45. This is rare and valuable – they report 0.024 mm² for 128 PDU+ units vs. 1.57 mm² for a 128-PTW GMMU (1.53% area). This makes the "hardware tax" claim credible.

2. **Comprehensive sensitivity analysis (Section 8.7):** They sweep PTW counts (8/16/32), L2 TLB MSHRs (64/128/256), NoC latency (1x/1.5x/2x), TLB latency, and page sizes. Figure 27 shows Heliostat+ achieves 3.28× speedup with only 8 PTWs – demonstrating scalability in resource-constrained scenarios.

3. **Realistic baseline and comparisons:** 16 PTWs is well-cited as the standard (references [8, 12, 16, 21, 22, 24, 40, 41, 46-48, 53]). Comparing against Valkyrie and BarreChord (two recent PACT/ISCA papers) rather than just the baseline is appropriate.

4. **L1S utilization data (Section 5.4):** They measure actual L1S utilization at 1-19%, justifying the cache hijacking without hand-waving.

5. **Energy accounting (Figure 34):** They model leakage + dynamic power across GMMU, PTW, PTW Cache, PDU, and L1S over execution time, showing 41.42% energy reduction on average.

### Weaknesses

1. **Simulation-only evaluation:** Despite RTL synthesis, all performance numbers come from MGPUSim (a cycle-level simulator). There's no FPGA prototype or silicon measurement. The ~50-cycle NoC latency (Table 2) is noted as a "single-stage crossbar" – real GPU interconnects are more complex.

2. **RTA availability assumption is absolute:** Section 7 states "When an RT kernel is invoked, Heliostat is not activated." The paper assumes workloads are either 100% RT or 0% RT. Mixed workloads (e.g., a game rendering frames while also running DLSS/compute) are not evaluated. They mention this could be monitored but don't implement it.

3. **Stride predictor is simplistic (Section 6.4.1):** Only 4 strides (1, 64, 128, 256) are supported, hard-coded based on the 128-CU configuration. The paper admits "Heliostat+ outperforms [SP and ASP] when integrated with our proposed lookahead mechanism" (Section 8.7.6) but doesn't explore adaptive stride detection.

4. **L1S contention understudied:** While L1S utilization is low *on average*, the paper doesn't show per-phase utilization. Kernel argument loads happen at kernel launch – if many kernels launch frequently, this could create contention bursts.

5. **Page fault path is a fallback to GMMU (Section 5.2.4):** When PDU detects invalid PTE or access control mismatch, it "returns the translation request to the RFU so that the GMMU can replay the translation." The latency of this replay path isn't characterized.

6. **Cuckoo filter false positive impact (Section 7):** They claim 0.38% empirical false positive rate but don't show the impact on specific workloads. A false positive causes a wasted RTA lookup + NoC round-trip before falling back.

---

## Q4: What the Authors Didn't Tell You

1. **The "112-bit fits in 256-bit" claim hides complexity:** RayPTWProperty (112 bits) is smaller than RayProperty (256 bits), so they claim "no extra storage space" (Section 5.2.1). But the Ray Buffer must now support *two different data layouts*. Either they multiplex the decoder (adding latency) or they always allocate the larger format (wasting the claimed savings). The paper is silent on this.

2. **Inter-CU NoC traffic increase is glossed over:** Every RTA-handled translation requires a round-trip through the inter-CU NoC (Figure 9). With 65%+ of translations offloaded to RTAs (Figure 23), this is substantial new traffic. The paper tests NoC latency sensitivity (Figure 29) but not NoC bandwidth saturation.

3. **The L1S "access mask register" is per-SA, not per-CU:** Figure 13 shows a single mask register per L1S cache. Since L1S is shared by all CUs in a Shader Array, this means *all* RTAs in that SA share the same reserved cache way. With 4 CUs per SA (128 CUs / 32 SAs from Figure 9), this is only ~4KB reserved per SA for page tables. The paper doesn't discuss thrashing between RTAs in the same SA.

4. **PDU latency is not specified:** Section 5.2.4 describes the PDU logic (comparators, adder, shifter) but never states the cycle count. BVH node decoding in RTAs is pipelined over multiple cycles; is PDU single-cycle? The paper assumes it integrates into the existing pipeline but doesn't specify where the critical path is.

5. **The "128 RTAs" headline is misleading:** Each RTA can handle 4 concurrent warps (Table 2, "RTA Max Warp = 4"). But a translation request doesn't occupy a full warp – it's a single address. The paper doesn't clarify how many concurrent translations each RTA can actually handle. If it's 4, you effectively have 512 "translation slots," not 128.

6. **Lookahead buffer eviction policy is LRU by default, but 8-PTE packing creates alignment issues:** Section 6.3 packs 8 PTEs with consecutive VPNs (at fixed stride) per cache line. If the access pattern doesn't align with these strides, useful PTEs could be evicted with useless neighbors. The paper doesn't evaluate lookahead buffer pollution.

7. **Memory oversubscription results (Section 8.9, Figure 33) are suspiciously modest:** Only 1.21× speedup under 150% oversubscription. The paper attributes this to "page fault handling typically being the primary bottleneck" – but this contradicts their core claim that PTW bandwidth is the bottleneck. Under heavy faulting, the GMMU replay path (which Heliostat forces on faults) becomes the new hotspot.

8. **No discussion of memory consistency implications:** PTWs in the GMMU presumably have coherent access to page tables. RTAs accessing page tables via L1S (which is a read-only cache in baseline) raises questions. Section 7's "TLB/Lookahead buffer coherence" paragraph mentions flushing on page faults but doesn't discuss how concurrent page table updates (e.g., dirty bit setting) are handled if an RTA is mid-walk.