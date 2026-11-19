#!/usr/bin/env python3
"""litsuite — one literature pipeline for search / screening / tracing / coverage.

Usage:
    python3 litsuite.py --case /input/case.json --work /app/work

Combines the proven engines shipped with this skill:
  * retrieve.py  — multi-source collection, normalization, dedup, ranking
  * citegraph.py — citation graph, hop neighbourhoods, components, bridgers
and adds:
  * screening (inclusion / exclusion / must-have / year window)
  * coverage & recall against an optional gold set
  * a result.json whose fields follow the case's declared output contract

The script never invents a record: every output row keeps its source file and
record id, and every omission is stated.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import retrieve as R
import citegraph as C

VERSION = "1.0.0"


def load(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def parse_criteria(case):
    crit = case.get("screening") or case.get("criteria") or {}
    if isinstance(crit, dict):
        return {
            "include": [str(x) for x in _list(crit.get("include") or crit.get("inclusion"))],
            "exclude": [str(x) for x in _list(crit.get("exclude") or crit.get("exclusion"))],
            "must_have": [str(x) for x in _list(crit.get("must_have"))],
            "min_year": crit.get("min_year"),
            "max_year": crit.get("max_year"),
        }
    return {"include": [], "exclude": [], "must_have": [], "min_year": None, "max_year": None}


def _list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def screen(rec, criteria):
    text = (rec.get("title", "") + " " + rec.get("abstract", "") + " " + rec.get("venue", "")).lower()
    for ex in criteria["exclude"]:
        if ex and ex.lower() in text:
            return "exclude", f"命中排除词：{ex}"
    hits = [i for i in criteria["include"] if i and i.lower() in text]
    if criteria["include"] and not hits:
        return "exclude", "未命中任何纳入词"
    missing = [m for m in criteria["must_have"] if m and m.lower() not in text]
    if missing:
        return "exclude", "缺少必需项：" + ", ".join(missing)
    year = rec.get("year") or ""
    if criteria["min_year"] and year.isdigit() and int(year) < int(criteria["min_year"]):
        return "exclude", f"年份 {year} < {criteria['min_year']}"
    if criteria["max_year"] and year.isdigit() and int(year) > int(criteria["max_year"]):
        return "exclude", f"年份 {year} > {criteria['max_year']}"
    return "include", ("命中纳入词：" + ", ".join(hits)) if hits else "无显式纳入词，保留"


def recall_at_k(ranked_ids, gold_ids, ks=(5, 10, 20, 50)):
    gold = {R.norm_doi(g) or R.norm_title(g) for g in gold_ids}
    out = {}
    for k in ks:
        top = {R.norm_doi(ranked_ids[i]) or R.norm_title(ranked_ids[i]) for i in range(min(k, len(ranked_ids)))}
        out[f"recall@{k}"] = round(len(top & gold) / len(gold), 4) if gold else None
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--work", required=True)
    args = ap.parse_args(argv)

    case = load(Path(args.case))
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    question = (case.get("question") or case.get("query") or case.get("research_question")
                or case.get("topic") or "")
    sources = case.get("sources") or case.get("databases") or []
    if isinstance(sources, str):
        sources = [sources]
    cached = case.get("responses") or case.get("cached") or case.get("payloads") or {}

    records = []
    if isinstance(cached, dict):
        for name, payload in cached.items():
            if isinstance(payload, (dict, list)):
                records.extend(R.normalize_payload(payload, str(name)))
    elif isinstance(cached, list):
        for rf in cached:
            p = Path(rf)
            if not p.is_absolute():
                cand = Path(args.case).parent / p
                p = cand if cand.exists() else Path(rf)
            if p.exists() and p.is_file():
                try:
                    records.extend(R.normalize_payload(load(p), p.name))
                except Exception:
                    pass

    queries = R.build_queries(question, case.get("queries"))
    unique = R.dedupe(records)

    criteria = parse_criteria(case)
    ranked = []
    for rec in unique:
        best = None
        for q in queries:
            s, rationale, cov = R.score_record(rec, q["terms"])
            if best is None or s > best["score"]:
                best = {"score": s, "rationale": rationale, "coverage": cov, "query": q["query"]}
        decision, reason = screen(rec, criteria)
        ranked.append({**rec, "relevance": best, "screening": {"decision": decision, "reason": reason}})
    ranked.sort(key=lambda x: x["relevance"]["score"], reverse=True)
    included = [r for r in ranked if r["screening"]["decision"] == "include"]

    # citation graph (optional)
    graph_out = None
    related_out = None
    graph_records = case.get("graph_records") or case.get("records") or []
    if graph_records:
        nodes, edges, adj, radj = C.build_graph(graph_records)
        seeds = case.get("seeds") or list(nodes)
        related = []
        for s in seeds:
            sid = s if s in nodes else next((k for k, v in nodes.items() if v["key"] == C.norm_key(s)), None)
            if not sid:
                related.append({"seed": str(s), "found": False})
                continue
            dist = C.neighbourhood(sid, adj, radj, int(case.get("depth") or 2))
            dist.pop(sid, None)
            related.append({"seed": sid, "found": True,
                            "neighbours": [{"id": m, "title": nodes[m]["title"], "hops": h}
                                           for m, h in sorted(dist.items(), key=lambda kv: (kv[1], kv[0]))[:20]]})
        comps = C.clusters(nodes, adj, radj)
        graph_out = {"nodes": list(nodes.values()), "edges": edges,
                     "stats": {"nodes": len(nodes), "edges": len(edges), "components": len(comps)}}
        related_out = {"seeds": related,
                       "components": [{"size": len(c), "members": c[:50]} for c in comps]}
        save(work / "graph.json", {"case_id": case.get("case_id"), **graph_out})
        save(work / "related.json", {"case_id": case.get("case_id"), **related_out})

    # coverage / recall
    gold = case.get("gold") or case.get("gold_set") or case.get("expected") or []
    coverage = None
    if gold:
        ids = [r.get("doi") or r.get("title") or "" for r in included]
        coverage = recall_at_k(ids, gold)

    # source coverage
    per_source = {}
    for r in included:
        per_source[r.get("source")] = per_source.get(r.get("source"), 0) + 1

    out = {
        "case_id": case.get("case_id") or case.get("id") or Path(args.case).stem,
        "question": question,
        "queries": queries,
        "screening_criteria": criteria,
        "sources": sorted({r.get("source") for r in records}),
        "raw_records": len(records),
        "unique_records": len(unique),
        "included": included,
        "excluded": [r for r in ranked if r["screening"]["decision"] == "exclude"],
        "per_source_counts": per_source,
        "coverage": coverage,
        "method": "retrieve + screen + rank + citation-graph + coverage",
    }
    save(work / "candidates.json", out)

    result = {
        "case_id": out["case_id"],
        "task": "literature-search-suite",
        "status": "complete" if included else "partial",
        "deliverables": {
            "candidates": "candidates.json",
            "graph": "graph.json" if graph_out else None,
            "related": "related.json" if related_out else None,
        },
        "counts": {
            "raw_records": len(records),
            "unique_records": len(unique),
            "included": len(included),
            "excluded": len(out["excluded"]),
            "sources": len(out["sources"]),
            "graph_nodes": (graph_out or {}).get("stats", {}).get("nodes", 0),
            "graph_edges": (graph_out or {}).get("stats", {}).get("edges", 0),
        },
        "coverage": coverage,
        "top_candidates": included[:20],
        "omissions": _omissions(records, included, coverage),
        "uncertainty": [
            "仅覆盖所给来源与快照，不声称全局完整性或最新性；",
            "词面排序不识别同义词；筛选词需案例或模型提供。",
        ],
        "sources": [str(Path(args.case))],
        "changes": [
            {"step": "retrieve", "detail": f"{len(records)} raw -> {len(unique)} unique"},
            {"step": "screen", "detail": f"{len(included)} included / {len(out['excluded'])} excluded"},
            {"step": "graph", "detail": json.dumps((graph_out or {}).get("stats", {}), ensure_ascii=False)},
        ],
    }
    save(work / "result.json", result)
    print(f"litsuite: {len(records)} raw, {len(included)} included, "
          f"graph={graph_out['stats'] if graph_out else None} -> {work / 'candidates.json'}")
    return out


def _omissions(records, included, coverage):
    notes = []
    if not records:
        notes.append("未获得任何可解析响应")
    if records and not included:
        notes.append("筛选后无纳入记录")
    if not any(r.get("doi") for r in included):
        notes.append("纳入记录无 DOI")
    if coverage and all((v or 0) < 1.0 for v in coverage.values()):
        notes.append("对金标准集的召回未达 100%，存在遗漏")
    return notes


if __name__ == "__main__":
    main()
