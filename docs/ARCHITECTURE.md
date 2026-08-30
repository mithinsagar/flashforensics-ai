# Architecture

How the system is put together, and why each boundary sits where it does.

---

## Layers

```
┌─────────────────────────────────────────────────────────────────────┐
│  Next.js dashboard          SSE progress, entropy map, evidence     │
└─────────────────────────────┬───────────────────────────────────────┘
                              │ HTTP + Server-Sent Events
┌─────────────────────────────▼───────────────────────────────────────┐
│  FastAPI                    sessions, streaming, download, ask      │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────────┐
│  LangGraph pipeline         scan → carve → classify → adjudicate    │
│                             → report                                │
└──────┬───────────────────────────────┬──────────────────────┬───────┘
       │                               │                      │
┌──────▼──────────┐   ┌────────────────▼──────┐   ┌───────────▼──────┐
│  disk/          │   │  knowledge/           │   │  llm/            │
│  parsers,       │   │  format corpus,       │   │  providers,      │
│  entropy,       │   │  Chroma indexes,      │   │  prompts         │
│  carver,        │   │  embedding fallback   │   │                  │
│  validators     │   │                       │   │                  │
└─────────────────┘   └───────────────────────┘   └──────────────────┘
       ▲
       │
┌──────┴──────────────────────────────────────────────────────────────┐
│  MCP server                 the same primitives, over stdio         │
└─────────────────────────────────────────────────────────────────────┘
```

The `disk/` package has no dependency on anything above it. It is importable on its own, which is what lets the MCP server expose the primitives directly rather than wrapping the pipeline.

---

## `disk/` — the substrate

| Module | Responsibility |
|---|---|
| `image.py` | Memory-mapped, sector-addressed view over a file. Clamps reads at the end rather than raising, because a truncated image is a normal input. Records read statistics. |
| `fat32.py` | FAT32 parser. Boot sector with backup fallback, FAT mirror reconciliation, damage-tolerant chain walking, LFN reassembly, orphan detection. |
| `exfat.py` | exFAT parser. Three-entry file sets, UTF-16 names, the `NoFatChain` contiguous-run shortcut, the allocation bitmap. |
| `entropy.py` | Block-level Shannon entropy, content-band classification, chi-square uniformity, anomaly detection, downsampling for display. |
| `signatures.py` | 77 magic byte signatures with ambiguity groups and per-format size ceilings. |
| `validators.py` | Per-format structural walkers. The evidence layer. |
| `carver.py` | Signature scanning, extent determination, fragment construction. |

**Damage as data, not as exceptions.** Every parser collects `DamageReport` objects instead of raising. A card with a wiped boot sector, a half-erased FAT and three severed chains should produce a report describing all of that, not a stack trace from the first problem. This shapes the whole package: `follow_chain` stops at the first thing that cannot be true and records why, rather than following a pointer into nonsense.

**Validators return one shape.** Every validator produces a `ValidationResult` with the same fields: header valid, footer present, structure complete, confidence, evidence, problems, metadata, true size. The adjudicator therefore reasons over a uniform set of facts, and a language model is asked to explain evidence rather than to guess a format.

---

## `knowledge/` — retrieval

Two Chroma collections with different lifetimes.

**`KnowledgeBase`** indexes 69 format descriptions once and persists. `query_within` restricts retrieval to a candidate set, so when the header has already ruled out sixty formats the search does not offer them back.

**`FragmentIndex`** is per session, holding one natural-language document per carved fragment. Ephemeral by design: fragment content is the user's data and should not outlive the session.

**Embedding fallback.** The intended model is all-MiniLM-L6-v2 via Chroma's bundled ONNX runtime, which avoids a PyTorch dependency. `build_embedding_function` constructs it and then actually exercises it on a probe string, because the model downloads lazily and a failure surfacing mid-analysis is far worse than one caught at startup. When it cannot be downloaded, `HashingEmbedding` takes over: hashed word and character n-grams with sublinear term weighting, L2 normalised.

The collection name is keyed to the active embedder. A persisted index built with MiniLM and queried with the hashing fallback would return confident nonsense, because the vectors live in unrelated spaces. Namespacing makes that impossible rather than merely unlikely.

---

## `llm/` — the model boundary

Anthropic and OpenAI are called over plain HTTP with `httpx` rather than through their SDKs: shorter dependency list, and the request shape stays visible.

`HeuristicProvider` reports `supports_reasoning = False`, and agents check that flag and take their own rule path directly, skipping a prompt round-trip that would only be parsed back into the same decision.

`FallbackProvider` wraps a remote provider. On failure it counts the failure and re-raises for structured calls, so the calling agent applies its own rules with full context. Free-text calls, such as the closing briefing, do fall through to the heuristic engine, because there is no richer context available there. `health()` reports the fallback count, so a run that quietly stopped using the model says so.

---

## `agents/` — the pipeline

`RecoveryState` is a `TypedDict`, flat and mostly plain data. Non-serialisable handles (the open image, the entropy map, the parser) are carried under underscore-prefixed keys and must be declared in the schema, because LangGraph drops keys the schema does not know about.

The event emitter lives in the state rather than in a module global, so two analyses running concurrently cannot write into each other's stream.

**Three-tier classification, cheapest first:**

