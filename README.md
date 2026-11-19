# litsuite — 文献检索综合 Skill

一条管线覆盖检索赛道：检索 → 筛选 → 排序 → 引文追踪 → 覆盖率。

## 依赖
- Python 3.9+，仅标准库；同目录 `retrieve.py`、`citegraph.py`。

## 用法
```bash
python3 scripts/litsuite.py --case /input/case.json --work /app/work
```

输入示例：
```json
{
  "case_id": "demo",
  "question": "attention for translation",
  "responses": ["crossref_cache.json"],
  "screening": {"include": ["attention"], "exclude": ["survey"]},
  "gold": ["10.1/a"],
  "graph_records": [{"id": "10.1/a", "references": ["10.1/b"]}],
  "seeds": ["10.1/a"]
}
```
> 示意示例，不是正式案例结果。

## 输出
| 文件 | 内容 |
|-|-|
| `candidates.json` | 纳入/剔除集、查询计划、筛选条件、分来源计数、覆盖率 |
| `graph.json` / `related.json` | 引文图与种子邻域（有图数据时） |
| `result.json` | `case_id` / `status` / counts / coverage / omissions |

## 关键设计
出处可追溯；筛选有理由；排序透明；引文邻域与桥接；对金标准测 recall@k；遗漏显式。

## 局限
- 词面匹配不识别同义词/跨语言；无网络时只用缓存。

## 版本
litsuite 1.0.0，纯 Python 标准库。
