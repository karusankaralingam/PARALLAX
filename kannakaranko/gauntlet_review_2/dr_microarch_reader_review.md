# MagiCache: The Whiteboard Explanation

Alright, let's cut through the marketing language and understand what's actually happening here.

## The Core Problem They're Solving

Existing in-cache computing architectures (like EVE, Neural Cache, Duality Cache) take your L2/L3 cache and **statically** carve it into two pieces:
- **Computing arrays**: SRAM rows that do bit-line computation (AND/NOR operations by activating multiple wordlines simultaneously)
- **Storage arrays**: Regular cachelines for the processor

The problem? If you allocate 50% of your cache to "computing arrays" and your application only uses 2 out of 32 vector registers, you've just wasted ~94% of that computing space. Those rows sit there doing nothing but can't be used as cache either.

## The "Aha!" Moment: Cacheline-Level Granularity

Here's the clever insight: **Why partition at the array level when you can partition at the row level?**

```
EVE (Array-Level):                    MagiCache (Row-Level):
┌─────────────────┐                   ┌─────────────────┐
│ Computing Array │ ← All 256 rows    │ Row 0: cacheline│
│ (v0-v31 fixed)  │   are computing   │ Row 1: v0 seg   │ ← Computing
├─────────────────┤                   │ Row 2: cacheline│
│ Storage Array   │ ← All rows are    │ Row 3: v1 seg   │ ← Computing
│ (cachelines)    │   cachelines      │ Row 4: cacheline│
└─────────────────┘                   │ ...             │
                                      └─────────────────┘
```

The trick is adding **two bits to each tag entry**:
1. **Computing bit (C)**: Is this row a computing line or a cacheline?
2. **Presence bit (P)**: For cache coherence (who owns this data?)

That's it. That's the hardware "magic." Two bits per tag entry.

## The Virtual Register Mapping Table (VRMT)

This is where the actual bookkeeping happens. It's a 32×Q table where:
- 32 rows = 32 architectural vector registers (v0-v31)
- Q columns = number of "segments" per register (each segment = one cacheline width)

Each entry stores: `{valid_bit, row_index}`

```
VRMT[v0][segment_1] = {1, row_3}  // v0's first segment is at row 3
VRMT[v0][segment_2] = {1, row_7}  // v0's second segment is at row 7
VRMT[v1][segment_1] = {0, X}      // v1 not allocated yet
```

**Key insight**: They use **lazy initialization**. Vector registers are only allocated when actually used by an instruction. If your code only touches v0 and v1, that's all that gets allocated. The rest stays as cache.

## The Data Layout Choice: Bit-Parallel

This is important and often glossed over. They chose **bit-parallel** layout (all bits of one element on the same wordline) instead of bit-serial (bits transposed across wordlines).

Why? Because bit-parallel has the **same layout as regular cachelines**. This means:
- No transpose operation needed when converting between cacheline ↔ computing line
- A row can switch roles with just tag bit manipulation
- Simpler coherence handling

The tradeoff: Bit-serial has higher throughput for arithmetic, but bit-parallel enables their whole "fused array" concept.

## Instruction Chaining: Hiding Memory Latency

The second contribution is about **asynchronous execution across arrays**.

In the baseline (EVE), all arrays must synchronize after every instruction:
```
Array 0: [LOAD v0]──────────────────[SYNC]──[COMPUTE]──[SYNC]
Array 1: [LOAD v0]──────────────────[SYNC]──[COMPUTE]──[SYNC]
Array 2: [LOAD v0]──[MSHR stall]────[SYNC]──[COMPUTE]──[SYNC]
Array 3: [LOAD v0]──[MSHR stall]────[SYNC]──[COMPUTE]──[SYNC]
```

With instruction chaining, arrays can proceed independently:
```
Array 0: [LOAD v0]──[COMPUTE]──[STORE]──────────────────[SYNC]
Array 1: [LOAD v0]──[COMPUTE]──[STORE]──────────────────[SYNC]
Array 2: [LOAD v0]──[MSHR stall]──[COMPUTE]──[STORE]───[SYNC]
Array 3: [LOAD v0]──[MSHR stall]──[COMPUTE]──[STORE]───[SYNC]
```

Synchronization only happens at "group boundaries" (configuration instructions, permutation instructions, or conflicting memory addresses).

---

## The Skeptic's Check

### 1. The VRMT Size Claim
They claim 4.5 KB for the VRMT. Let's verify:
- 32 registers × Q segments × (1 + log₂(256)) bits = 32 × Q × 9 bits
- For Q = 128 segments (to get 65536-bit vectors with 512-bit cachelines): 32 × 128 × 9 = 36,864 bits = 4.5 KB ✓

This checks out, but note this is **per L2 cache slice**. In a multi-core system with shared L2, you need one per core or complex arbitration.

### 2. The "8.9% Area Overhead" for Fused Arrays
They claim 8.9% area overhead for the peripheral circuits (logic layer, add layer, shift layer, etc.). This is **on top of** the vanilla SRAM array.

But wait—they're comparing against EVE which already has these circuits on half the arrays. So the *incremental* overhead is:
- EVE: 50% of arrays have ~17.7% overhead → ~8.9% total
- MagiCache: 100% of arrays have ~8.9% overhead → ~8.9% total

They're roughly equivalent in area, but MagiCache gets **2× the computing parallelism** (all 32 arrays can compute vs. 16 in EVE).

### 3. The FFA Allocation Policy
They use "Find-First-Available" instead of LRU for allocating computing lines. They claim <1% miss rate increase.

**Red flag**: FFA scans 32 cachelines per cycle to find a free one. That's 32 tag comparisons in parallel. In a 256-row array, worst case is 8 cycles. This is non-trivial logic, and they don't break down its area/power cost.

### 4. The 1.19x-1.61x Speedup
Looking at Figure 8, the speedup varies wildly:
- **matmul**: 1.61x (best case) — high arithmetic intensity, few registers
- **backprop**: 1.19x (worst case) — strided accesses kill performance

For strided accesses (backprop, k-means), the MSHR bottleneck dominates. Instruction chaining can't help much because requests from different arrays hit different cachelines, so you can't overlap them effectively.

### 5. The Coherence Overhead
They mention adding a "presence bit" for coherence but don't quantify the snoop traffic. When a vector instruction accesses data owned by L1, they must:
1. Send snoop request to L1
2. Wait for L1 to respond (and possibly writeback)
3. Invalidate L1 copy
4. Then serve the vector request

This is **not free**, especially for applications with mixed scalar/vector access patterns.

---

## Discussion Questions

1. **What happens when the working set exceeds L2 capacity?** Their instruction chaining helps hide latency, but with 32 MSHRs and 2048-element vectors, you're still looking at 64 batches of memory requests. At what point does the MSHR become the bottleneck regardless of chaining?

2. **Why L2 and not L3?** Duality Cache and Neural Cache target L3. MagiCache targets L2. The tradeoff is latency vs. capacity. With their lazy allocation scheme, would L3 make more sense for larger working sets?

3. **The bit-parallel choice limits throughput.** They acknowledge bit-serial has higher throughput. For applications like neural network inference (their motivating example), is the flexibility of fused arrays worth the throughput loss?

4. **Context switch overhead**: They mention storing/restoring only valid registers, but what's the actual latency? If a process uses 8 registers × 128 segments × 512 bits = 64 KB of vector state, that's a significant memory operation.