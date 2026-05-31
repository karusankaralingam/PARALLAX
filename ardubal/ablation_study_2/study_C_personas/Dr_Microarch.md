# Qtenon: Architecture Deconstruction

## Q1: Whiteboard Explanation

Let me walk you through what's actually happening in this system, because the block diagrams hide the real wiring.

**The Problem They're Solving:**
Hybrid quantum-classical algorithms (like VQE, QAOA) require tight iteration loops: run quantum circuit → measure → classical optimization → update parameters → repeat. Current systems use an FPGA over Ethernet to control the quantum chip, with communication latencies of 1-10ms per round trip (Table 1). When quantum execution is only 7.9% of runtime (Figure 1b), you're burning 90%+ on communication and host computation.

**The Core Architecture (Figure 4):**

The "magic" is conceptually simple: they welded the quantum controller directly onto a RISC-V chip using two existing interfaces:

1. **RoCC Interface (datapath ❶):** This is the Rocket Custom Coprocessor interface—a standard way to attach accelerators to Rocket/BOOM cores. It gives you single-cycle register-to-accelerator transfers for 64-bit values. They use this for the `q_update` instruction to push parameter updates directly from CPU registers to the quantum controller cache.

2. **TileLink Bus (datapaths ❷❸):** The standard SiFive coherent interconnect. They hang the quantum controller cache off this like another L1 cache, at the same hierarchy level. This enables bulk transfers via `q_set` and `q_acquire`.

**The Memory Organization (Table 2, Figure 4):**

The quantum controller cache is ~5.66MB of SRAM organized into five segments:
- `.program`: 520KB — stores gate definitions (64 qubits × 1024 entries × 65 bits)
- `.pulse`: 5MB — the actual DAC waveforms (64 qubits × 1024 entries × 640 bits)
- `.measure`: 40KB — readout results
- `.slt`: 112KB — Skip Lookup Table (the pulse cache tags)
- `.regfile`: 4KB — frequently-updated parameters

The 2D organization assigns each qubit its own address chunk. Qubit 0's program lives at 0x0-0x3ff, qubit 1 at 0x400-0x7ff, etc. This eliminates encoding qubit indices in every instruction.

**The Pipeline (Figure 6):**

Four stages for pulse computation:
- Stage 1: Fetch instruction from Program Index Buffer
- Stage 2: Decode, fetch from regfile if needed, check SLT
- Stage 3: Pulse generation (8 PGUs in parallel)
- Stage 4: Write to pulse cache

The Skip Lookup Table (Figure 7) is essentially a content-addressed cache that maps (gate_type, parameter) → pulse_address. If you've computed RY(π/2) before, don't recompute—just return the cached pulse address. This is their "quantum locality" exploitation.

**The ISA (Table 3, Figure 8):**

Five new instructions extending RISC-V:
- `q_update`: Register → Quantum Cache (1 cycle via RoCC)
- `q_set`: Host Memory → Quantum Cache (bulk, via TileLink)
- `q_acquire`: Quantum Cache → Host Memory (bulk)
- `q_gen`: Trigger pulse generation
- `q_run`: Execute quantum circuit N times

The key insight in the ISA: they treat the quantum program as *mutable data* rather than a static instruction stream. Parameters live in `.regfile` and get linked at runtime.

## Q2: The Key Insight

**The "Magic Trick":**

The paper calls it "tightly coupled architecture," but let me translate: **they're treating the quantum controller as a scratchpad-based accelerator attached via RoCC, and exploiting temporal locality in pulse computation via an associative cache (the SLT).**

The real cleverness is in the SLT design (Section 5.3, Figure 7). Most pulse generation is repeated—VQE updates one parameter at a time while 99% of gates stay identical. The SLT maintains a mapping:

```
hash(gate_type[3b] || parameter[4b]) → QAddress[30b]
```

When a gate parameter was previously computed, you skip the 1000-cycle PGU latency entirely. Table 5 shows computation requirement reductions of 96.8%-98.9% for gradient descent methods.

**What makes this actually work:**

1. **The RoCC path for parameter updates:** Single-cycle latency means updating one parameter takes ~1ns vs ~10ms over Ethernet. This enables incremental compilation—only recompute what changed.

2. **Unified address space:** The quantum controller cache is memory-mapped. The CPU can write program definitions directly without protocol overhead. No marshaling, no serialization.

3. **Batched transmission (Algorithm 1):** They buffer measurement results (K = ⌊256/64⌋ = 4 shots) before issuing a TileLink PUT. This amortizes bus overhead.

4. **Fine-grained synchronization (Section 6.2, Figure 9):** Instead of FENCE instructions that stall the entire pipeline, they implement a memory barrier query via RoCC. The CPU polls whether specific addresses have been written—non-blocking, 1-cycle query latency.

**The structural delta from baseline:**

Previous systems (eQASM, HiSEP-Q): CPU → Ethernet/USB → FPGA buffer → PGU → DAC

Qtenon: CPU → RoCC/TileLink → SRAM buffer → PGU → SerDes → DAC

The FPGA is gone. The network interface is gone. The quantum controller is now just specialized SRAM with address decode logic and 8 hardwired PGUs.

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Cycle-accurate simulation:** They used FireSim on Xilinx Alveo U200 (Section 7.1), which gives you real cycle counts rather than analytical models. The floorplan in Figure 10 shows actual implementation.

2. **Comprehensive breakdown:** Figure 13 shows exactly where time goes—from 204.3ms baseline to 18.1ms with full optimization. Figure 14 breaks down communication into individual instruction contributions.

