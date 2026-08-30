# Walkthrough

Everything in this project, explained from zero. Read this before you talk to anyone about the code. It assumes no prior knowledge of filesystems and builds up to why each design decision was made.

If someone asks "what is a cluster chain" or "why entropy" or "how do you know that JPEG is complete", the answers are here.

---

## 1. How a memory card actually stores a file

A card is a flat array of bytes. Nothing about it knows what a file is. Structure is imposed by a **filesystem**, and on SD cards and USB drives that is almost always FAT32 or exFAT.

The card is divided into fixed-size chunks called **sectors**, historically 512 bytes. The filesystem groups sectors into **clusters**, the smallest unit it will allocate. A 1 KB cluster on a 512-byte-sector card is 2 sectors. This is why a 3-byte file still consumes 1 KB on disk.

A FAT32 volume has four regions, in order:

```
┌──────────────┬────────────┬────────────┬────────────────────────────┐
│ Reserved     │ FAT copy 1 │ FAT copy 2 │ Data region                │
│ (boot sector)│            │            │ (clusters 2, 3, 4, ...)    │
└──────────────┴────────────┴────────────┴────────────────────────────┘
```

**Boot sector.** Sector 0. Holds the BIOS Parameter Block: bytes per sector, sectors per cluster, how many FATs, how big each FAT is, which cluster the root directory starts at. Without it you cannot compute where anything lives. FAT32 keeps a backup copy at sector 6, and that backup matters enormously in practice.

**File Allocation Table.** An array of 32-bit entries, one per cluster. Entry N tells you which cluster follows cluster N in the same file. That is the **cluster chain**.

Say `photo.jpg` occupies clusters 100, 101, 105:

```
FAT[100] = 101       cluster 100 is followed by 101
FAT[101] = 105       cluster 101 is followed by 105
FAT[105] = 0x0FFFFFFF   end of chain
```

To read the file you start at 100, read that cluster, look up `FAT[100]`, go to 101, read, look up, go to 105, read, see the end marker, stop. **Walking the chain is how a file gets read.** Two copies of this table exist so a damaged one can be checked against the other.

**Directory entries.** 32 bytes each, living in the data region. Each holds the 8.3 short name, attributes, timestamps, the **first cluster** of the file, and the file size. Long filenames like `inspection-checklist.pdf` do not fit in 8.3, so they are stored in extra entries immediately before the short one, 13 UTF-16 characters at a time, in reverse order, tied together by a checksum of the short name. That is why `build_lfn_entries` in the fixture generator writes them backwards.

**Cluster to byte offset.** The single most important formula in this project:

```
byte_offset = (data_start_sector + (cluster - 2) * sectors_per_cluster) * bytes_per_sector
```

Clusters are numbered from 2 because entries 0 and 1 in the FAT are reserved for the media descriptor and an end-of-chain marker.

---

## 2. What "corruption" actually means

None of the common failures destroy your data. They destroy the metadata that finds it.

**Wiped boot sector.** The card is interrupted mid-metadata-write. Sector 0 goes to zeroes. Your computer no longer knows the geometry so it offers to reformat. Every photo is still there, untouched. The backup boot sector at sector 6 usually still holds the geometry, which is why `Fat32Parser.detect` checks sector 6 as well as sector 0. Handling this is not a clever trick, it is what the specification intends.

**Damaged FAT.** Part of the allocation table gets overwritten. Chains break mid-file. The second FAT copy may still hold the missing entries, which is what `_reconcile_fats` recovers: where one copy says a cluster is free and the other holds a live value, the live value wins, and the disagreement range is recorded as evidence of where the damage sits.

**Erased directory entries.** The entry naming a file is destroyed but the FAT still marks its clusters as allocated. The data is intact and reachable, and nothing points at it. These are **orphaned clusters**, and they are exactly what carving is for.

**Normal deletion.** FAT marks the first byte of the entry `0xE5` and frees the cluster chain. The bytes stay on the card until something overwrites them, which is why deleted photos are recoverable at all.

**Truncation.** The card is pulled mid-write. The directory entry claims the full size; the tail clusters were never written. The file looks fine in a listing and opens as half an image.

**Payload corruption.** Bits flip inside the data. Header and footer are intact, the file opens, the content is wrong. This is the case a header-only checker cannot catch.

---

## 3. Why entropy

**Shannon entropy** measures how unpredictable a stream of bytes is, in bits per byte, from 0 to 8.

