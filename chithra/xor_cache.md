# Title: The XOR Cache: A Catalyst for Compression (ISCA 2025)

## 1. Whiteboard explanation — You're explaining this paper's mechanism to a smart colleague who hasn't read it. Walk through what they built and how it works.

LLCs in inclusive caches can have low effective capacity due to redundancy across the cache hierarchy. This work leverages redundancy due to inclusion to perform cache compression by storing an XOR of two line pairs (say A and B) in the LLC, provided at least 1 of them is available in the higher levels of cache.

For example, during insertion of line B into LLC, if a pair A can be found (in the higher levels of cache) to colocate with, XOR is performed between A and B to get `A_XOR_B` and stored in the LLC, as long as A or B is present in the private cache. During a miss, the requestor of line B can use a copy of line A from the private cache to XOR A with `A_XOR_B` to get back B.

The line pairs A, B are chosen in such a way that A is similar to B, which reduces the entropy of the compressed line, and further intra-level compression can be done on the XOR-compressed result by other compression schemes to further compress the line. A mapping function is used to find the line pair to colocate with. The MSI protocol is redesigned to ensure that coherence is maintained and to aid the decompression step. To facilitate XOR compression, the tag array of LLC is decoupled from the data array and compression-related metadata is stored in the tag array.

This method is evaluated across multi-programmed and multi-threaded benchmarks to reduce LLC area by 1.93x, power by 1.92x, achieving energy-delay product reduction by 26.3%.

## 2. What is the key insight that makes it work? (The "aha" — not what they did, but why it works)

The key insight of this paper is to use the redundancy across the cache hierarchy in an inclusive cache for inter-line compression of pairs of cache lines within the LLC by performing a computationally inexpensive XOR operation between them. By selecting line pairs with the most similarity, they further increase scope for intra-line compression using available cache compression schemes. Either of the original lines can be retrieved from the XOR-ed value by XOR-ing with the other line. This is made possible by decoupling the LLC tag array and data array and by redesigning the MSI coherence protocol to add 1 more state.

## 3. What's the strongest aspect of the evaluation, and what's the weakest? (Methodology critique)

**Strengths:**
- The idea of using redundancy in inclusive caches for compression through inter-line and intra-line compression techniques is novel.
- The experiments covered are extensive and the results are drawn across multiple factors for various benchmarks. Sensitivity of the solution to various factors is studied, along with extensive design space exploration and profiling of the LLC for different compression algorithms, making the solution very well defined and stronger.

**Weakest Parts:**
- The paper considers the MSI protocol for coherence. But the latest industry standards are different — Intel uses MESIF; Arm, AMD, and Nvidia use MOESI protocols. Network traffic savings through silent evictions via the E state are not explored in this work, especially considering that the solution increases network traffic.
- From the results section, the claim that even though there is more network traffic, that will not be a problem in emerging chiplet-based systems is unsubstantiated without results.
- Possibility of leakage of information about private data through side-channel attacks on cache-based compression systems is not addressed in this paper.

## 4. What did the authors not tell you? (Hidden assumptions, missing comparisons, unstated limitations)

To avoid deadlock, this work requires an unblocking private cache controller and a blocking LLC controller. Blocking LLC controllers can have performance implications compared to non-blocking ones. During evaluation, the L3 configuration is not clarified to be blocking or non-blocking.

## 5. What's the connection to ideas outside this paper's scope? (Cross-domain links, broader implications)

- The area and subsequent power savings through XOR caching can help processors that run power-hungry AI workloads.
- The finding that the XOR of two similar lines can reduce entropy and boost other compression techniques can boost memory-side compression.
