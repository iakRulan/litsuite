---
name: litsuite
description: "One literature pipeline covering search, screening, citation tracing and coverage: collect from the supplied sources, normalize, dedupe, screen with explicit criteria, rank transparently, build the citation graph and its hop neighbourhoods, and measure recall against an optional gold set. Trigger on: '文献检索', '系统检索', '多数据库检索', '覆盖率分析', '纳排筛选', '最新文献追踪', 'literature search suite', 'screening and ranking', 'recall analysis'. Not for: reading a single paper, writing, or drawing."
---

# litsuite — Literature Search Suite (retrieve · screen · trace · coverage)

> **脚本路径**：本技能安装于 `/skills/weekly-skill/`，脚本绝对路径为 `/skills/weekly-skill/scripts/`（工作目录是 `/app/work`，不要用相对路径）。

One pipeline for the whole retrieval track. The failure mode this skill exists to prevent: a result set with no screening rationale, no source provenance, no coverage statement, and no citation context.

**Method in one line:** collect → normalize → dedupe → screen (include/exclude/must-have/year) → rank transparently → build citation graph + hop neighbourhoods → measure recall@k against a gold set → report included / excluded / omissions.

## When to use

- Input: a case file (default `/input/case.json`) with a question, sources/cached responses, optional `screening`, `graph_records`/`seeds`, `gold`.
- Output: under `/app/output` — `candidates.json`, `graph.json`, `related.json`, `result.json`.

## Pipeline

```bash
python3 /skills/weekly-skill/scripts/litsuite.py --case /input/case.json --work /app/work
```

1. **Retrieve** — multi-source normalization (Crossref `message.items`, EuropePMC `resultList.result`, generic lists), query plan, DOI/title dedup.
2. **Screen** — `exclude` term hit → out; no `include` hit → out; missing `must_have` → out; year outside window → out. Each decision has a reason.
3. **Rank** — `(3·title_hits + 1·abstract_hits + 2·coverage + 0.5·doi + 0.3·abstract + recency) · source_weight`, rationale stored.
4. **Trace** — citation graph, 1/2-hop neighbourhoods per seed, connected components, bridgers.
5. **Coverage** — `recall@5/10/20/50` against `gold` when supplied.
6. **Report** — included, excluded, per-source counts, coverage, omissions.

## Rules that must hold

1. **Provenance per record** (`source_file` + `record_id`).
2. **Every screening decision has a reason.**
3. **No pooling of unlike records**; dedup by identity.
4. **Omissions are stated** (empty result, no DOI, recall < 100%).
5. **No fabricated metadata.**

## Exception handling
| Situation | Behaviour |
|-|-|
| No responses | `status: partial`, omissions explains |
| No screening criteria | all kept, reason noted |
| No graph records | graph skipped |
| No gold set | coverage `null` |
| Source unreachable | logged, cached fallback |

## Self-check
```bash
python3 -c "import json;c=json.load(open('/app/work/candidates.json'));print(c['counts'] if 'counts' in c else (c['raw_records'], len(c['included']), len(c['excluded'])))"
python3 -c "import json;r=json.load(open('/app/work/result.json'));print(r['case_id'], r['status'], r['counts'])"
```