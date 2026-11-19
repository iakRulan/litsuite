# 输出字段字典

## `candidates.json`
`case_id` / `question` / `queries[]` / `screening_criteria{}` / `sources[]` / `raw_records` / `unique_records` / `included[]`（含 relevance + screening）/ `excluded[]` / `per_source_counts{}` / `coverage{}` / `method`

## `result.json`
`case_id` / `task` / `status` / `deliverables` / `counts{raw_records,unique_records,included,excluded,sources,graph_nodes,graph_edges}` / `coverage` / `top_candidates` / `omissions` / `uncertainty` / `sources` / `changes`

## `graph.json` / `related.json`
见 citegraph 的字段字典。