```
H = -Σ p(x) log₂ p(x)
```

`p(x)` is the frequency of byte value `x` in the block. If every byte is identical, one value has probability 1, and H = 0. If all 256 values are equally likely, each has probability 1/256, and H = 8.

Content types occupy separable bands:

| Entropy | Content |
|---|---|
| ~0.0 | Unallocated space, all zeroes |
| 1.0 – 4.0 | Structured binary, filesystem metadata |
| 4.0 – 6.5 | Natural language, source code, markup |
| 6.5 – 7.5 | Mixed containers, office documents |
| > 7.5 | Compressed or encrypted: JPEG, PNG, ZIP, MP4 |

**This buys three things.**

*Where to look.* One pass over a 128 GB card tells you which 2% holds data. Carving then skips 98% of the device instead of scanning it.

*Where the damage is.* A block at 7.9 bits followed immediately by a zero-filled block is a truncation cliff, the signature of a file whose tail was never written. That is `find_anomalies`.

*What the filesystem is lying about.* A block carrying 7.9 bits of entropy sitting in space the filesystem calls free is not free. Its directory entry is gone. That is an orphan, found statistically rather than structurally.

**One thing entropy cannot do:** separate compressed from encrypted data. Both sit near 8.0. That is what `chi_square_uniformity` is for. Encrypted output is much closer to genuinely uniform, so the chi-square residual against a flat distribution is small for encryption and larger for compression, which leaves detectable structure.

Implementation note: the entropy function uses `np.bincount` rather than a Python loop, because it runs over every block of the image and the naive version dominates total runtime.

---

## 4. Carving, and why the naive version is not useful

Carving means finding files by their content when the filesystem cannot help. Most formats announce themselves:

| Format | Magic bytes |
|---|---|
| JPEG | `FF D8 FF` … ends `FF D9` |
| PNG | `89 50 4E 47 0D 0A 1A 0A` … ends with an `IEND` chunk |
| PDF | `%PDF-` … ends `%%EOF` |
| ZIP | `50 4B 03 04` … ends `50 4B 05 06` |
| SQLite | `SQLite format 3\0` |

Scan the raw device for these patterns, dump what you find. That is PhotoRec. Three problems make the raw output nearly unusable.

**Problem 1: where does it end?** Many formats have no footer at all. MP3 does not. The usual fix is to dump a fixed maximum, which is how a three-minute song becomes a 64 MB blob with the next four files attached.

*Here:* `determine_extent` runs the structural validator and trims to the exact size the format itself reports. For MP3, `validate_mp3` walks frame headers, each of which encodes the bitrate and sample rate that determine that frame's exact length, giving a byte-precise size. For formats with genuinely no end marker, `_bound_by_free_space` stops at the first sustained run of zeroes, because FAT writes files into consecutive clusters and leaves the rest zeroed.

**Problem 2: false positives.** A two-byte signature appears by chance roughly once every 65 KB of random data. On a 64 GB card that is a million hits.

*Here:* two filters. FAT allocates in clusters, so a real file always begins on a cluster boundary; a hit at an arbitrary offset is almost always a coincidence inside compressed data. Then a confidence floor drops hits whose structure never validated. On the benchmark this takes false positives to zero.

**Problem 3: ambiguity.** These eight formats share the identical four-byte header, because they are all zip containers:

```
zip  docx  xlsx  pptx  apk  jar  epub  odt
```

And these six share an ISO base media `ftyp` box:

```
mp4  mov  m4a  3gp  heic  avif
```

And these four share the OLE compound header:

```
doc  xls  ppt  msg
```

*Here:* `validate_zip` parses the archive and reads the entry names. `word/document.xml` means DOCX. `xl/workbook.xml` means XLSX. `AndroidManifest.xml` plus `classes.dex` means APK. `META-INF/MANIFEST.MF` means JAR. For ISO base media, the brand string in the `ftyp` box is the disambiguator.

---

## 5. Structural validation, the evidence layer

This is what makes a recoverability verdict mean something. A signature match says "something that starts like a JPEG begins here". It says nothing about whether the rest survived. To answer that you walk the format's own structure.

**JPEG.** A chain of length-prefixed segments followed by entropy-coded scan data. `validate_jpeg` walks marker to marker. Reaching `FFDA` (start of scan) proves the header region is intact. Reaching `FFD9` (end of image) proves the scan data was not cut short. Along the way the frame header gives pixel dimensions and the EXIF block gives camera strings. Scan data present but no EOI means truncated, which is the difference between a photo that opens and a grey half-image.

