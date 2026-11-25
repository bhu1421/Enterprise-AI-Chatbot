# ATLAS Enterprise AI — RAG Evaluation Report

**Evaluation date:** 24 November 2026  
**Artifact type:** Reproducible evaluation of a fictional enterprise document assistant  
**Repository:** [bhu1421/Enterprise-AI-Chatbot](https://github.com/bhu1421/Enterprise-AI-Chatbot)  
**Evaluation configuration:** `openai/gpt-oss-120b` via Groq · `BAAI/bge-m3` embeddings · Pinecone cosine vector search · top-K **5** · minimum relevance **0.48** · FastAPI/LangGraph on AWS EC2 in Docker.

> **Scope and limitations:** These figures describe one run on a *synthetic*, seven-document corpus, not production performance on real enterprise data. The original GPT-OSS-120B benchmark is preserved unchanged. Subsequent debugging and fixes are reported separately. The reviewed-answer score is a **provisional rubric-based judgment**, not independently double-annotated ground truth or an automated hallucination rate.

## 1. Objective and dataset

Assess (1) document/page retrieval, (2) answer correctness, (3) appropriate abstention when evidence is unavailable, and (4) end-to-end latency and request reliability of ATLAS.

The benchmark has **100 independent questions** over **seven synthetic OrionVale Systems PDFs**. Question categories: **40 factual, 25 paraphrase, 15 scenario, 10 cross-document, and 10 unanswerable**. The original question sheet contains expected answers and document-page labels (`OV-...:pN`).

Questions are split into **70 development** and **30 held-out test** examples, with **63/27 answerable questions** respectively. The held-out split tests previously unseen *queries about the same seven indexed documents*, not transfer to an unseen organization or new document collection. No model weights were trained or fine-tuned during this evaluation; the split is relevant to selecting or debugging RAG components without tuning against final test questions.

Gold answers were **not ingested** into Pinecone. Seven synthetic source documents were ingested as reference material. The documented access configuration is the default demo workspace; authentication and ACL isolation were not independently audited by this benchmark.

## 2. Exact headline results — original benchmark

| Metric | Development | Held-out test | Entire 100-question sample |
|:--|--:|--:|--:|
| Questions | 70 | 30 | 100 |
| Answerable questions | 63 | 27 | 90 |
| Hit@5 | **98.41%** (62/63) | **92.59%** (25/27) | **96.67%** (87/90) |
| Macro source-page Recall@5 | **96.83%** | **87.04%** | **93.89%** |
| Median end-to-end latency | **3.93 s** | **3.80 s** | **3.90 s** |
| P95 end-to-end latency (nearest-rank) | **5.59 s** | **5.43 s** | **5.55 s** |
| HTTP/request errors | **0/70** | **0/30** | **0/100** |
| Correct abstentions on unanswerable questions | **7/7** | **3/3** | **10/10** |

**Metrics definitions:**

- **Hit@5:** For an answerable question, at least **one** retrieved, post-filtered source page matches a gold document/page. Hit@5 = matched answerable questions / total answerable questions. It does **not** imply that an answer is correct.
- **Macro Recall@5:** Per answerable question, calculate `(number of distinct matched gold pages) / (number of gold pages)`; average across answerable questions. This is **page-level** recall, not chunk-level recall.
- **Citation-based retrieval evaluation:** The benchmark reads the API's `sources` response after relevance filtering. This is not a raw vector index recall benchmark, and can be affected by application routing (e.g. HITL requests return no sources).
- **Latency:** Measured at the benchmark client for `/api/chat` calls; includes backend processing and network/service overhead, and may include fast no-evidence or approval responses. P95 follows the evaluation script's nearest-rank convention.
- **API reliability:** Zero HTTP request errors were observed; this does not mean that every request produced a correct or fully answered response.
- **Appropriate abstention:** The answer text for all ten deliberately unanswerable queries avoided manufacturing unsupported facts. The graph's `status=no_evidence` occurred only once among those ten, so `strict_no_evidence` status alone substantially undercounts appropriate refusals.

## 3. Provisional answer-correctness review

Each of the 100 generated responses was compared with the expected answer and the fictional source-document facts using a rubric. Reviews and explanations are in `results/reviewed_100_questions.csv`.

| Review outcome | Development | Held-out test | All questions |
|:--|--:|--:|--:|
| Pass (fully matches rubric) | 66 | 26 | **92** |
| Partial (some correct information, material gap) | 1 | 2 | **3** |
| Fail | 3 | 2 | **5** |
| **Strict provisional answer correctness (Pass only)** | **94.29%** | **86.67%** | **92.00%** |

### Results by question type

| Question type | N | Pass | Partial | Fail |
|:--|--:|--:|--:|--:|
| Factual | 40 | 38 | 1 | 1 |
| Paraphrase | 25 | 23 | 0 | 2 |
| Scenario | 15 | 14 | 0 | 1 |
| Cross-document | 10 | 7 | 2 | 1 |
| Unanswerable | 10 | 10 | 0 | 0 |

**Scoring rules:** Pass = answer matches the expected decision/fact with necessary qualifications; Partial = materially incomplete or insufficiently supported, even if some facts are correct; Fail = wrong refusal for a genuinely answerable query, incorrect action routing, or incorrect response. Unanswerable questions receive Pass only for appropriately refusing to fabricate unavailable information. Partial answers are counted as **0** in strict correctness. This is one provisional review, with no second independent annotator or inter-rater agreement.

**Not a verified faithfulness rate:** The exported benchmark CSV has source document/page IDs, but **does not contain the complete exact retrieved chunks nor a reliable claim-to-citation-index map**. Therefore it cannot establish a rigorous claim-level faithfulness or hallucination percentage. The `manual_faithful_0_or_1` field remains blank. A stronger future evaluation should store the exact retrieval snapshots and assess each atomic claim against its evidence with human review or a carefully validated adjudicator.

## 4. Error analysis

| Question | Split | Result | Root cause / follow-up |
|:--|:--|:--|:--|
| `Q007` | Development | Partial | Named the People Portal correctly, but did not retrieve the canonical corporate page that explicitly identifies benefits registration; a citation/evidence completeness gap. |
| `Q048` | Development | Fail | False abstention despite employee-handbook guidance on attendance corrections. |
| `Q052` | Development | Fail | False abstention despite explicit security-policy statement that ISO 27001 certification is *not* claimed. |
| `Q081` | Development | Fail | False abstention despite retrieved workweek and hybrid attendance sources. |
| `Q036` | Held-out | Fail | The correct `OV-CS-006:p2` passage ranked **#1** in direct Pinecone diagnostics with score **0.4407**, below the **0.48** configured cutoff, so it was filtered out; original answer: `no_evidence`. |
| `Q078` | Held-out | Fail | Action-keyword substring matching sent an informational question about report exports to `pending_approval`, bypassing RAG. Fixed in application code and successfully regression-tested separately. |
| `Q089` | Held-out | Partial | Missing relevant vendor-review requirements and restricted-data qualifiers. |
| `Q090` | Held-out | Partial | Omitted the 10-working-day travel-claim submission deadline and some process context. |

**Post-fix regression (not part of held-out metrics):** After changing the HITL match to check leading explicit-action phrases rather than arbitrary substrings, Q078 was retested. ATLAS returned `status=answered`, P2 High, acknowledgement within one business hour, resolution within two business days, and citations to `OV-CS-006` pages 1–2. **The original held-out Hit@5 remains 92.59%.** Q036 was explicitly left as an original documented miss; no retrospective change to the held-out result was made.

## 5. Reproducing the evaluation

### Inputs

- `evaluation/ATLAS_100_Question_RAG_Evaluation.csv` — original labeled question/expected-answer sheet (already in repo)
- `evaluation/atlas_evaluate.py` — evaluation runner (already in repo)
- `evaluation/synthetic_corpus/*.pdf` — **seven fictional OrionVale reference PDFs** (add with this bundle)
- `evaluation/results/dev_results.csv` and `evaluation/results/test_results.csv` — frozen original, unmodified API outputs and source IDs
- `evaluation/results/reviewed_100_questions.csv` — raw fields plus provisional verdict and explanation columns; original answer and source fields preserved
- `evaluation/results/summary_metrics.json` — machine-readable computed metric summary

### Environment prerequisites

1. Deploy the ATLAS FastAPI application with the evaluated configuration: **Groq `openai/gpt-oss-120b`, SentenceTransformers `BAAI/bge-m3`, Pinecone top-K 5, `MIN_RELEVANCE=0.48`**. The deployment requires configured database, Pinecone, Groq and S3 access.
2. Ingest all **seven** synthetic PDFs into the demo workspace, and verify that each document has status `ready`. Use the same access identity and document-level permissions used for the original tests. Avoid ingesting the answer-labeled evaluation CSV (it would leak ground truth).
3. Run the commands below from the `evaluation/` directory. The URL `127.0.0.1:8000` assumes the evaluation runs on the EC2 host alongside the deployed backend.

```bash
python3 atlas_evaluate.py \
  --csv ATLAS_100_Question_RAG_Evaluation.csv \
  --base-url http://127.0.0.1:8000 \
  --split dev --sleep 15

python3 atlas_evaluate.py \
  --csv ATLAS_100_Question_RAG_Evaluation.csv \
  --base-url http://127.0.0.1:8000 \
  --split test --sleep 15
```

The runner generates timestamped CSV results and prints the JSON summary. Compare them with the **frozen original outputs** in `results/`. **Do not** expect byte-for-byte identical generated answers or latency from a non-deterministic hosted LLM or changing infrastructure. Preserve the exact model/configuration and commit hash for formal repeatability.

### Manual review procedure

1. Compare each generated answer to the expected answer and applicable source-policy text; verify required qualifiers (times, amounts, authorities, exceptions, dates).
2. Mark Pass/Partial/Fail with an explanation; review unsupported refusals separately from legitimately unanswerable questions.
3. For faithfulness, collect **full retrieved chunks with citation indexes**, split the response into atomic claims, and evaluate evidence support for each claim. Do not publish a faithfulness percentage until that work is complete.
4. Freeze original dev/test outputs before applying code changes; label any later reevaluation **post-fix regression**, not untouched held-out testing.

## 6. Limitations and future work

- Small, single-synthetic-company benchmark of seven short, consistent PDFs; results may differ with longer, noisier, multilingual or contradictory documents.
- The held-out split holds out queries, **not** source documents. The sample has only 27 answerable held-out questions and three held-out unanswerable questions; percentages should be interpreted with that sample size in mind.
- Questions/expected answers were synthetic; there was no blind expert panel or multiple independent raters.
- The benchmark uses API-returned source lists rather than all raw vector matches, so RAG routing and filtering influence retrieval metrics.
- The fixed similarity threshold can suppress relevant top-ranked material (Q036). Improving retrieval requires development-set validation and a fresh independent test if the held-out examples influence tuning.
- Keyword-based HITL routing produced an avoidable false-positive (Q078), subsequently fixed and regression-tested. The benchmark does **not** assess the security of the full HITL approval mechanism.
- Successful refusals on 10 negative examples do **not** establish a zero hallucination rate or enterprise-wide safety.
- API reliability and latency were measured under a sequential synthetic load with a 15-second inter-request pause, not a concurrency or production load test.

---

*Supporting artifacts:* `results/dev_results.csv`, `results/test_results.csv`, `results/reviewed_100_questions.csv`, `results/summary_metrics.json`, the original benchmark questions and evaluation script, and the seven fictional reference PDFs.
