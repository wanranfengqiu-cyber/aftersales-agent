"""
写前校验 my_guards 的单测。只依赖标准库 + pytest。
运行(在 tau2-bench 目录):
  .venv\\Scripts\\python.exe -m pytest D:\\learn\\aftersales-agent\\tests -q
"""
import json
import os
import sys

HERE = os.path.dirname(__file__)
for p in [os.path.join(HERE, "..", "tau2_agent"),                                  # 本仓库里的副本
          os.path.join(HERE, "..", "..", "tau2-bench", "src", "tau2", "agent"),   # D:\learn 下的相对位置
          os.environ.get("MY_GUARDS_DIR", "")]:
    if p and os.path.exists(os.path.join(p, "my_guards.py")):
        sys.path.insert(0, p)
import my_guards as G  # noqa: E402

ORDER_P = {"order_id": "#W1", "user_id": "u1", "status": "pending",
           "items": [{"name": "Lamp", "item_id": "i_old", "product_id": "p1"}],
           "payment_history": [{"payment_method_id": "credit_card_1"}]}
ORDER_D = dict(ORDER_P, order_id="#W2", status="delivered")
USER = {"user_id": "u1", "payment_methods": {
    "credit_card_1": {"source": "credit_card", "brand": "visa", "last_four": "1234", "id": "credit_card_1"},
    "paypal_1": {"source": "paypal", "id": "paypal_1"},
    "gift_card_1": {"source": "gift_card", "balance": 50, "id": "gift_card_1"}}}
PRODUCT = {"product_id": "p1", "variants": {
    "i_old": {"item_id": "i_old", "available": True},
    "i_new": {"item_id": "i_new", "available": True},
    "i_gone": {"item_id": "i_gone", "available": False}}}


def mem(auth=True):
    m = G.new_memory()
    if auth:
        G.update_memory(m, {"name": "find_user_id_by_email"}, {"content": "u1"})
        G.update_memory(m, {"name": "get_user_details"}, {"content": json.dumps(USER)})
        G.update_memory(m, {"name": "get_order_details"}, {"content": json.dumps(ORDER_P)})
        G.update_memory(m, {"name": "get_order_details"}, {"content": json.dumps(ORDER_D)})
        G.update_memory(m, {"name": "get_product_details"}, {"content": json.dumps(PRODUCT)})
    return m


def hist(*user_lines, agent=""):
    h = []
    for u in user_lines:
        if agent:
            h.append({"role": "assistant", "content": agent})
        h.append({"role": "user", "content": u})
    return h


def rules_of(v):
    return {r for r, _ in v}


def test_read_tools_are_never_blocked():
    assert G.check_write({"name": "get_order_details", "arguments": {"order_id": "#W9"}}, G.new_memory(), []) == []


def test_auth_required():
    v = G.check_write({"name": "cancel_pending_order", "arguments": {"order_id": "#W1", "reason": "no longer needed"}},
                      mem(auth=False), hist("I no longer need it. Yes, cancel it."))
    assert "auth" in rules_of(v)


def test_error_result_not_remembered():
    m = G.new_memory()
    G.update_memory(m, {"name": "find_user_id_by_email"}, {"content": "Error: User not found", "error": True})
    assert m["user_id"] is None


def test_cancel_ok():
    v = G.check_write({"name": "cancel_pending_order", "arguments": {"order_id": "#W1", "reason": "no longer needed"}},
                      mem(), hist("I no longer need it.", "Yes, please cancel it."))
    assert v == []


def test_cancel_needs_pending_and_lookup():
    v = G.check_write({"name": "cancel_pending_order", "arguments": {"order_id": "#W2", "reason": "no longer needed"}},
                      mem(), hist("I no longer need it. yes"))
    assert "order" in rules_of(v)
    v = G.check_write({"name": "cancel_pending_order", "arguments": {"order_id": "#W404", "reason": "no longer needed"}},
                      mem(), hist("I no longer need it. yes"))
    assert "order" in rules_of(v)


def test_other_users_order_blocked():
    m = mem()
    G.update_memory(m, {"name": "get_order_details"}, {"content": json.dumps(dict(ORDER_P, order_id="#W3", user_id="u2"))})
    v = G.check_write({"name": "cancel_pending_order", "arguments": {"order_id": "#W3", "reason": "ordered by mistake"}},
                      m, hist("ordered by mistake, yes"))
    assert "order" in rules_of(v)


def test_confirm_required():
    v = G.check_write({"name": "cancel_pending_order", "arguments": {"order_id": "#W1", "reason": "no longer needed"}},
                      mem(), hist("I no longer need it.", "Hmm, what would the refund be?"))
    assert "confirm" in rules_of(v)
    v = G.check_write({"name": "cancel_pending_order", "arguments": {"order_id": "#W1", "reason": "no longer needed"}},
                      mem(), hist("I no longer need it.", "No, wait."))
    assert "confirm" in rules_of(v)


def test_reason_must_come_from_user_or_listing():
    v = G.check_write({"name": "cancel_pending_order", "arguments": {"order_id": "#W1", "reason": "ordered by mistake"}},
                      mem(), hist("Yes, cancel it."))
    assert "reason" in rules_of(v)
    v = G.check_write({"name": "cancel_pending_order", "arguments": {"order_id": "#W1", "reason": "ordered by mistake"}},
                      mem(), hist("Yes, cancel it.", agent="Cancel #W1, reason: ordered by mistake. Confirm?"))
    assert v == []


def test_payment_must_be_chosen():
    call = {"name": "exchange_delivered_order_items",
            "arguments": {"order_id": "#W2", "item_ids": ["i_old"], "new_item_ids": ["i_new"], "payment_method_id": "paypal_1"}}
    assert "payment" in rules_of(G.check_write(call, mem(), hist("Yes, go ahead.")))
    assert G.check_write(call, mem(), hist("Use my PayPal.", "Yes, go ahead.")) == []
    bad = json.loads(json.dumps(call)); bad["arguments"]["payment_method_id"] = "paypal_999"
    assert "payment" in rules_of(G.check_write(bad, mem(), hist("Use my PayPal. yes")))


def test_credit_card_by_last_four():
    call = {"name": "return_delivered_order_items",
            "arguments": {"order_id": "#W2", "item_ids": ["i_old"], "payment_method_id": "credit_card_1"}}
    assert G.check_write(call, mem(), hist("Refund to the card ending 1234.", "yes")) == []


def test_exchange_items_rules():
    base = {"order_id": "#W2", "payment_method_id": "paypal_1"}
    same = {"name": "exchange_delivered_order_items", "arguments": dict(base, item_ids=["i_old"], new_item_ids=["i_old"])}
    gone = {"name": "exchange_delivered_order_items", "arguments": dict(base, item_ids=["i_old"], new_item_ids=["i_gone"])}
    h = hist("PayPal please.", "Yes.")
    assert "items" in rules_of(G.check_write(same, mem(), h))
    assert "items" in rules_of(G.check_write(gone, mem(), h))


def test_write_result_updates_order_status():
    m = mem()
    G.update_memory(m, {"name": "cancel_pending_order"}, {"content": json.dumps(dict(ORDER_P, status="cancelled"))})
    assert m["orders"]["#W1"]["status"] == "cancelled"
