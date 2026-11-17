"""
离线回放:把官方基线的每一次写操作拿出来,用 my_guards 的规则检查一遍。
用途:① 看规则会不会误拦正确的写操作(成功的对话里被拦 = 规则太严);
     ② 统计基线有多少次"违规写操作"。
用法:python replay_guards.py <results.json> <my_guards.py 所在目录>
"""
import json, sys
from collections import Counter, defaultdict
sys.path.insert(0, sys.argv[2])
import my_guards as G

d = json.load(open(sys.argv[1], encoding="utf-8"))
stat = Counter(); flagged = defaultdict(Counter); examples = defaultdict(list)
for s in d["simulations"]:
    ok = s["reward_info"]["reward"] == 1.0
    mem = G.new_memory(); hist = []; calls = {}
    for m in s["messages"]:
        if m["role"] == "assistant" and m.get("tool_calls"):
            for c in m["tool_calls"]:
                calls[c["id"]] = c
                if c["name"] in G.WRITE_TOOLS:
                    stat["写操作(成功对话)" if ok else "写操作(失败对话)"] += 1
                    v = G.check_write(c, mem, hist, n_calls_in_msg=len(m["tool_calls"]))
                    if v:
                        stat["被拦(成功对话)" if ok else "被拦(失败对话)"] += 1
                    for rule, why in v:
                        flagged["成功对话" if ok else "失败对话"][rule] += 1
                        if ok and len(examples[rule]) < 3:
                            examples[rule].append((s["task_id"], s["trial"], c["name"], why[:90],
                                                   (G._last_user_text(hist) or "")[:120]))
        if m["role"] == "tool" and m.get("id") in calls:
            G.update_memory(mem, calls[m["id"]], m)
        hist.append({"role": m["role"], "content": m.get("content"), "tool_calls": m.get("tool_calls"),
                     "id": m.get("id"), "error": m.get("error")})
print(dict(stat))
for k, c in flagged.items():
    print(k, dict(c))
print("\n成功对话里被拦的例子(用来判断规则是不是太严):")
for rule, ex in examples.items():
    for e in ex:
        print(" ", rule, e)
