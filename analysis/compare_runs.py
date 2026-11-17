"""
比较几次 τ³-bench 运行:pass^1–pass^4、按官方 train/test 划分、按题自助抽样的 95% 置信区间、逐题差别。
只用标准库。
用法:
  python compare_runs.py --base 基线=path/to/baseline/results.json --run 自研=path/to/my_agent/results.json \
                         [--run 自查版=...] [--split path/to/retail/split_tasks.json]
"""
import argparse
import json
import random
from collections import defaultdict
from math import comb


def load(path):
    m = defaultdict(list)
    for s in json.load(open(path, encoding="utf-8"))["simulations"]:
        m[s["task_id"]].append(s["reward_info"]["reward"] == 1.0)
    return m


def pk(m, k, ts):
    return sum(comb(sum(m[t]), k) / comb(len(m[t]), k) for t in ts) / len(ts)


def ci(a, b, ts, k, n=5000, seed=0):
    rnd = random.Random(seed)
    d = sorted(pk(a, k, s) - pk(b, k, s) for s in ([rnd.choice(ts) for _ in ts] for _ in range(n)))
    return d[int(0.025 * n)], d[int(0.975 * n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="名字=results.json")
    ap.add_argument("--run", action="append", required=True, help="名字=results.json,可多次")
    ap.add_argument("--split", default="", help="retail 的 split_tasks.json")
    a = ap.parse_args()
    bname, bpath = a.base.split("=", 1)
    base = load(bpath)
    runs = [(n, load(p)) for n, p in (r.split("=", 1) for r in a.run)]
    groups = [("全部", sorted(base, key=int))]
    if a.split:
        sp = json.load(open(a.split, encoding="utf-8"))
        groups += [(f"{g}({len(sp[g])} 题)", sp[g]) for g in ("train", "test")]
    print("| 题目 | 运行 | pass^1 | pass^2 | pass^3 | pass^4 | pass^1 差值 95% 区间 | pass^4 差值 95% 区间 |")
    print("|---|---|---|---|---|---|---|---|")
    for gname, ts in groups:
        print(f"| {gname} | {bname} | " + " | ".join(f"{pk(base, k, ts):.3f}" for k in range(1, 5)) + " | — | — |")
        for n, m in runs:
            c1, c4 = ci(m, base, ts, 1), ci(m, base, ts, 4)
            print(f"| {gname} | {n} | " + " | ".join(f"{pk(m, k, ts):.3f}" for k in range(1, 5))
                  + f" | [{c1[0]:+.3f}, {c1[1]:+.3f}] | [{c4[0]:+.3f}, {c4[1]:+.3f}] |")
    for n, m in runs:
        better = sum(sum(m[t]) > sum(base[t]) for t in base)
        worse = sum(sum(m[t]) < sum(base[t]) for t in base)
        print(f"\n{n} 相比 {bname}:变好 {better} 题,变差 {worse} 题")


if __name__ == "__main__":
    main()
