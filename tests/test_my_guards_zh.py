"""
中文领域写前校验 my_guards_zh 的单测。只依赖标准库 + pytest。
核心检查:用 50 个任务的数据,把"参考答案里的提交"喂给校验,应该全部放行;
把"不该提交却提交"的情况(超七日、不适用商品、不完好、超 2000 元、运费算错)喂进去,应该被拦。
"""
import json
import os
import sys

HERE = os.path.dirname(__file__)
for p in [os.path.join(HERE, "..", "tau2_agent"), os.path.join(HERE, "..", "..", "tau2-bench", "src", "tau2", "agent"),
          os.environ.get("MY_GUARDS_DIR", "")]:
    if p and os.path.exists(os.path.join(p, "my_guards_zh.py")):
        sys.path.insert(0, p)
        break
sys.path.insert(0, os.path.join(HERE, "..", "zh_domain"))
import my_guards_zh as G  # noqa: E402
import build_zh_domain as B  # noqa: E402  (导入时就在内存里生成好 50 个任务和数据库)


def memory_for(oid):
    o = B.orders[oid]
    uid = o["user_id"]
    m = G.new_memory()
    G.update_memory(m, {"name": "find_user_by_phone"}, {"content": uid})
    G.update_memory(m, {"name": "get_user_details"}, {"content": json.dumps(B.users[uid])})
    G.update_memory(m, {"name": "get_order_details"}, {"content": json.dumps(o)})
    G.update_memory(m, {"name": "get_logistics"}, {"content": json.dumps(B.logistics[oid])})
    return m


def hist(task, last="确认,提交吧。"):
    return [{"role": "user", "content": task["user_scenario"]["instructions"]["reason_for_call"]},
            {"role": "assistant", "content": "以上是退货细节,请确认。"},
            {"role": "user", "content": last}]


def naive_call(oid):
    """'不看规则直接退'的提交:退第一件非赠品,运费按包邮规则算对,赠品都退回。"""
    o = B.orders[oid]
    first = [i for i in o["items"] if not i["is_gift"]][0]["item_id"]
    left = sum(i["price"] * i["quantity"] for i in o["items"] if not i["is_gift"] and i["item_id"] != first)
    return {"name": "apply_return_refund", "arguments": {
        "order_id": oid, "item_ids": [first], "refund_method": o["payment_method"],
        "deduct_shipping": bool(o["free_shipping"]) and left < 99, "unreturned_gift_ids": []}}


def test_reference_answers_pass():
    for t in B.tasks:
        a = t["evaluation_criteria"]["actions"][-1]
        if a["name"] != "apply_return_refund":
            continue
        v = G.check_write({"name": a["name"], "arguments": a["arguments"]}, memory_for(a["arguments"]["order_id"]), hist(t))
        assert v == [], (t["id"], v)


def test_refuse_and_transfer_tasks_are_blocked():
    for t in B.tasks:
        acts = t["evaluation_criteria"]["actions"]
        if acts[-1]["name"] == "apply_return_refund":
            continue
        oid = acts[1]["arguments"]["order_id"]
        v = G.check_write(naive_call(oid), memory_for(oid), hist(t))
        rules = {r for r, _ in v}
        assert rules & {"window", "category", "condition", "amount"}, (t["id"], v)
        if acts[-1]["name"] == "transfer_to_human_agents":
            assert "amount" in rules, t["id"]


def test_wrong_shipping_is_blocked():
    t = next(x for x in B.tasks if x["id"] == "E1")
    a = json.loads(json.dumps(t["evaluation_criteria"]["actions"][-1]["arguments"]))
    a["deduct_shipping"] = False
    v = G.check_write({"name": "apply_return_refund", "arguments": a}, memory_for(a["order_id"]), hist(t))
    assert "shipping" in {r for r, _ in v}


def test_other_refund_method_needs_user_request():
    t = next(x for x in B.tasks if x["id"] == "A2")
    a = json.loads(json.dumps(t["evaluation_criteria"]["actions"][-1]["arguments"]))
    m = memory_for(a["order_id"])
    m["user"]["payment_methods"]["alipay_02"] = "支付宝"
    a["refund_method"] = "alipay_02"
    assert "refund_method" in {r for r, _ in G.check_write({"name": "apply_return_refund", "arguments": a}, m, hist(t))}


def test_confirm_and_negation():
    t = next(x for x in B.tasks if x["id"] == "A1")
    a = t["evaluation_criteria"]["actions"][-1]
    m = memory_for(a["arguments"]["order_id"])
    call = {"name": a["name"], "arguments": a["arguments"]}
    assert "confirm" in {r for r, _ in G.check_write(call, m, hist(t, "退款多久到账?"))}
    assert "confirm" in {r for r, _ in G.check_write(call, m, hist(t, "不对,先别提交。"))}
    assert G.check_write(call, m, hist(t, "好的,没问题。")) == []


def test_needs_logistics_and_auth():
    t = next(x for x in B.tasks if x["id"] == "A1")
    a = t["evaluation_criteria"]["actions"][-1]
    m = memory_for(a["arguments"]["order_id"])
    m["logistics"] = {}
    assert "window" in {r for r, _ in G.check_write({"name": a["name"], "arguments": a["arguments"]}, m, hist(t))}
    m2 = G.new_memory()
    assert "auth" in {r for r, _ in G.check_write({"name": a["name"], "arguments": a["arguments"]}, m2, hist(t))}