**PNG.** The most precisely gradeable format in the project, because every chunk carries a CRC32. `validate_png` recomputes each one. **A CRC failure is proof of corruption, not an inference from it.** This is why a PNG with all its chunks present but a failing CRC is judged PARTIAL and never RECOVERABLE.

**ZIP.** Walk the local file headers to collect entry names, then find the end-of-central-directory record. One subtlety worth understanding: searching backwards from the end of the buffer is the obvious implementation and it is wrong. A carve read runs well past the archive, so the last EOCD in the window usually belongs to a later file, and taking it stretches a 4 KB archive into a 300 KB fragment. `_find_matching_eocd` anchors the search at where local-entry parsing stopped.

**PDF.** Header version, count of `obj` versus `endobj`, `startxref` pointer, `%%EOF` marker, page objects, `/Encrypt` dictionary. Objects opened but never closed means the body was cut short.

**ISO base media (MP4).** Walk the box tree. `ftyp` gives the brand, `moov` carries the track index, `mdat` holds the media. Cameras write `moov` last, so a recording interrupted by card removal has an `mdat` full of video and no index. There is a second subtlety: the camera writes box headers with their final declared sizes and then streams samples in, so a truncated recording validates structurally while most of the `mdat` payload is still zeroes. `_mdat_zero_ratio` measures the payload directly, because the declared size records what the camera intended, not what reached the card.

**SQLite.** The header declares a page size and a page count, so the true file length is verifiable arithmetic rather than a guess.

**Plain text.** No magic bytes at all. Identified by a high printable-character ratio plus clean UTF-8 decoding, with a delimiter-rhythm check separating CSV from prose. The detected format normalises to `txt` regardless of what the filename claimed, because `.log`, `.md` and `.ini` are the same thing to a recovery tool, and a filename is a claim about content while this module only reports what the bytes support.

---

## 6. The agent pipeline

Five nodes in a LangGraph state machine:

```
scan → carve → classify → adjudicate → report
```

Linear, because the dependencies genuinely are. Nothing can be carved before the scanner has isolated the orphaned regions. Nothing classified before it is carved. Nothing judged before it is identified. Branching here would be decoration.

What the graph buys over a plain function chain: a typed state object every node reads and writes, so a failure traces to the node that produced the bad field; a conditional edge that routes a failure straight to the end instead of letting a broken image cascade through four more stages; and room to add a repair or reassembly branch later without restructuring callers.

**Scanner.** Detects the filesystem, parses the boot sector (falling back to the backup), walks the directory tree, computes orphaned clusters, builds the entropy map, flags anomalies.

**Carver.** Carves orphaned cluster runs and non-empty free space, skipping ranges that belong to files the filesystem can still describe, because re-carving an intact file just produces a duplicate the user has to dismiss. It also verifies the files that *are* still reachable, running them through the same validators. A file with a directory entry is not automatically healthy: a photo whose last clusters were zeroed still appears at full size in the listing, and the damage is only discovered on opening it.

**Classifier.** Three tiers, cheapest first. Structural evidence, which is decisive when available. Retrieval against the format knowledge base, restricted to the candidate set the header already permits. A language model, last, only for what remains open.

**Adjudicator.** Assigns one of four statuses with a plain-language explanation and a priority, then ranks everything.

| Status | Meaning |
|---|---|
| `RECOVERABLE` | Structure complete, integrity checks pass, opens normally |
| `PARTIAL` | Real content survives but incomplete or damaged |
| `METADATA_ONLY` | Only headers survive; properties readable, file will not open |
| `JUNK` | Chance signature match inside unrelated data |

**Reporter.** Indexes every fragment for question answering and writes the closing briefing.

---

## 7. Why the vector database is there

Two Chroma collections, doing different jobs.

**The format knowledge base** holds 69 natural-language descriptions of file formats, embedded once. The classifier turns a fragment's measured properties into a sentence and retrieves the nearest descriptions. This gives the language model a shortlist drawn from a fixed corpus instead of letting it free-associate a format name from a header it half-remembers.

`query_within` restricts retrieval to the candidate set the header already permits. When the bytes have ruled out sixty formats, searching all sixty-nine invites an answer the evidence forbids.

