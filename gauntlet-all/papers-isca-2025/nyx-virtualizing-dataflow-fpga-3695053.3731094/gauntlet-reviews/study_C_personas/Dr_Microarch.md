## Q1: Whiteboard Explanation

Let me draw out how Nyx actually works at the hardware level.

**The Problem Setup:**
Existing FPGA virtualization systems (Coyote, Nimblock, etc.) partition the FPGA into Partially Reconfigurable Regions (PRRs) and let multiple tenants share them. But they operate in a **task-parallel** model: Task A1 runs, completes, writes to DRAM, then Task A2 loads the data and runs. There's a serialization penalty between dependent tasks.

Traditional dataflow on FPGAs is the opposite: you wire Task A1 → FIFO → Task A2 at synthesis time, and data streams directly. But this requires **static allocation** of the entire FPGA to one application—incompatible with multi-tenancy.

**Nyx's Core Trick: Virtual FIFOs in DRAM**

Looking at Figure 4 and Figure 5, here's the wiring:

1. **Dataflow Proxy** (per PRR): A shim module at each reconfigurable region's interface. It intercepts all streaming output from the user logic. Critically, it **hides the existence/state of downstream tasks** from the producer. The producer task thinks its consumer is always ready.

2. **Virtual FIFO Controller** (in static region): One controller per VFIFO channel. Each manages a dedicated address space in FPGA DRAM that acts as a circular buffer. The controller implements a handshake protocol:
   - **Initial handshake**: When Task A1 (producer) is configured, the FPGA Manager assigns a VFIFO channel and writes the parent task's ID to the controller.
   - **Data flow**: A1's Dataflow Proxy streams data out. If A2 (consumer) isn't ready, the controller writes to DRAM. If A2 is present, data passes through directly.
   - **Completion handshake**: When A2 is configured, the Manager looks up the DAG, finds the matching VFIFO, and notifies the controller to start feeding stored data.

3. **Crossbar Routing** (Figure 4): A VFIFO Crossbar connects all Dataflow Proxies to all VFIFO Controllers, and a Memory Crossbar connects controllers to DDR channels. This is the "glue" that allows any PRR to talk to any VFIFO.

**The Scheduling Dance:**

The FPGA Hypervisor (running on the host CPU) maintains:
- A DAG per application
- A resource database (which PRRs are free)
- A bitstream database

Since ICAP (Internal Configuration Access Port) is serial—only one PRR can be reconfigured at a time—the hypervisor must carefully sequence task launches. The key insight: **a producer can run to completion without its consumer being present**. Data safely buffers in the VFIFO's DRAM space. This decouples reconfiguration scheduling from data dependency.

**Data Path Example (from Figure 5):**

Task A1 runs, produces data → Dataflow Proxy → VFIFO Controller (channel assigned, stores to DDR Channel 0). Task A1 completes and vacates PRR. Later, Task A2 is configured on PRR 0. FPGA Manager tells the VFIFO Controller "consumer A2 is ready." Controller starts feeding buffered data to A2's Dataflow Proxy. Meanwhile, Task A3 can be configured on PRR 2 and immediately start receiving A2's output through a *different* VFIFO channel—achieving task pipelining.

---

## Q2: The Key Insight

**The "Magic Trick":** Nyx uses **DRAM-backed virtual FIFOs with decoupled handshake semantics** to enable dataflow-style streaming between tasks that are *not simultaneously present* on the FPGA.

The crucial architectural insight is this: In traditional dataflow, the FIFO between producer and consumer provides backpressure—if the consumer stalls, the FIFO fills, and the producer stalls. This requires both endpoints to exist. Nyx breaks this coupling by:

1. **Making the producer oblivious**: The Dataflow Proxy always accepts data. The producer never stalls due to consumer absence—data just goes to DRAM.

2. **Using DRAM as an "infinite" FIFO**: This converts a spatial dependency (both tasks must be wired together) into a temporal dependency (producer can finish before consumer starts).

