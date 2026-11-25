# -*- coding: utf-8 -*-
"""汇总简化方案对照实验：读取 ablation-features.ndjson 与 a-timing.ndjson，输出分类统计表。
用法: python ablation_summarize.py <outDir> [--md 输出文件]
"""
import json
import math
import sys
from collections import defaultdict


def percentile(values, fraction):
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(math.ceil(fraction * len(ordered))) - 1))
    return ordered[index]


def main():
    outdir = sys.argv[1]
    timing_a = {}
    with open(sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else f"{outdir}/../../a-timing.ndjson", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith('{"r"'):
                item = json.loads(line)
                timing_a[(item["r"], item["f"])] = item["ms"]
    all_rows = [json.loads(line) for line in open(f"{outdir}/ablation-features.ndjson", encoding="utf-8") if line.strip()]
    errored = [row for row in all_rows if "err" in row]
    rows = [row for row in all_rows if "err" not in row]
    print(f"图斑 {len(all_rows)}，处理异常 {len(errored)}，空环(A 接受的结果) {sum(1 for row in rows if row.get('A', {}).get('empty_ring'))}")
    categories = [
        ("全部", lambda row: True),
        ("普通", lambda row: not (row["bt"] or row["mr"] or row["mp"] or row["big"])),
        ("贴边界", lambda row: row["bt"] == 1),
        ("多环", lambda row: row["mr"] == 1),
        ("MultiPolygon", lambda row: row["mp"] == 1),
        ("超限(>5000)", lambda row: row["big"] == 1),
    ]
    variants = ["A", "B", "C", "D", "B2", "C2"]
    names = {"A": "A 已部署(turf DP+采样)", "B": "B 原生TPS", "C": "C 原生TPS+校验重试", "D": "D 边界保护",
             "B2": "B' 原生DP", "C2": "C' 原生DP+校验重试"}
    report = {}
    lines = []
    for category, predicate in categories:
        subset = [row for row in rows if predicate(row)]
        lines.append(f"\n### 类别：{category}（{len(subset)} 个图斑）\n")
        lines.append("| 方案 | 产出变化 | 越界率 | 非法率 | 有效简化比例 | 拒绝/跳过 | 顶点压缩(总量/中位) | 面积变化 中位/P95/最大 | 贴边长度保持 | 切掉ROI拐点的图斑 | P50/P95/P99/最大 ms |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        report[category] = {"count": len(subset)}
        for variant in variants:
            changed = [row for row in subset if row[variant]["o"] == "a"]
            outside = [row for row in changed if row[variant]["c"] == "O"]
            invalid = [row for row in changed if row[variant]["c"] == "I"]
            legal = [row for row in changed if row[variant]["c"] == "-"]
            skipped = sum(1 for row in subset if row[variant]["o"] == "s")
            compression = 1 - sum(row[variant]["n2"] for row in legal) / sum(row["n"] for row in legal) if legal else float("nan")
            median_compression = percentile([1 - row[variant]["n2"] / row["n"] for row in legal], 0.5) if legal else float("nan")
            area_change = [abs(row[variant]["a2"] - row["area"]) / row["area"] for row in legal if row["area"] > 0]
            touching_legal = [row for row in legal if row["bt"]]
            obl = sum(row[variant]["obl2"] for row in touching_legal) / sum(row["obl"] for row in touching_legal) if touching_legal else float("nan")
            cut = sum(1 for row in changed if row["bt"] and row[variant].get("cc", 0) > 0)
            if variant == "A":
                times = [timing_a[(row["r"], row["f"])] for row in subset if (row["r"], row["f"]) in timing_a]
            else:
                times = [row[variant + "_ms"] for row in subset]
            max_out = max([row[variant].get("out") or 0 for row in changed] or [0]) * 111320.0 ** 2
            stats = {
                "count": len(subset), "changed": len(changed), "outside": len(outside), "invalid": len(invalid),
                "legal_changed": len(legal), "skipped": skipped,
                "effective_rate": len(legal) / len(subset) if subset else float("nan"),
                "outside_rate": len(outside) / len(changed) if changed else float("nan"),
                "invalid_rate": len(invalid) / len(changed) if changed else float("nan"),
                "compression_total": compression, "compression_median": median_compression,
                "area_median": percentile(area_change, 0.5), "area_p95": percentile(area_change, 0.95),
                "area_max": max(area_change) if area_change else float("nan"),
                "boundary_length_kept": obl, "corner_cut_features": cut, "max_outside_m2": max_out,
                "ms_p50": percentile(times, 0.5), "ms_p95": percentile(times, 0.95), "ms_p99": percentile(times, 0.99),
                "ms_max": max(times) if times else float("nan"),
            }
            report[category][variant] = stats
            if variant in ("A", "B", "C", "D"):
                lines.append(
                    f"| {names[variant]} | {len(changed)} ({len(changed) / len(subset):.1%}) | {len(outside)} ({stats['outside_rate']:.2%}) | "
                    f"{len(invalid)} ({stats['invalid_rate']:.2%}) | {len(legal)} ({stats['effective_rate']:.1%}) | {skipped} | "
                    f"{compression:.1%} / {median_compression:.1%} | {stats['area_median']:.3%} / {stats['area_p95']:.3%} / {stats['area_max']:.3%} | "
                    f"{obl:.2%} | {cut} | {stats['ms_p50']:.1f} / {stats['ms_p95']:.1f} / {stats['ms_p99']:.1f} / {stats['ms_max']:.0f} |"
                )
    text = "\n".join(lines)
    print(text)
    if "--md" in sys.argv:
        path = sys.argv[sys.argv.index("--md") + 1]
        open(path, "w", encoding="utf-8").write(text + "\n")
        open(path.replace(".md", ".json"), "w", encoding="utf-8").write(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
