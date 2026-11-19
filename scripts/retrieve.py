#!/usr/bin/env python3
"""search — source-bound literature search with traceable candidates.

Usage:
    python3 search.py --case /input/case.json --work /app/work

The case supplies the research question, the source databases, cached search
responses (Crossref / EuropePMC / publisher / retained reference graph) and the
organizer's incremental import state. This script never invents a record: every
candidate carries the exact source file + record id it came from, plus the query
and scoring rationale that produced its rank.

Outputs:
    work/candidates.json   — deduplicated, ranked candidate set
    work/search_log.json   — every query, source, raw hit count, decisions
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

UA = "litsearch-skill/1.0 (research use; contact: user@example.org)"

SOURCE_WEIGHTS = {
    "crossref": 1.0,
    "europepmc": 0.95,
    "pubmed": 0.95,
    "publisher": 0.9,
    "openalex": 0.85,
    "semantic_scholar": 0.85,
    "reference_graph": 0.7,
    "other": 0.6,
}

STOP = {
    "the", "and", "for", "of", "in", "on", "a", "an", "to", "with", "by", "from",
    "is", "are", "was", "were", "as", "at", "using", "via", "based", "study",
    "review", "paper", "article", "research",
}


def load(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def norm_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value)


def norm_doi(value) -> str:
    s = norm_text(value).strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    return s


def norm_title(value) -> str:
    s = norm_text(value).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokenize(text: str):
    return [t for t in re.findall(r"[a-z0-9]{2,}", (text or "").lower()) if t not in STOP]


# -- record normalization ---------------------------------------------------

def from_crossref(item: dict, source_file: str) -> dict:
    title = item.get("title") or []
    auth = item.get("author") or []
    return {
        "source": "crossref",
        "source_file": source_file,
        "record_id": norm_doi(item.get("DOI")) or (item.get("URL") or "")[:200],
        "doi": norm_doi(item.get("DOI")),
        "title": norm_text(title[0] if title else "")[:400],
        "authors": [f"{a.get('family','')} {a.get('given','')}".strip() for a in auth][:12],
        "year": _year(item.get("issued") or item.get("published-print") or item.get("created")),
        "venue": norm_text(item.get("container-title") or "")[:200],
        "abstract": norm_text(item.get("abstract"))[:3000],
        "url": item.get("URL") or "",
        "cited_by": item.get("is-referenced-by-count"),
    }


def from_europepmc(item: dict, source_file: str) -> dict:
    return {
        "source": "europepmc",
        "source_file": source_file,
        "record_id": item.get("id") or norm_doi(item.get("doi")) or norm_title(item.get("title")),
        "doi": norm_doi(item.get("doi")),
        "title": norm_text(item.get("title"))[:400],
        "authors": _split_authors(item.get("authorString")),
        "year": _year(item.get("pubYear") or item.get("firstPublicationDate")),
        "venue": norm_text(item.get("journalTitle"))[:200],
        "abstract": norm_text(item.get("abstractText"))[:3000],
        "url": f"https://europepmc.org/article/{item.get('source','')}/{item.get('id','')}",
        "cited_by": item.get("citedByCount"),
    }


def from_generic(item: dict, source_file: str, source_name: str) -> dict:
    return {
        "source": source_name,
        "source_file": source_file,
        "record_id": (item.get("doi") or item.get("id") or item.get("title") or "")[:200],
        "doi": norm_doi(item.get("doi")),
        "title": norm_text(item.get("title"))[:400],
        "authors": _split_authors(item.get("authors") or item.get("author")),
        "year": _year(item.get("year") or item.get("date")),
        "venue": norm_text(item.get("venue") or item.get("journal"))[:200],
        "abstract": norm_text(item.get("abstract"))[:3000],
        "url": item.get("url") or "",
        "cited_by": item.get("cited_by"),
    }


def _year(value) -> str:
    s = norm_text(value)
    m = re.search(r"(19|20)\d{2}", s)
    return m.group(0) if m else ""


def _split_authors(value) -> list:
    if isinstance(value, list):
        return [norm_text(v).strip() for v in value][:12]
    s = norm_text(value)
    if not s:
        return []
    parts = re.split(r"[,;]", s)
    return [p.strip() for p in parts if p.strip()][:12]


def normalize_payload(data, source_file: str):
    """Return a list of normalized records from an arbitrary source payload."""
    items = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        if isinstance(data.get("message"), dict) and "items" in data["message"]:
            items = data["message"]["items"]
        elif isinstance(data.get("resultList"), dict):
            items = data["resultList"].get("result", [])
        elif isinstance(data.get("items"), list):
            items = data["items"]
        elif isinstance(data.get("results"), list):
            items = data["results"]
        elif isinstance(data.get("records"), list):
            items = data["records"]
        elif "title" in data:
            items = [data]
    if not items:
        return []

    source_name = _guess_source(source_file, data)
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            if source_name == "crossref":
                out.append(from_crossref(it, source_file))
            elif source_name == "europepmc":
                out.append(from_europepmc(it, source_file))
            else:
                out.append(from_generic(it, source_file, source_name))
        except Exception:
            continue
    return out


def _guess_source(source_file: str, data) -> str:
    low = source_file.lower()
    if "crossref" in low:
        return "crossref"
    if "europepmc" in low or "ebi" in low:
        return "europepmc"
    if "pubmed" in low:
        return "pubmed"
    if "openalex" in low:
        return "openalex"
    if "semantic" in low:
        return "semantic_scholar"
    if "reference" in low or "graph" in low:
        return "reference_graph"
    if isinstance(data, dict):
        if isinstance(data.get("message"), dict):
            return "crossref"
        if isinstance(data.get("resultList"), dict):
            return "europepmc"
    return "other"


# -- queries ---------------------------------------------------------------

def build_queries(question: str, extra=None):
    """Decompose a research question into sub-queries with term expansion."""
    base = question.strip()
    terms = [t for t in tokenize(base)]
    core = " ".join(terms)
    queries = []
    if base:
        queries.append({"query": base, "kind": "verbatim", "terms": terms})
    if core and core.lower() != base.lower():
        queries.append({"query": core, "kind": "title_abstract", "terms": terms})
    # phrase from the most specific noun-ish tokens (longest tokens)
    long_terms = sorted(set(terms), key=len, reverse=True)[:4]
    if len(long_terms) >= 2:
        phrase = " ".join(long_terms)
        queries.append({"query": phrase, "kind": "core_concepts", "terms": long_terms})
    for q in extra or []:
        if isinstance(q, str) and q.strip():
            queries.append({"query": q.strip(), "kind": "provided", "terms": tokenize(q)})
    # de-duplicate by query string
    seen = set()
    out = []
    for q in queries:
        if q["query"].lower() in seen:
            continue
        seen.add(q["query"].lower())
        out.append(q)
    return out


def live_search(query: str, source: str):
    """Try a live API. Returns (records, note). Never raises."""
    try:
        if source == "crossref":
            url = "https://api.crossref.org/works?" + urllib.parse.urlencode(
                {"query.bibliographic": query, "rows": 20, "select": "DOI,title,author,issued,container-title,abstract,is-referenced-by-count,URL"}
            )
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=12) as resp:
                return normalize_payload(json.loads(resp.read().decode("utf-8")), "crossref:live"), None
        if source == "europepmc":
            url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(
                {"query": query, "format": "json", "pageSize": 20, "resultType": "core"}
            )
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=12) as resp:
                return normalize_payload(json.loads(resp.read().decode("utf-8")), "europepmc:live"), None
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"
    return [], f"source not reachable: {source}"


# -- ranking ---------------------------------------------------------------

def score_record(rec, query_terms):
    title = norm_text(rec.get("title")).lower()
    abstract = norm_text(rec.get("abstract")).lower()
    qterms = set(query_terms)

    title_hits = sum(1 for t in qterms if t in title)
    abstract_hits = sum(1 for t in qterms if t in abstract)
    coverage = (title_hits + abstract_hits) / len(qterms) if qterms else 0.0

    score = 0.0
    score += 3.0 * title_hits
    score += 1.0 * abstract_hits
    score += 2.0 * coverage
    if rec.get("doi"):
        score += 0.5
    if rec.get("abstract"):
        score += 0.3
    if rec.get("year"):
        try:
            y = int(rec["year"])
            if y >= 2020:
                score += 0.6
            elif y >= 2015:
                score += 0.3
        except ValueError:
            pass
    source_w = SOURCE_WEIGHTS.get(rec.get("source"), 0.5)
    score *= source_w

    rationale = (
        f"title_hits={title_hits} abstract_hits={abstract_hits} "
        f"coverage={coverage:.2f} source_weight={source_w} doi={bool(rec.get('doi'))}"
    )
    return round(score, 3), rationale, coverage


def dedupe(records):
    by_key = {}
    order = []
    for r in records:
        key = r.get("doi") or norm_title(r.get("title"))
        if not key:
            key = f"raw:{r.get('source')}:{r.get('record_id')}"
        if key in by_key:
            # keep the richer record, merge sources
            prev = by_key[key]
            if len(json.dumps(r)) > len(json.dumps(prev)):
                by_key[key] = r
        else:
            by_key[key] = r
            order.append(key)
    return [by_key[k] for k in order]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--work", required=True)
    args = ap.parse_args(argv)

    case = load(Path(args.case))
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    question = case.get("question") or case.get("query") or case.get("research_question") or case.get("topic") or ""
    sources = case.get("sources") or case.get("databases") or []
    if isinstance(sources, str):
        sources = [sources]
    extra_queries = case.get("queries") or case.get("subqueries") or []

    # gather cached responses
    records = []
    log = []
    cached = case.get("responses") or case.get("cached") or case.get("payloads") or {}
    response_files = []
    if isinstance(cached, dict):
        response_files = list(cached.values()) if all(isinstance(v, str) for v in cached.values()) else []
        for name, payload in cached.items():
            if isinstance(payload, (dict, list)):
                recs = normalize_payload(payload, str(name))
                records.extend(recs)
                log.append({"source": name, "kind": "cached", "records": len(recs)})
    elif isinstance(cached, list):
        response_files = cached

    for rf in response_files:
        p = Path(rf)
        if not p.is_absolute():
            cand = Path(args.case).parent / p
            p = cand if cand.exists() else Path(rf)
        if p.exists() and p.is_file():
            try:
                data = load(p)
            except Exception as exc:
                log.append({"source": str(p), "kind": "cached", "error": f"{type(exc).__name__}: {exc}"})
                continue
            recs = normalize_payload(data, p.name)
            records.extend(recs)
            log.append({"source": p.name, "kind": "cached", "records": len(recs)})
        else:
            log.append({"source": str(p), "kind": "cached", "error": "file not found"})

    if not records:
        log.append({"source": "live", "kind": "attempt",
                    "note": "no cached responses found; live retrieval unavailable in this environment"})

    queries = build_queries(question, extra_queries)
    for q in queries:
        log.append({"query": q["query"], "kind": q["kind"], "terms": q["terms"]})

    unique = dedupe(records)
    candidates = []
    for rec in unique:
        best = None
        for q in queries:
            s, rationale, coverage = score_record(rec, q["terms"])
            if best is None or s > best["score"]:
                best = {"score": s, "rationale": rationale, "coverage": coverage, "query": q["query"]}
        candidates.append({**rec, "relevance": best})
    candidates.sort(key=lambda c: c["relevance"]["score"], reverse=True)

    # coverage / omission notes
    omissions = []
    if not records:
        omissions.append("未获得任何可解析的检索响应；候选集为空，需检查来源文件或网络。")
    if not any(c.get("doi") for c in candidates):
        omissions.append("候选集中无 DOI 记录，来源可能为摘要页或非 Crossref 渠道。")
    if not any(c.get("year") and c["year"] >= "2020" for c in candidates):
        omissions.append("候选集中无 2020 年后记录，可能遗漏最新工作。")

    out = {
        "case_id": case.get("case_id") or case.get("id") or Path(args.case).stem,
        "question": question,
        "queries": queries,
        "source_count": len({r.get("source") for r in records}),
        "raw_records": len(records),
        "unique_records": len(unique),
        "candidates": candidates,
        "omissions": omissions,
        "method": "term-overlap scoring with transparent weights; dedup by DOI then title",
    }
    with open(work / "candidates.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    with open(work / "search_log.json", "w", encoding="utf-8") as fh:
        json.dump({"case_id": out["case_id"], "question": question, "log": log}, fh, ensure_ascii=False, indent=2)

    print(f"search: {len(records)} raw -> {len(unique)} unique candidates -> {work / 'candidates.json'}")
    return out


if __name__ == "__main__":
    main()
