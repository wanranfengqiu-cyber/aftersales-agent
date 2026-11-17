"""
故障注入实验:无故障 + 5 类故障,× 4 版执行器,每格跑 N 次,统计
  执行成功率(执行器说成功,且后台恰好有一条正确的退款)
  谎报成功(执行器说成功,但后台没退成或退错)
  重复退款(同一个退款请求,后台出现多条记录)
  漏报(执行器说失败,但后台其实退了)
  转人工、平均耗时、模型调用次数

用法(在 D:\\learn\\aftersales-agent\\executor 目录,用装了 playwright 的 Python):
  python run_fault_injection.py --runs 2 --faults none,popup --versions v1_fixed,v4_selfheal --headed   # 先小样本看一眼
  python run_fault_injection.py --runs 20                                                               # 正式实验
输出:results\\fault_injection_<时间>.csv(每次运行一行)和 .md(汇总表)
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict

from playwright.sync_api import sync_playwright

from rpa_executor import VERSIONS, RefundRequest, execute

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "http://127.0.0.1:8765"
FAULTS = ["none", "delay", "popup", "element_change", "login_expired", "submit_timeout"]
FAULT_ZH = {"none": "无故障", "delay": "随机延迟", "popup": "弹窗遮挡", "element_change": "按钮改版",
            "login_expired": "登录过期", "submit_timeout": "提交超时(实际已成功)"}
VERSION_ZH = {"v1_fixed": "固定脚本", "v2_retry": "+重试", "v3_verify": "+幂等查重与回读核验", "v4_selfheal": "+LLM 自愈定位"}
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本机地址不走代理


def admin(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method="GET" if data is None else "POST",
                                 headers={"Content-Type": "application/json"})
    with _opener.open(req, timeout=30) as r:
        return json.loads(r.read())


def start_backend():
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "merchant_backend:app", "--port", "8765",
                             "--log-level", "warning"], cwd=HERE)
    for _ in range(60):
        try:
            admin("/admin/health")
            return proc
        except Exception:
            time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("模拟后台没有启动起来,看上面的报错")


def summarize(rows, runs):
    cell = defaultdict(lambda: defaultdict(int))
    for r in rows:
        c = cell[(r["fault"], r["version"])]
        c["n"] += 1
        for k in ("success", "false_success", "duplicate", "missed", "transfer"):
            c[k] += int(r[k])
        c["seconds"] += r["seconds"]
        c["llm_calls"] += r["llm_calls"]
    versions = [v for v in VERSIONS if any(r["version"] == v for r in rows)]
    faults = [f for f in FAULTS if any(r["fault"] == f for r in rows)]
    lines = [f"# 故障注入结果(每格 {runs} 次)", "",
             "格子里:成功率(谎报成功 / 重复退款 / 漏报 / 转人工 次数)", "",
             "| 故障 | " + " | ".join(VERSION_ZH[v] for v in versions) + " |",
             "|---|" + "---|" * len(versions)]
    total = defaultdict(lambda: defaultdict(int))
    for f in faults:
        cells = []
        for v in versions:
            c = cell[(f, v)]
            for k, val in c.items():
                total[v][k] += val
            cells.append(f"{c['success'] / c['n']:.0%}({c['false_success']}/{c['duplicate']}/{c['missed']}/{c['transfer']})" if c["n"] else "-")
        lines.append(f"| {FAULT_ZH[f]} | " + " | ".join(cells) + " |")
    lines.append("| **合计** | " + " | ".join(
        f"**{total[v]['success'] / total[v]['n']:.0%}**({total[v]['false_success']}/{total[v]['duplicate']}/{total[v]['missed']}/{total[v]['transfer']})"
        for v in versions) + " |")
    lines += ["", "| 版本 | 平均耗时(秒/次) | 模型调用(次) |", "|---|---|---|"]
    for v in versions:
        lines.append(f"| {VERSION_ZH[v]} | {total[v]['seconds'] / total[v]['n']:.1f} | {total[v]['llm_calls']} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--faults", default=",".join(FAULTS))
    ap.add_argument("--versions", default=",".join(VERSIONS))
    ap.add_argument("--headed", action="store_true", help="显示浏览器窗口,看它怎么点")
    a = ap.parse_args()
    faults, versions = a.faults.split(","), a.versions.split(",")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(HERE, "results")
    os.makedirs(out_dir, exist_ok=True)
    shot_dir = os.path.join(out_dir, f"screenshots_{stamp}")
    rows = []
    proc = start_backend()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not a.headed)
            total, k = len(faults) * len(versions) * a.runs, 0
            for fault in faults:
                for version in versions:
                    for i in range(a.runs):
                        k += 1
                        admin("/admin/reset", {})
                        admin("/admin/config", {"fault": fault, "seed": i})
                        ctx = browser.new_context()
                        page = ctx.new_page()
                        req = RefundRequest(order_id=f"W{1001 + i % 20}", amount=round(10 + i * 1.5, 2))
                        t0 = time.time()
                        res = execute(page, req, version, BASE, shot_dir=shot_dir)
                        dt = time.time() - t0
                        ctx.close()
                        truth = admin(f"/admin/refunds?idem_key={req.idem_key}")
                        n = len(truth)
                        correct = n == 1 and truth[0]["order_id"] == req.order_id and abs(truth[0]["amount"] - req.amount) < 1e-6
                        said_ok = res.status == "success"
                        row = {"fault": fault, "version": version, "run": i, "status": res.status, "refunds_in_db": n,
                               "success": said_ok and correct, "false_success": said_ok and not correct,
                               "duplicate": n > 1, "missed": (not said_ok) and n >= 1,
                               "transfer": res.status == "transfer_to_human", "seconds": round(dt, 1),
                               "attempts": res.attempts, "llm_calls": res.llm_calls,
                               "heal": json.dumps(res.heal_clicks, ensure_ascii=False),
                               "screenshot": res.screenshot or "", "notes": " | ".join(res.notes)[:400]}
                        rows.append(row)
                        flag = "✅" if row["success"] else ("❗谎报" if row["false_success"] else ("❗重复" if row["duplicate"] else "❌"))
                        print(f"[{k}/{total}] {FAULT_ZH[fault]:<8} {VERSION_ZH[version]:<12} 第{i + 1}次 {flag} "
                              f"状态={res.status} 后台记录={n} 用时={dt:.1f}s")
            browser.close()
    finally:
        proc.terminate()
        if rows:
            csv_path = os.path.join(out_dir, f"fault_injection_{stamp}.csv")
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            md = summarize(rows, a.runs)
            with open(os.path.join(out_dir, f"fault_injection_{stamp}.md"), "w", encoding="utf-8") as f:
                f.write(md + "\n")
            print("\n" + md)
            print(f"\n明细:{csv_path}")


if __name__ == "__main__":
    main()