**The fragment index** is created per session, one document per carved fragment. The document is phrased in the words a person would use: format name, category, verdict in plain language, size in human units, plus EXIF strings, document titles and archive entry names pulled out during validation. That is what makes "did you find any photos from a Canon camera" answerable.

Retrieval is scoped to one session, so an answer can never leak across analyses, and every claim carries the fragment id it came from, so it can be checked against the actual bytes.

The RAG agent also reads the obvious nouns out of a question and turns them into a metadata filter. Pure vector search answers "what did you find" well and "how many videos are recoverable" badly, because counting needs a filter rather than a similarity ranking.

---

## 8. Why there is a rule engine underneath the model

The deterministic engine is not a demo fallback. It is a design requirement, for one reason:

**The benchmark numbers in the README have to be reproducible by anyone who clones this repository, and a number that moves when you swap models is not measuring the recovery engine.**

So the ordering is: structural evidence decides what it can decide, retrieval decides what it can, and the model handles what is genuinely open and writes the explanations. Correctness does not depend on it. That is also what lets the whole thing run with no API key, no network, on an air-gapped forensics workstation.

There is exactly one rule implementation per decision, owned by the agent that makes that decision. An earlier version had rules in two places, in the provider and in the agent, and they drifted: the provider's version misjudged a truncated MP4 that the agent's version got right. When a model call fails now, the error reaches the agent, which applies its own rules with full context.

---

## 9. How the accuracy numbers are produced

`tools/make_fixture.py` formats a FAT32 volume from scratch in pure Python. No `mkfs`, no loop mount, no root, which is why the corpus regenerates identically on Linux, macOS and CI.

It computes the FAT geometry (solving the circular relationship between FAT size and cluster count by iteration), writes the boot sector and its backup, builds the allocation table, lays out a realistic card (`DCIM/100CANON`, `Documents`, `Media`), and writes **real files**: JPEGs rendered with Pillow carrying real EXIF, PNGs with valid chunk CRCs, structurally valid PDFs with an xref table and trailer, genuine zip containers with the internal entry names that make a DOCX a DOCX, a real SQLite database, a structurally correct MP4 box tree, MP3 frames at the correct 417-byte length for 128 kbps at 44.1 kHz.

Then it damages the image on purpose and records what it did. Every planted file gets its SHA-256, its cluster list, its byte offset and its scenario written to a manifest.

`tools/benchmark.py` runs the pipeline and compares against that manifest. Because the manifest records exactly what went in and exactly what was done to it, the recovery rate is a measurement rather than a claim.

---

## 10. Questions you should be able to answer

**What is a cluster chain?**
A linked list stored in the File Allocation Table. Entry N holds the number of the cluster that follows cluster N in the same file. Reading a file means starting at its first cluster, reading, looking up the FAT entry, and repeating until an end-of-chain marker.

**Why not use pytsk3?**
It needs `libtsk` compiled, which breaks on macOS often enough to make the repository unusable for anyone cloning it. More importantly, libraries built for healthy filesystems raise on the first inconsistency, and damage tolerance is the whole point. This parser records the inconsistency as evidence and keeps walking.

**How do you know a JPEG is complete?**
Walk its segment markers from SOI. Reaching start-of-scan proves the header region survived; reaching the end-of-image marker proves the scan data was not cut short. Both are required.

**Why does a PNG with all its chunks present get marked PARTIAL?**
Because PNG checksums every chunk. If a CRC fails, stored bytes changed. The file opens and the pixels are wrong. A verdict that ignored the checksum would be a guess dressed up as an assessment.

**What does entropy actually tell you?**
What kind of content occupies a block, cheaply, over the whole device. That decides where carving spends its time, and the transitions in the profile are themselves evidence of truncation and of orphaned data sitting in supposedly free space.

**Why does alignment matter?**
FAT allocates in clusters, so a real file always begins on a cluster boundary. A two-byte signature appears by chance about once every 65 KB of random data. Requiring alignment removes essentially all of those coincidences.

**What is the hardest part of carving?**
Not finding files. Knowing where they end. A fragment with the next file's bytes stapled on is not a recovered photo, which is why extent accuracy is measured separately from recall.

**What would you build next?**
Fragmented file reassembly. Right now a file split across non-adjacent clusters with a destroyed chain is recovered up to the first discontinuity. Reassembling it means scoring candidate continuations by content, and for JPEG specifically the Huffman decoder state at the boundary makes correctness checkable rather than heuristic.
