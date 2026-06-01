## Q1: Whiteboard Explanation

Let me break down XHarvest as if explaining to a colleague at the whiteboard.

**The Problem:** Modern SSDs are caught in a "cost-utilization dilemma." They pack expensive internal resources (ARM processors, 10GB+ DRAM for metadata) to handle peak I/O bursts, but Alibaba traces show these resources sit idle >96% of the time (Figure 3a). That's 30-40% of your SSD cost just... waiting.

**Prior Attempts:**
- *Open-Channel SSD (OCSSD):* "Just use the host!" — strips out SSD internals, runs the Flash Translation Layer (FTL) on the host CPU. Problem: contends with user applications for memory/CPU. Linux deprecated it in kernel 5.15 (Section 3.1).
- *DRAMless SSD:* Buffers metadata in host memory via DMA. Problem: 4KB DMA granularity doesn't match 8-byte mapping entries, causing cache pollution and 95.81% FTL throughput degradation (Figure 6).

**XHarvest's Insight:** Don't statically allocate — *dynamically harvest*. Keep modest internal resources (25% compute, 10% memory) for normal operation. When I/O bursts hit, borrow host resources on-demand.

**The Architecture (Figure 7):**
1. **CPU Harvesting:** Run firmware in an Intel SGX enclave on the host. Protects proprietary FTL algorithms from leakage while leveraging powerful x86 cores.
2. **Memory Harvesting:** Use CXL.mem to build a *unified* FTL cache spanning host EPC and SSD DRAM. Fine-grained (cacheline) access eliminates DMA's granularity mismatch.
3. **Secure Communication:** CXL 3.1's TEE Security Protocol (TSP) encrypts flit-level traffic between enclave and SSD. Mutual authentication prevents rogue access to SSD DRAM.
4. **Dynamic Launch:** A load detector in the SSD triggers enclave activation within 5ms when load exceeds 60% threshold (Section 5.4).

**Key Mechanism — CXL-driven Communication (Figure 9):** Instead of costly ecall/ocall transitions (20K+ cycles each), the enclave polls a ring buffer in SSD DRAM via secure CXL traffic. Message passing at 64-byte granularity achieves 3.7M OPS throughput (Figure 24).

---

## Q2: The Key Insight

The central insight is a **temporal decoupling observation**: I/O bursts are *rare* and *non-simultaneous* across SSDs.

From Section 3.3 and Figure 3c: Only 2.20% of runtime sees more than 5 VMs with >50% I/O utilization simultaneously. Meanwhile, Figure 3d shows that when SSDs hit 85-100% load, host CPU utilization is actually *low* (45.69% in "Low" category) due to CPU waiting on I/O.

This leads to the core design principle: **"Harvest idle host resources dynamically, rather than statically reserving expensive SSD-internal resources."**

The corollary insight is that CXL's cache-coherent, cacheline-granular semantics finally make host-SSD collaboration viable. Prior approaches failed because:
- PCIe separates cache-coherency domains (Section 3.2)
- DMA's 4KB granularity misaligns with 8-byte FTL entries
- OS stack latency (10μs) dwarfs CXL memory access (200ns)

By combining CXL.mem for unified memory and CXL 3.1 TSP for secure traffic, XHarvest achieves what OCSSD couldn't: efficient harvesting without security compromise or granularity mismatch.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Real Workload Characterization:** The Alibaba cluster analysis (Figure 3a-d) provides compelling motivation with 8-day traces from 4,000 servers. The 96.64% under-25% utilization statistic is damning for conventional SSD economics.

2. **Comprehensive Baseline Comparison:** They compare against ConvSSD, OCSSD, DLSSD, and multiple XHarvest ablations (Base, Base+CPU), allowing readers to attribute gains to specific mechanisms. Figure 12-13 show both micro and macro benchmarks.

3. **Real Application Validation:** The Rocksdb (Figure 16a) and Terasort (Figure 16b-c) experiments demonstrate practical benefits under memory contention scenarios, not just synthetic I/O patterns.

4. **Latency Breakdown Analysis:** Figure 20's decomposition into FTL/Data/Firmware components with per-request metadata traffic quantifies *why* CXL helps — XH-Host cuts FTL latency by 84.03% at 100% hit ratio versus DLSSD.