3. **Deferring the consumer-side handshake**: The VFIFO Controller holds data until the FPGA Manager signals that the consumer is configured and ready.

This is structurally different from prior work (Coyote, Nimblock) in one key way: those systems treat inter-task data as **bulk transfers** (write to memory, read from memory). Nyx treats it as **streaming through a persistent channel** that survives task reconfiguration. The difference is subtle but critical—Nyx can start feeding data to a newly-configured task *immediately* without an explicit DMA setup, because the channel was already established.

The specific hardware enabler is the **per-channel VFIFO Controller** (Section 3.3) which maintains read/write pointers and the parent/child task IDs across reconfiguration events. It's essentially a stateful FIFO controller whose backing store is in DRAM rather than BRAM.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Comprehensive congestion scenarios** (Section 4.3, Figures 7-9): The evaluation spans Relaxed (1500ms delay), Standard (200ms), and Stressed (50ms) event arrival rates. This is methodologically sound—it shows Nyx's behavior across the utilization spectrum, not just a cherry-picked "sweet spot."

2. **Scheduler-agnostic gains**: Figure 7 shows improvements across five different scheduling policies (Non-Sharing, FCFS, RR, SJF, Nimblock). The 2x-2.75x geometric mean improvement under Stressed conditions isn't scheduler-specific—this suggests the benefit comes from the architecture, not a co-designed scheduler.

3. **Deadline violation analysis** (Figure 9): The authors sweep Deadline Factor from 0 to 20 with 0.01 granularity, showing Nyx reaches 10% violation threshold at DF=8.14 vs. Baseline's DF=15.79 (Standard scenario, SJF). This is a more meaningful metric for cloud SLA compliance than raw latency.

4. **Honest acknowledgment of limitations** (Section 4.2, Figure 6): Digit Recognition shows ~1x performance because its two tasks have vastly different execution times (seconds vs. milliseconds), leaving no overlap opportunity. Image Scaling converges to baseline at high batch sizes due to region contention. The authors don't hide these cases.

### Weaknesses

1. **Memory bandwidth assumption is unexamined**: Every VFIFO channel competes for DRAM bandwidth. The Alveo U250 has 4 DDR channels. Figure 4 shows VFIFOs spread across channels, but Section 4 never reports:
   - Achieved DRAM bandwidth under peak VFIFO contention
   - Impact of VFIFO traffic on application memory traffic (user kernels also need DRAM)
   
   This is a critical omission. If 8 VFIFOs are all active, each streaming at 300 MHz × 512 bits = 19.2 GB/s, you'd saturate the ~77 GB/s aggregate DDR bandwidth. The evaluation uses "up to 8 VFIFOs" but never stress-tests memory contention.

2. **Partial reconfiguration latency is treated as constant** (Section 3.1): "each bitstream has the same file size." This is only true if all PRRs are identically sized. The paper never states the bitstream size or the actual PR latency (typical values: 50-100ms for multi-MB bitstreams). Given that evaluation workloads have event intervals as low as 50ms, PR latency could dominate. The absolute numbers are missing.

3. **Fork/join overhead is significant but under-explored**: Table 3 shows Nyx (fork/join) uses 45.4% BRAM vs. 28.6% for base Nyx—a 60% increase. Yet only Optical Flow requires this, and the paper states this "opens a new direction for future research" (Section 4.5). This is a substantial limitation since many real DAGs have diamond patterns (e.g., ResNet skip connections).

4. **Eight PRRs is a design choice, not a limitation study**: The paper states "eight reconfigurable regions, though Nyx is applicable to other sizes" but never evaluates scalability. Optical Flow (9 tasks) already exceeds 8 PRRs, causing convergence to task-parallel (Figure 6). For larger workloads (e.g., DNN layers), this could be prohibitive.

