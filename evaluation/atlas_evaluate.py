"""Evaluate ATLAS end-to-end using the OrionVale 100-question ground truth.

Uses only Python's standard library. Calls ATLAS's existing /api/documents and
/api/chat endpoints. It never uploads, deletes, or modifies documents.

Example:
    python atlas_evaluate.py --csv ATLAS_100_Question_RAG_Evaluation.csv \
      --base-url http://127.0.0.1:8000 --split dev --limit 5

Note: the demo app does persist generated chat turns in PostgreSQL.
"""

import argparse
import csv
import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CODE_BY_FILE_PREFIX = {
    "01": "OV-CORP-001",
    "02": "OV-HR-002",
    "03": "OV-SEC-003",
    "04": "OV-PROC-004",
    "05": "OV-FIN-005",
    "06": "OV-CS-006",
    "07": "OV-DG-007",
}

REQUIRED_CSV_COLUMNS = {
    "question_id", "split", "category", "question", "expected_answer",
    "answerable", "gold_references", "gold_document_codes", "gold_pages",
}


def api_request(base_url, route, payload=None, timeout=120):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = Request(
        base_url.rstrip("/") + route,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            body = exc.read(500).decode("utf-8", "replace")
        except Exception:
            body = ""
        raise RuntimeError(f"HTTP {exc.code} on {route}: {body[:250]}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Network error on {route}: {exc}") from exc


def extract_doc_code(title):
    """Match the seven original file-name prefixes; suffixes like '(1)' are fine."""
    name = str(title).strip()
    if len(name) >= 3 and name[:2] in CODE_BY_FILE_PREFIX and name[2] in "_- ":
        return CODE_BY_FILE_PREFIX[name[:2]]
    # Alternatively accept a file title containing an explicit document code.
    for code in CODE_BY_FILE_PREFIX.values():
        if code in name.upper():
            return code
    return None


def parse_gold_refs(value):
    """Gold references look like OV-HR-002:p2|OV-CORP-001:p1."""
    return {ref.strip() for ref in str(value or "").split("|") if ref.strip()}


def source_ref(source, doc_id_to_code):
    code = doc_id_to_code.get(str(source.get("document_id", "")))
    if not code:
        code = extract_doc_code(source.get("title", ""))
    if not code:
        return "UNMAPPED:" + str(source.get("document_id", ""))
    try:
        page = int(source.get("page", 0))
    except (ValueError, TypeError):
        page = 0
    return f"{code}:p{page}"


def main():
    ap = argparse.ArgumentParser(description="ATLAS RAG benchmark runner")
    ap.add_argument("--csv", required=True, help="Path to downloaded 100-question CSV")
    ap.add_argument("--base-url", required=True, help="ATLAS URL, for example http://IP:8000")
    ap.add_argument("--split", choices=("dev", "test", "all"), default="dev")
    ap.add_argument("--limit", type=int, default=0, help="Run only first N questions (0 = entire split)")
    ap.add_argument("--sleep", type=float, default=1.5, help="Seconds between calls to reduce rate limiting")
    ap.add_argument("--timeout", type=int, default=120, help="Seconds to wait for each chat response")
    ap.add_argument("--output", help="Results CSV path (generated automatically if omitted)")
    args = ap.parse_args()

    if args.limit < 0 or args.sleep < 0:
        ap.error("--limit and --sleep must be nonnegative")

    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED_CSV_COLUMNS - set(reader.fieldnames or [])
        if missing:
            ap.error(f"CSV is missing columns: {sorted(missing)}")
        rows = list(reader)

    if len(rows) != 100:
        print(f"NOTE: CSV has {len(rows)} rows rather than the original 100.")

    selected = [row for row in rows if args.split == "all" or row["split"].lower() == args.split]
    if args.limit:
        selected = selected[:args.limit]
    if not selected:
        ap.error("No matching evaluation questions")

    print("Checking indexed documents in ATLAS...")
    documents = api_request(args.base_url, "/api/documents").get("documents", [])
    ready = [d for d in documents if d.get("status") == "ready"]
    doc_id_to_code = {}
    counts = Counter()
    for doc in ready:
        code = extract_doc_code(doc.get("title", ""))
        if code:
            doc_id_to_code[str(doc["id"])] = code
            counts[code] += 1

    print("Ready evaluation documents:")
    for code in CODE_BY_FILE_PREFIX.values():
        print(f"  {code}: {counts[code]} ready")
    missing_codes = [code for code in CODE_BY_FILE_PREFIX.values() if counts[code] == 0]
    if missing_codes:
        raise SystemExit(
            "STOP: Missing ready PDFs for " + ", ".join(missing_codes) + ". "
            "Finish ingesting all seven PDFs before a representative benchmark."
        )
    duplicates = [code for code in CODE_BY_FILE_PREFIX.values() if counts[code] > 1]
    if duplicates:
        print("WARNING: Multiple ready copies for " + ", ".join(duplicates) +
              ". Stale or duplicate vectors may affect measured retrieval.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = Path(args.output or f"atlas_eval_{args.split}_{stamp}.csv")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    results = []
    answerable_seen = 0
    hits = 0
    per_question_recalls = []
    no_evidence_negative = 0
    negative_seen = 0
    latencies = []
    failed_calls = 0

    for i, row in enumerate(selected, start=1):
        gold = parse_gold_refs(row["gold_references"])
        is_answerable = row["answerable"].strip().lower() == "yes"
        began = time.perf_counter()
        error = ""
        response = {}
        try:
            # No conversation_id: each question is independent and receives a fresh chat session.
            response = api_request(args.base_url, "/api/chat", {"question": row["question"]}, args.timeout)
        except Exception as exc:
            error = str(exc)
            failed_calls += 1
        elapsed = round(time.perf_counter() - began, 3)

        if not error:
            latencies.append(elapsed)
        sources = response.get("sources", []) or []
        retrieved = {source_ref(src, doc_id_to_code) for src in sources}
        matched = gold & retrieved
        question_hit = int(bool(matched)) if is_answerable and gold and not error else 0
        question_recall = len(matched) / len(gold) if is_answerable and gold and not error else 0.0
        if is_answerable:
            answerable_seen += 1
            hits += question_hit
            per_question_recalls.append(question_recall)
        else:
            negative_seen += 1
            no_evidence_negative += int(not error and response.get("status") == "no_evidence")

        result = {
            "question_id": row["question_id"],
            "split": row["split"],
            "category": row["category"],
            "question": row["question"],
            "expected_answer": row["expected_answer"],
            "answerable": row["answerable"],
            "gold_references": row["gold_references"],
            "retrieved_references": "|".join(sorted(retrieved)),
            "hit_at_5": question_hit if is_answerable else "",
            "recall_at_5": f"{question_recall:.3f}" if is_answerable else "",
            "source_count": len(sources),
            "status": response.get("status", ""),
            "answer": response.get("answer", ""),
            "latency_seconds": elapsed,
            "request_error": error,
            "manual_correct_0_or_1": "",  # Fill after verifying answer against PDF.
            "manual_faithful_0_or_1": "",  # Fill after verifying cited support.
        }
        results.append(result)
        status = "ERROR" if error else ("HIT" if question_hit else "MISS") if is_answerable else response.get("status", "negative")
        print(f"[{i:03d}/{len(selected):03d}] {row['question_id']} {status} {elapsed:.2f}s")
        if error:
            print("    ", error[:240])
        if args.sleep and i != len(selected):
            time.sleep(args.sleep)

    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)

    summary = {
        "split": args.split,
        "questions_run": len(results),
        "failed_api_calls": failed_calls,
        "answerable_questions": answerable_seen,
        "hit_at_5_percent": round(100 * hits / answerable_seen, 2) if answerable_seen else None,
        "macro_recall_at_5_percent": round(100 * statistics.mean(per_question_recalls), 2) if per_question_recalls else None,
        "unanswerable_questions": negative_seen,
        "strict_no_evidence_percent": round(100 * no_evidence_negative / negative_seen, 2) if negative_seen else None,
        "median_end_to_end_latency_s": round(statistics.median(latencies), 3) if latencies else None,
        "p95_end_to_end_latency_s": round(sorted(latencies)[max(0, int(0.95 * len(latencies) + 0.9999) - 1)], 3) if latencies else None,
        "results_csv": str(output_path),
        "notes": [
            "Retrieval metrics evaluate citations returned by the deployed RAG path after relevance filtering.",
            "Gold labels are document:page; not chunk-level identifiers.",
            "For multi-source questions, macro Recall@5 measures the fraction of pages found.",
            "Answer correctness and faithfulness require human review of the results CSV.",
            "No-evidence rate is a conservative metric, not a complete measure of appropriate abstention.",
            "Any API errors make the run unsuitable for a final resume performance claim.",
        ],
    }
    summary_path = output_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n=== ATLAS EVALUATION SUMMARY ===")
    print(json.dumps(summary, indent=2))
    print("Review answers manually: fill manual_correct_0_or_1 and manual_faithful_0_or_1 in the CSV.")


if __name__ == "__main__":
    main()