3. **Both optimization methods:** Testing GD (many communication rounds, simple computation) and SPSA (few rounds, complex computation) covers the workload diversity well.

4. **Scalability analysis:** Figure 17 tests up to 320 qubits, showing linear scaling. They acknowledge the pin count limitation honestly.

5. **Software optimization ablation:** Figure 16 isolates memory consistency (2.5-2.8× gain) from scheduling (3.4-10.1× gain).

**Weaknesses:**

1. **The baseline is a strawman.** They compare against "Intel i9-14900K + 100GbE + FPGA" but assume ideal network conditions with no switching overhead (Section 7.1). Real quantum labs have cable losses, protocol overhead, and jitter. The 14.9× end-to-end speedup is against a hypothetically clean baseline.

2. **Quantum chip is simulated.** "We use simulator data obtained from Qiskit" (Section 7.1). The ADI interface to real qubits—with its timing constraints, calibration requirements, and noise—is completely abstracted. The 600ns measurement time (Section 7.1) is a fixed constant, not measured.

3. **The 1000-cycle PGU latency is asserted, not justified.** Section 7.1 says "enforced latency of 1000 cycles, approximating realistic operational times [14, 31]." But pulse generation complexity varies dramatically with gate type. DRAG pulses are more complex than simple Gaussians. This is a convenient simplification.

4. **No power or area numbers for the quantum controller.** They give a floorplan (Figure 10) but never report mm², watts, or even LUT counts for the FireSim implementation. The 5.66MB SRAM is substantial—that's larger than many L2 caches.

5. **Memory consistency overhead is hand-waved.** Figure 5 shows the RBQ/WBQ architecture, but the actual latency of the memory barrier query isn't characterized. They claim "single-cycle latency" (Section 6.2) but that's just the RoCC round-trip—the actual barrier check logic adds cycles.

6. **The 2GHz SerDes assumption is aggressive.** Section 5.2 claims "each data entry is put into ten parallel 64 bit buffers... then fed into a SerDes unit... at the target 2 GHz DAC frequency." That's 128 Gbps per qubit. At 64 qubits, you're looking at 8 Tbps of aggregate bandwidth. No discussion of SerDes power or implementation complexity.

7. **Instruction count comparison is misleading.** Table 1 claims ~285 instructions for Qtenon vs ~30,000 for HiSEP-Q. But these aren't the same thing—Qtenon instructions are RISC-V extended opcodes that trigger complex hardware state machines. HiSEP-Q instructions directly specify pulse timing. Apples to oranges.

## Q4: What the Authors Didn't Tell You

**The Hidden Hardware Tax:**

1. **5.66MB of SRAM at L1-equivalent latency.** This isn't free. In a 7nm process, this would be roughly 5-8 mm² of die area just for the quantum controller cache. For context, a Rocket core is ~0.5 mm². They've added 10-16× the core area in SRAM alone.

2. **The SLT is a CAM.** Figure 7 shows tag comparison across entries. With 2 sets × 128 entries × 64 qubits, you have 16,384 parallel comparisons happening. CAMs are power-hungry and area-expensive. The "Least Count" replacement policy (Section 5.3) requires reading all valid bits and counts simultaneously.

3. **The WBQ/RBQ complexity (Figure 5).** The 32-entry reorder buffer with tag-based dequeuing isn't trivial. They've essentially implemented a simplified version of out-of-order memory ordering just for the quantum controller interface.

4. **SerDes per qubit.** Each qubit needs serializers running at 2GHz to feed the DACs. At 64 qubits, that's 64 SerDes instances. These are typically synthesized as hard macros, not standard cells.

**What the evaluation doesn't show:**

1. **End-to-end with real quantum hardware.** Every number is from simulation. The actual signal integrity, calibration drift, and timing margin issues of driving real qubits at these speeds are unexplored.

2. **Comparison with other tightly-coupled approaches.** They compare against loosely-coupled FPGA systems but not against other ASIC proposals. QUASAR [5] is mentioned but not benchmarked against.

3. **Multi-program scenarios.** What happens when two processes want to run quantum circuits? There's no discussion of virtualization, time-sharing, or resource management.

4. **Error sensitivity.** Quantum gates require precise timing. If the multi-stage pipeline introduces jitter (from cache misses, bus contention, etc.), that degrades gate fidelity. No analysis of timing variance.

**The scalability cliff:**

Figure 17 shows linear scaling to 320 qubits, but the authors acknowledge (Section 7.5) two limits:
- Cache size: 320 qubits needs ~28MB of SRAM
- Pin count: 2 DACs × 320 qubits = 640 analog outputs

Modern high-end packages have ~2000-3000 pins total. With power/ground, test, and digital I/O, you're hitting pin limits around 200-300 qubits. The paper doesn't discuss interposer or chiplet solutions.

**The coherence model is incomplete:**

Section 6.2 describes a "soft memory barrier" but doesn't specify the consistency model. What happens if the CPU reads `.measure` while `q_run` is in progress? The paper says the RoCC query checks if "write request has been sent through system bus"—but that's not the same as "write has completed." There's a potential window for stale reads.

**The "incremental compilation" is oversold:**

Section 6.1 claims "recompilation overhead to less than 100ns in practice." But this only works when parameters are pre-designated with `reg_flag=1`. If the circuit structure changes (different gate sequence, different qubit connectivity), you still need full recompilation. The dynamic incremental compilation is really "pre-planned parameter substitution."