1. Structural evidence, when the validator reached high confidence. Free and decisive.
2. Retrieval ranking over the candidate set, when structure was inconclusive but retrieval discriminated. On a tie the header order wins, because an arbitrary pick from a flat similarity ranking is a coin flip dressed up as a decision.
3. Model adjudication, for what remains, under a per-run budget.

**Verdict rules live in exactly one place.** `adjudicator._rule_verdict` is the only rule implementation for recoverability; `classifier._retrieval_classification` is the only one for identification. An earlier version duplicated this logic in the provider, and the two drifted apart, misjudging a truncated MP4.

---

## `api/` — the HTTP surface

**In-memory session store.** A forensics tool that persisted disk images and their carved contents to a shared database would be creating a second copy of exactly the data the user is most anxious about. Losing a session on restart is a far better failure mode than leaking one.

**Image ownership.** Sessions record whether the server put the file there. Uploads are the server's to delete; an image registered by path belongs to whoever pointed at it, and deleting that would destroy the user's only copy of the device they are trying to recover.

**SSE, not WebSockets.** The traffic is entirely one-directional, SSE survives proxies that mangle upgrades, and browsers reconnect on their own.

**Threading.** The pipeline runs on a worker thread; subscribers live on the event loop. `store.publish` marshals across with `call_soon_threadsafe`. Events are buffered and replayed, so a browser connecting mid-run sees the stages that already happened rather than joining a blank timeline.

The `analyze` handler is `async` purely so it runs on the event loop, which is the only place the loop reference can be captured. A sync handler runs in the threadpool where `get_event_loop()` raises.

**Fragments are cut on demand.** A run finding four hundred fragments should not write four hundred files the user may never open.

---

## `mcp_server/` — the protocol surface

Nine read-only tools exposing the primitives rather than the pipeline. `analyze_image` is there for the one-shot case, but the value is in the low-level operations, because those compose into investigations nobody wrote a workflow for.

Handlers run through `asyncio.to_thread`, since the disk work is blocking and would otherwise stall the protocol loop.

Nothing writes to the image under examination. These are evidence, and a recovery attempt that modifies its own input destroys what it was trying to save.

---

## Frontend

Next.js 15 app router, one client component tree, no state library. The dashboard has a single source of truth (the session) and a single stream (SSE), so a reducer would be ceremony.

The entropy map is hand-drawn SVG rather than a charting library, for two reasons: the data is a hundred thousand measurements downsampled to a fixed-width strip, which no general chart component does well, and the fragment markers need to be positioned by byte offset against the same scale.

**Two-tier rendering.** A single full-width chart fails on the input that matters. Real cards are mostly empty, so a 128 GB card holding 2 GB of photos puts every interesting measurement into the first 1.5% of the width. The locator strip is always the whole volume, so the emptiness stays visible information, with a bracket marking the detail window. The detail chart defaults to the occupied extent.

The zoom window comes from the backend, which re-buckets the same block measurements over the narrower range at full resolution. Zooming the overview points client-side would magnify data that had already been averaged down, adding no information.

**Order of operations on start:** subscribe to the stream first, then POST analyze. On a small image the entire scan stage happens in the gap between the POST returning and the stream connecting.

---

## Data flow for one analysis

```
POST /api/sessions/from-path   →  session created, image not copied
POST /api/sessions/{id}/analyze →  worker thread starts, loop captured
GET  /api/sessions/{id}/stream  →  buffered events replayed, then live

  scanner      detect FS → boot sector (backup fallback) → walk tree
               → orphaned clusters → entropy map → anomalies
  carver       orphaned runs → free space → verify reachable files
               → dedupe by offset → cluster annotation
  classifier   structural → retrieval → model, per fragment
  adjudicator  verdict + explanation + priority → rank
  reporter     index fragments → briefing → close image

GET  /api/sessions/{id}          →  full result payload
GET  /api/sessions/{id}/fragments →  ranked, filterable
POST /api/sessions/{id}/ask       →  RAG over this session only
```

---

## Performance

On the 128 MB benchmark fixture, a full run takes about 2.4 seconds on two cores.

Where the time goes, and what would matter at 64 GB:

- **Entropy scan** is linear in image size and reads every byte once. `np.bincount` per block keeps it fast; a Python loop would dominate everything else.
- **Carving** is confined to candidate regions, so it scales with occupied space rather than device size. This is the single biggest win: a mostly-empty 64 GB card carves like a small one.
- **Validation** is capped at 16 MB per fragment.
- **Model calls** are the only network-bound step, capped by `llm_max_fragments` per run, and skipped entirely for fragments structure already resolved.

The unavoidable cost at large sizes is the entropy pass. Sampling every Nth block for a first approximation, then measuring fully only around detected transitions, is the obvious optimisation and is not implemented.

---

## Extension points

**A new format** needs an entry in `signatures.py`, a description in `knowledge/filetypes.py`, and a validator in `validators.py` registered in the `VALIDATORS` map or one of the family sets.

**A new filesystem** needs a parser exposing `detect`, `parse_boot_sector`, `walk`, `orphaned_clusters`, `referenced_clusters`, `cluster_runs`, `cluster_to_offset` and `read_file`. The scanner dispatches on `detect`.

**A new agent** is a function taking and returning `RecoveryState`, registered as a node with its edges. The conditional edge after `scan` is the model for adding a failure route.

**A new LLM provider** subclasses `LLMProvider` and implements `complete`. Setting `supports_reasoning = False` makes agents skip it in favour of their own rules.
