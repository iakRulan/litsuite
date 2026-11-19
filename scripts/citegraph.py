#!/usr/bin/env python3
"""citegraph — citation tracing and related-literature discovery.

Usage:
    python3 citegraph.py --case /input/case.json --work /app/work

The case supplies seed records, each with an identifier and a list of cited
identifiers (a retained reference graph, or records with `references`/`cites`
fields). This script:

  1. normalizes identifiers (DOI, title) into one key space;
  2. builds a directed citation graph (seed -> cited);
  3. computes forward (cited-by direction), backward (references) and 2-hop
     neighbourhoods for each seed;
  4. finds connected clusters / bridgers;
  5. writes graph.json, related.json and result.json.

Every edge carries the source record id and the field it came from, so a
related-paper claim can be traced back to the input graph.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict, deque
from pathlib import Path

VERSION = "1.0.0"


def load(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def norm_key(value) -> str:
    if value is None:
        return ""
    s = str(value).strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def rec_id(rec, index):
    if isinstance(rec, str):
        return rec
    return str(rec.get("id") or rec.get("doi") or rec.get("title") or rec.get("record_id") or f"R{index+1}")


def rec_refs(rec):
    """Return (list_of_references, field_name)."""
    for field in ("references", "refs", "cited", "cites", "bibliography", "reference_list"):
        v = rec.get(field) if isinstance(rec, dict) else None
        if isinstance(v, list) and v:
            out = []
            for x in v:
                if isinstance(x, str):
                    out.append(x)
                elif isinstance(x, dict):
                    out.append(x.get("doi") or x.get("id") or x.get("title") or "")
            return [o for o in out if o], field
    return [], None


def load_case(case_path: Path) -> dict:
    case_path = Path(case_path)
    if case_path.is_dir():
        return {"case_id": case_path.name, "records": [], "seeds": [], "raw": {}}
    raw = load(case_path)
    case = dict(raw) if isinstance(raw, dict) else {"value": raw}
    recs = (case.get("records") or case.get("papers") or case.get("documents")
            or case.get("graph") or case.get("sources") or [])
    seeds = case.get("seeds") or case.get("seed_ids") or case.get("queries") or []
    return {
        "case_id": case.get("case_id") or case.get("id") or case_path.stem,
        "raw": case,
        "records": recs if isinstance(recs, list) else [recs],
        "seeds": seeds if isinstance(seeds, list) else [seeds],
        "depth": int(case.get("depth") or 2),
        "top_k": int(case.get("top_k") or 20),
    }


def build_graph(records):
    """Return nodes, edges, adjacency, and a key->record map."""
    nodes = {}
    key_to_id = {}
    edges = []
    for i, rec in enumerate(records):
        if isinstance(rec, str):
            rec = {"id": rec, "title": rec}
        rid = rec_id(rec, i)
        title = str(rec.get("title") or "")
        key = norm_key(rec.get("doi") or rec.get("id") or title)
        nodes[rid] = {"id": rid, "title": title, "key": key, "year": rec.get("year") or ""}
        if key:
            key_to_id.setdefault(key, rid)
        refs, field = rec_refs(rec)
        for ref in refs:
            rkey = norm_key(ref)
            target = key_to_id.get(rkey)
            edges.append({
                "source": rid,
                "target_key": rkey,
                "target": target,
                "raw": str(ref)[:200],
                "field": field,
            })
    # resolve targets that appear later in the list
    for e in edges:
        if e["target"] is None and e["target_key"] in key_to_id:
            e["target"] = key_to_id[e["target_key"]]
    adj = defaultdict(list)
    radj = defaultdict(list)
    for e in edges:
        if e["target"]:
            adj[e["source"]].append(e["target"])
            radj[e["target"]].append(e["source"])
    return nodes, edges, adj, radj


def neighbourhood(start, adj, radj, depth):
    """BFS over both directions up to `depth` hops."""
    seen = {start: 0}
    q = deque([(start, 0)])
    while q:
        node, d = q.popleft()
        if d >= depth:
            continue
        for nxt in adj.get(node, []) + radj.get(node, []):
            if nxt not in seen:
                seen[nxt] = d + 1
                q.append((nxt, d + 1))
    return seen


def clusters(nodes, adj, radj):
    """Connected components over the undirected graph."""
    seen = set()
    out = []
    for n in nodes:
        if n in seen:
            continue
        comp = []
        q = deque([n])
        seen.add(n)
        while q:
            x = q.popleft()
            comp.append(x)
            for y in adj.get(x, []) + radj.get(x, []):
                if y not in seen:
                    seen.add(y)
                    q.append(y)
        out.append(comp)
    out.sort(key=len, reverse=True)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--work", required=True)
    args = ap.parse_args(argv)

    case = load_case(Path(args.case))
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    nodes, edges, adj, radj = build_graph(case["records"])
    depth = case["depth"]
    seeds = case["seeds"] or list(nodes)

    related = []
    for seed in seeds:
        sid = seed if seed in nodes else next((k for k, v in nodes.items() if v["key"] == norm_key(seed)), None)
        if not sid:
            related.append({"seed": str(seed), "found": False, "note": "种子不在输入图中"})
            continue
        dist = neighbourhood(sid, adj, radj, depth)
        dist.pop(sid, None)
        members = sorted(dist.items(), key=lambda kv: (kv[1], kv[0]))[:case["top_k"]]
        related.append({
            "seed": sid,
            "found": True,
            "neighbours": [
                {"id": mid, "title": nodes[mid]["title"], "hops": h}
                for mid, h in members
            ],
            "counts": {"1-hop": sum(1 for _, h in members if h == 1),
                       "2-hop": sum(1 for _, h in members if h == 2)},
        })

    comps = clusters(nodes, adj, radj)
    bridgers = []
    # a bridger connects two large components in its neighbourhood
    for r in related:
        if not r["found"]:
            continue
        nbrs = set(n["id"] for n in r["neighbours"]) | {r["seed"]}
        touched = [i for i, c in enumerate(comps) if nbrs & set(c)]
        if len(touched) > 1:
            bridgers.append({"seed": r["seed"], "components": touched})

    graph_out = {
        "case_id": case["case_id"],
        "nodes": list(nodes.values()),
        "edges": edges,
        "stats": {"nodes": len(nodes), "edges": len(edges),
                  "resolved_edges": sum(1 for e in edges if e["target"]),
                  "components": len(comps)},
    }
    save(work / "graph.json", graph_out)
    save(work / "related.json", {
        "case_id": case["case_id"],
        "seeds": related,
        "components": [{"size": len(c), "members": c[:50]} for c in comps],
        "bridgers": bridgers,
    })

    result = {
        "case_id": case["case_id"],
        "task": "citation-tracing",
        "status": "complete" if nodes else "partial",
        "deliverables": {"graph": "graph.json", "related": "related.json"},
        "counts": {
            "records": len(case["records"]),
            "nodes": len(nodes),
            "edges": len(edges),
            "resolved_edges": sum(1 for e in edges if e["target"]),
            "components": len(comps),
            "seeds": len(related),
            "bridgers": len(bridgers),
        },
        "uncertainty": [
            "关联发现基于给定引用图，不外推未提供的文献；",
            "标识符归一化可能把不同版本的同一文献合并，需人工确认。",
        ],
        "sources": [str(Path(args.case))],
        "changes": [
            {"step": "graph", "detail": f"{len(nodes)} nodes / {len(edges)} edges"},
            {"step": "discover", "detail": f"{len(related)} seeds, {len(bridgers)} bridgers"},
        ],
    }
    save(work / "result.json", result)
    print(f"citegraph: {len(nodes)} nodes, {len(edges)} edges, "
          f"{len(comps)} components -> {work / 'related.json'}")
    return result


if __name__ == "__main__":
    main()
