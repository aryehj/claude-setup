# Vane Eval Query Set

Six research-style queries spanning different cognitive demands. Slugs q1–q6 are
stable identifiers used as filename fragments in eval output.

---

## Q1: us-presidential-election-2024

**Query:** What were the results of the 2024 United States presidential election —
who won, what was the Electoral College margin, and what happened with the popular
vote?

**Reference:** The 2024 U.S. presidential election was held on November 5, 2024.
Donald J. Trump (Republican) defeated Kamala Harris (Democratic), winning the
Electoral College 312 to 226. Trump also won the national popular vote — the first
Republican presidential candidate to do so since George W. Bush in 2004 — by roughly
1.5 percentage points. JD Vance was elected Vice President as Trump's running mate.
Harris had become the Democratic nominee in late July 2024 after President Joe Biden
withdrew from the race following his June 27 debate performance. Trump and Vance
were inaugurated on January 20, 2025, beginning Trump's second, non-consecutive term.

**Key facts:**
- Election date: 5 November 2024
- Donald Trump (R) defeated Kamala Harris (D)
- Electoral College result: 312 (Trump) to 226 (Harris)
- Trump also won the national popular vote (≈1.5 pp margin)
- First Republican popular-vote win since 2004
- JD Vance elected Vice President
- Harris replaced Biden as nominee in late July 2024
- Trump's second, non-consecutive term — inaugurated 20 January 2025

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

## Q3: medial-knee-pain-cycling

**Query:** A cyclist develops stubborn medial knee pain that comes on during long
rides and lingers for days afterward. What are the most likely diagnoses, how do
bike-fit and biomechanical factors contribute to each, and what clinical features
would help distinguish between them?

**Reference:** Medial knee pain in cyclists is multifactorial; the same symptom
location can arise from several distinct structures, and bike fit interacts
differently with each.

**Pes anserine bursitis / tendinopathy** is one of the most common cycling-specific
causes. The pes anserine is the conjoined insertion of the sartorius, gracilis, and
semitendinosus tendons on the medial tibia just below the joint line. Repetitive
knee flexion/extension under load inflames the bursa or the tendons themselves. The
primary bike-fit driver is saddle height that is too low: excessive knee flexion at
the bottom of the pedal stroke increases compressive and shear load on the insertion
with every revolution. Saddle fore-aft position also matters — too far forward
increases peak knee flexion angle similarly. Pain is typically at the proximal medial
tibia, reproduced by palpation 2–3 cm below the joint line, and worsens toward the
end of long efforts when fatigue degrades pedaling mechanics.

**Medial collateral ligament (MCL) stress** occurs when valgus force is repeatedly
applied to the knee. In cycling, this arises from cleat misalignment (insufficient
float or fixed cleats rotated too far inward, forcing the heel outward and knee
inward), excessive Q-factor mismatch (pedal stance width narrower than the rider's
natural hip width), or dynamic knee valgus driven by hip abductor weakness. The pain
follows the ligament line from the medial femoral epicondyle toward the tibial
attachment rather than below the joint, and is reproduced by valgus stress testing
or by replicating the cleat-forced alignment on the bike.

**Medial meniscus irritation** is less common in pure cycling than in impact sports
but can occur in riders with pre-existing degenerative changes or those who also run.
Joint-line tenderness, a palpable click, and pain that is provoked by combined
flexion and rotation (McMurray's test) distinguish it from soft-tissue causes. Bike
fit rarely causes de novo meniscus tears, but a saddle that is too low can aggravate
an already irritated meniscus by loading the joint in deep flexion.

**Saphenous nerve entrapment** is an under-recognised cause of medial knee and
distal thigh pain in cyclists. The infrapatellar branch of the saphenous nerve can be
compressed at the adductor canal or by sustained hip flexion in the aero position.
Pain has a dysaesthetic, burning, or tingling quality rather than the aching soreness
of musculotendinous causes, and may radiate down the medial calf. It does not
correlate with palpation of joint line or tendon insertion.

**Training load error** underlies or amplifies all of the above: a sudden large
increase in weekly distance or elevation is the most common precipitant, because
tissue adaptation lags behind cardiovascular fitness gains.

**Key distinguishing features by diagnosis:**
- Pes anserine: tenderness 2–3 cm below joint line on medial tibia; worsens late in
  long rides; linked to low saddle or forward seat
- MCL stress: tenderness along ligament from epicondyle to tibia; valgus-stress test
  positive or reproduces pain; linked to cleat rotation, narrow Q-factor, hip
  abductor weakness
- Medial meniscus: joint-line tenderness at the joint itself; McMurray's or Thessaly
  test positive; clicking or giving-way reported
- Saphenous nerve: burning/tingling quality; no focal musculoskeletal tenderness;
  may worsen in aggressive forward position

**Key facts:**
- Saddle too low → excessive knee flexion → pes anserine overload (most common fit cause)
- Cleat toe-in or insufficient float → valgus knee vector → MCL stress
- Q-factor narrower than hip width amplifies valgus load each pedal stroke
- Medial meniscus irritation: joint-line tenderness, McMurray's/Thessaly positive
- Saphenous nerve: burning/tingling quality, no musculoskeletal tenderness, worse in aero
- Training load spike is the most common precipitant regardless of structural diagnosis
- Saddle fore-aft too far forward increases knee flexion angle same as saddle too low
- Hip abductor weakness is a biomechanical amplifier for MCL and pes anserine pathology

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
