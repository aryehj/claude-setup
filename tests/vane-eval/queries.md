# Vane Eval Query Set

Six research-style queries spanning different cognitive demands. Slugs q1–q6 are
stable identifiers used as filename fragments in eval output.

---

## Q1: tritium-half-life

**Query:** What is the half-life of tritium, and what are its primary civilian
applications?

**Reference:** Tritium (hydrogen-3) has a half-life of approximately 12.32 years,
decaying by beta emission to helium-3. Its primary civilian applications include
self-luminous EXIT signs and watch dials (where the beta particles excite a
phosphor coating), neutron generators used in oil-well logging, and as a tracer in
hydrological studies to date groundwater. It is also a key fuel component in
deuterium–tritium fusion reactions being developed for commercial power generation.
Because of its relatively short half-life and low-energy beta emission, it is
considered one of the least hazardous radioactive isotopes in external exposure
scenarios, though ingestion or inhalation remains a concern.

**Key facts:**
- Half-life ≈ 12.32 years
- Decays by beta emission to helium-3
- Used in self-luminous devices (EXIT signs, watch dials)
- Tracer in hydrology / groundwater dating
- Fuel component in D–T fusion reactors
- Low external hazard due to weak beta, but internal exposure still a concern

---

## Q2: transformer-kv-cache

**Query:** Explain how the key-value cache works in transformer inference and why it
reduces computational cost.

**Reference:** During autoregressive transformer inference, each new token attends to
all previous tokens. Without caching, the full attention computation over the
context must be recomputed for every new generated token, scaling as O(n²) in the
sequence length. The key-value (KV) cache solves this by storing the key and value
projections for every previously processed token so that when generating token t, the
model only computes new K/V projections for the single new token and appends them to
the cache; the cached projections for tokens 0…t-1 are reused directly. This reduces
the per-step attention computation from O(n²) to O(n) (one dot-product row instead
of a full matrix), at the cost of VRAM proportional to batch_size × seq_len ×
num_layers × 2 × head_dim × precision. Prefill (processing the prompt in parallel)
still runs the full O(n²) computation once. Techniques like multi-query attention,
grouped-query attention, and paged attention address the memory pressure that grows
with the cache.

**Key facts:**
- Caches K and V projections for all previous tokens
- Reduces per-step attention from O(n²) to O(n)
- Memory cost scales with batch × context length × layers × head_dim
- Prefill phase still O(n²); cache only helps the decode phase
- Multi-query/grouped-query attention reduce cache memory footprint
- Paged attention (vLLM) virtualises the cache to reduce fragmentation

---

## Q3: iron-curtain-speech-context

**Query:** Who gave the "Iron Curtain" speech, when and where was it delivered, and
what political circumstances made it historically significant?

**Reference:** Winston Churchill delivered the "Sinews of Peace" speech — popularly
known as the "Iron Curtain" speech — on 5 March 1946 at Westminster College in
Fulton, Missouri, United States, with President Harry S. Truman seated on the stage.
Churchill, then out of office as British Prime Minister (he had been defeated in the
1945 general election), used the address to describe the division of Europe:
"From Stettin in the Baltic to Trieste in the Adriatic, an iron curtain has descended
across the Continent." The speech was significant for several reasons: it was one of
the earliest prominent public articulations of the emerging Cold War division between
Soviet-dominated Eastern Europe and the Western democracies; it called for an
Anglo-American alliance to counter Soviet expansionism at a time when the wartime
alliance with the USSR was fraying; and it provoked sharp criticism from both
Stalin — who compared Churchill to Hitler — and from American progressives who saw it
as inflammatory. Historians generally regard it as a landmark in the rhetorical
opening of the Cold War.

**Key facts:**
- Speaker: Winston Churchill (former UK Prime Minister, out of office)
- Date: 5 March 1946
- Venue: Westminster College, Fulton, Missouri, USA
- Truman was present on stage
- Phrase used: "an iron curtain has descended across the Continent"
- Described division from Stettin (Baltic) to Trieste (Adriatic)
- Called for Anglo-American alliance against Soviet expansion
- Stalin compared Churchill to Hitler in response
- Regarded as an early landmark articulation of Cold War division

---

## Q4: merkle-tree-blockchain

**Query:** How does a Merkle tree enable efficient and tamper-evident transaction
verification in a blockchain, and what is a Merkle proof?