5. **Hypervisor overhead is unquantified**: The scheduler runs on a 3.2 GHz i7 CPU. Section 3.2 describes handshake protocols, DAG lookups, and channel assignments. What's the cycle count for a scheduling decision? At 50ms event intervals with 8 PRRs and 5 pending apps, is the CPU a bottleneck?

---

## Q4: What the Authors Didn't Tell You

### 1. The BRAM Tax for VFIFO Controllers

Table 3 shows Nyx uses 28.6% BRAM vs. Baseline's 9.4%—a 3x increase. For the fork/join variant, it's 45.4%. The paper glosses over this as "justifiable" because it "minimizes execution time."

Let's unpack what's in those BRAMs:
- Each VFIFO Controller needs storage for read/write pointers, task IDs, and FIFO state machines
- The Crossbar (Figure 4) connecting N PRRs to M VFIFOs is an N×M switching fabric—likely implemented with BRAM-based routing tables
- On U250, 28.6% BRAM is ~540 out of 1920 UltraRAM + BRAM blocks

This is significant. Those BRAMs can't be used by user logic. A user kernel wanting to cache data on-chip has 19% fewer resources available.

### 2. The DRAM Latency Penalty

The paper sells VFIFOs as "seamless communication" but never quantifies the latency cost. Traditional on-chip FIFOs have 1-2 cycle latency. DRAM access is ~100ns at best (DDR4-2400, tRC ≈ 45ns, plus controller overhead).

When producer and consumer are both present, Section 3.3 states: "the virtual FIFO controller acts as an intermediate station, simply passing the data." But *where* does this "pass through" happen? If every data word still touches DRAM (for consistency), you're adding ~300 cycles of latency at 300 MHz per data transfer. The paper never clarifies whether there's a bypass path for co-scheduled tasks.

### 3. Single ICAP is Still the Elephant

Partial reconfiguration on Xilinx is fundamentally serial. ICAP has one port. The paper acknowledges this ("the only remaining overhead is the inherent serial nature of partial reconfiguration") but the scheduling algorithms don't seem to optimize for it.

Consider: With 8 PRRs and 9-task applications, you can only reconfigure one task per PR-latency interval. If PR takes 50ms (plausible for ~5MB bitstreams), configuring all 9 tasks sequentially takes 450ms before any dataflow benefit kicks in. The evaluation's "Stressed" scenario has 50ms event *arrival* intervals—this seems incompatible with realistic PR latencies.

### 4. The DAG Model is Feed-Forward Only

Table 2 shows all benchmarks are "feed-forward, represented as directed acyclic graphs." The VFIFO model assumes unidirectional streaming: producer writes, consumer reads.

What about:
- Iterative algorithms (convergence loops)?
- Bidirectional communication (A sends to B, B sends back acknowledgments)?
- Dynamic task graphs (task spawning)?

None of these are supported. The fork/join extension (Section 4.5) is admitted to be expensive and limited. Real-world ML workloads often have skip connections (ResNet), attention (transformers), or recurrence (LSTMs), which would require complex VFIFO topologies.

### 5. No Power Numbers

FPGAs are touted for power efficiency. Nyx adds:
- 8 Dataflow Proxies (per-PRR shim logic)
- 8+ VFIFO Controllers
- Crossbar switching fabric
- Continuous DRAM access for VFIFO traffic

What's the incremental power draw? On a 225W TDP Alveo U250, even 10W extra is significant for cloud economics. The paper is silent on this.

### 6. The Scheduler is Black-Boxed

Section 3.2 says Nyx "can seamlessly integrate any existing FPGA scheduling algorithms." But the VFIFO model introduces new scheduling dimensions:
- Which VFIFO channel to assign (affects memory bank contention)?
- When to flush a VFIFO vs. wait for consumer (memory pressure)?
- How to prioritize tasks whose VFIFOs are filling up?

The evaluated schedulers (FCFS, RR, SJF, Nimblock) weren't designed for dataflow-aware decisions. A scheduler co-designed with VFIFOs could potentially do much better—or expose pathological cases. This is unexplored.