5. **Cost Modeling Transparency:** Figure 17 uses cited market prices (references [50,65,70,73,74,87,108]) for NAND ($5.248/128GB), DRAM ($7.5/GB), controllers ($23), showing 31.50% savings for 1TB SSDs.

### Weaknesses

1. **CXL Emulation via NUMA (Critical):** Section 5.5 admits: "We use NUMA to emulate the CXL.mem/cache, mirroring CXL performance." Cross-NUMA latency (75ns) is claimed to match "final latency targets of CXL specification," but this elides CXL controller overhead, protocol translation costs, and real flit scheduling. Figure 25's sensitivity analysis using Sniper helps, but the primary results lack cycle-accurate CXL modeling.

2. **TSP Overhead Omitted:** Section 6.1 explicitly states: "due to the lack of ready-to-integrate hardware, we have to overlook the marginal overhead of secure CXL traffic." This is problematic since TSP involves AES-GCM encryption per flit — the claimed "5% latency overhead" (Section 5.2) is from literature, not measured.

3. **SGX Enclave Configuration Assumptions:** Table 1 lists "SGX 2.25" but doesn't specify EPC size limits. Modern SGX has 128MB-256MB EPC; exceeding it triggers costly paging. The 128MB FTL cache used in experiments (Section 6.1) conveniently fits, but scaling to 16TB SSDs (requiring proportionally larger caches per Section 2) would stress EPC limits.

4. **Single-SSD Focus:** Figure 15 examines multi-SSD scaling but only up to 4 SSDs. The claim that "XHarvest requires at most 8 CPU cores to saturate all SSDs" for 24-SSD servers (Section 6.2) is extrapolated, not demonstrated.

5. **No GC/WL Stress Testing:** While GC and wear-leveling are mentioned as firmware tasks (Section 2), the evaluation focuses on steady-state I/O. No experiments show behavior during GC-intensive phases when background traffic spikes unpredictably.

6. **Artifact Availability:** The GitHub link (https://github.com/ChaseLab-PKU/XHarvest) is provided but there's no mention of whether the emulation environment is Dockerized or includes reproduction scripts.

---

## Q4: What the Authors Didn't Tell You

1. **The Polling Power Cost:** Section 5.1 states the enclave is "pinned to a single host CPU core to continuously process I/O requests." Figure 22 shows this core at 60-80% utilization during bursts. But what about *idle* power? A polling core at 0.1% utilization (Section 5.4) still burns power. They claim "negligible CPU consumption" but don't quantify it in their energy model (Figure 18).

2. **EPC Pressure Under Contention:** The evaluation carefully allocates exactly 128MB host memory matching DLSSD (Section 6.1). But SGX EPC is a shared system resource. If other enclaves (databases, ML inference) compete for EPC, XHarvest's FTL cache would shrink or incur paging. This multi-tenant scenario is never explored.

3. **Warm-up Period for FTL Cache:** Section 5.3 describes LRU-managed caching of translation pages. But there's no analysis of cache warm-up time after dynamic enclave launch. The 5ms launch overhead (Figure 23a) doesn't include populating the FTL cache from cold state.

4. **The 75ns CXL Latency Assumption:** This "final latency target" from CXL Specification (cited as reference [15]) is aspirational. Current CXL 1.1/2.0 devices measured by Sun et al. [101] show 170-300ns real latencies. The gap between specification targets and silicon reality could significantly impact Figure 19-20's conclusions.

5. **Firmware Complexity in Enclave:** They claim only 3K LOC for the enclave (Section 5.5), but porting real vendor firmware (with proprietary GC algorithms, wear-leveling heuristics, error handling) into SGX's restricted environment is non-trivial. SGX prohibits syscalls, limits threading, and has strict memory layout requirements. The "open-source firmware" they test isn't representative of production complexity.

6. **CXL Flit Size Mismatch:** Section 5.2 configures 64-byte messages, but CXL flits are 68 bytes (256-bit data + metadata). The protocol overhead for flit framing and TSP MACs (12 bytes per transfer) accumulates — their claimed 256B per request communication (Section 6.3) may undercount actual link utilization.

7. **No Comparison with ZNS SSDs:** Section 7 mentions ZNS as a cost-efficient alternative but dismisses it due to "append-only write constraint." Given ZNS is actually shipping in production (Samsung, WD), a performance comparison would be informative.