**Reference:** A Merkle tree is a binary tree of cryptographic hashes. Leaf nodes
contain the hashes of individual transactions; each internal node is the hash of its
two children; and the root (Merkle root) is a single hash that commits to the entire
transaction set. In a blockchain, the Merkle root is embedded in the block header,
so the entire transaction set is implicitly committed to by the proof-of-work on that
header. Tamper-evidence follows directly: altering any single transaction changes its
leaf hash, which cascades up to change every ancestor hash and ultimately the root,
invalidating the header's hash. A Merkle proof (also called a Merkle path or audit
proof) lets a light client verify that a specific transaction is included in a block
without downloading all transactions. The proof consists of the sibling hashes along
the path from the leaf to the root. The verifier recomputes the root hash by hashing
the target transaction with the provided siblings up the tree; if the result matches
the header's Merkle root, inclusion is proven. This reduces verification from O(n)
data to O(log n) hashes.

**Key facts:**
- Leaf nodes = hashes of individual transactions
- Each internal node = hash(left_child ∥ right_child)
- Merkle root committed to in the block header
- Altering any transaction cascades a hash change up to the root
- A Merkle proof = sibling hashes on the path from leaf to root
- Proof size is O(log n) for n transactions
- Light clients can verify inclusion without downloading the full block

---

## Q5: llm-context-length-2024

**Query:** What context-length milestones did large language models reach in 2024,
which providers or models achieved them, and what are the practical limitations that
persist despite large context windows?

**Reference:** Through 2024 several providers pushed context windows dramatically:
Google's Gemini 1.5 Pro launched with a 1-million-token context window (later
extended in preview to 2 million tokens), Google's Gemini 1.5 Flash also supported
1 million tokens, and Anthropic's Claude 3 models (Haiku, Sonnet, Opus) offered
200,000-token contexts. OpenAI's GPT-4o supported 128,000 tokens. These were
production-available figures; research models explored even longer windows. Despite
headline numbers, practical limitations persist: "lost in the middle" degradation
means retrieval accuracy for facts buried in the middle of a long context is
substantially worse than for facts at the beginning or end; inference cost and
latency scale superlinearly with context length for full-attention models; KV-cache
memory becomes the binding constraint at large batch sizes; and most models were
trained with shorter contexts and fine-tuned to extend them (e.g. via RoPE scaling),
which can degrade quality on the full window compared to training length.

**Key facts:**
- Gemini 1.5 Pro: 1 million tokens (2 million in preview)
- Gemini 1.5 Flash: 1 million tokens
- Claude 3 family: 200,000 tokens
- GPT-4o: 128,000 tokens
- "Lost in the middle" effect: middle-of-context retrieval degrades
- Inference cost/latency scales poorly with full-attention at large context
- KV-cache memory is the binding constraint at large batch + long context
- Many models use RoPE or ALiBi scaling to extend beyond training length

---

## Q6: research-tool-tradeoffs

**Query:** What are the core trade-offs between using a RAG (retrieval-augmented
generation) system and a long-context LLM for enterprise knowledge-base querying,
and under what conditions should you prefer one over the other?

**Reference:** RAG and long-context LLMs represent different points on the
accuracy-vs-cost-vs-latency surface for knowledge retrieval. RAG retrieves a small
number of relevant chunks, keeping per-query token count low and costs predictable,
but introduces a retrieval quality bottleneck: a missed or mis-ranked chunk makes the
answer wrong regardless of how good the generator is. Long-context models sidestep
retrieval by attending over the entire corpus at once, but cost and latency scale
with context length, and "lost in the middle" degradation means very long contexts
don't always outperform RAG at fact lookup. The practical choice depends on corpus
size, query type, and update frequency: for corpora larger than a few million tokens,
fitting the full corpus in context is economically infeasible at scale, so RAG wins
on cost; for corpora that must be current (frequently updated), RAG with a live index
is much cheaper to maintain than re-running prefill on every query; for
single-document Q&A where the document fits in context, long-context models can
outperform RAG by avoiding chunking artifacts; for multi-hop queries that require
synthesising widely separated facts, long-context models handle implicit co-reference
better, while RAG requires explicit multi-hop retrieval strategies (iterative
retrieval, sub-question decomposition) to match. Hybrid approaches that use
embedding-based recall to populate a long context are a common middle ground.

**Key facts:**
- RAG keeps per-query tokens low; cost is predictable
- RAG has a retrieval quality bottleneck: missed chunks → wrong answer
- Long-context models avoid retrieval but cost/latency scale with context
- "Lost in the middle" limits long-context reliability at fact lookup
- RAG is preferred when corpus > practical context limit or updates are frequent
- Long-context preferred for single-document deep Q&A
- Multi-hop synthesis often favours long-context or iterative RAG
- Hybrid (embedding recall → populate context) is a common production pattern
