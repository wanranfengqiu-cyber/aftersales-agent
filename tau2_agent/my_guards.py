"""
写前策略校验(只用 Python 标准库,方便单测和离线回放)。

两件事:
1. update_memory():从工具调用和工具结果里,记下"已核实的信息"
   (核验过的用户、查过的订单 / 商品、支付方式)。
2. check_write():Agent 想调用写操作工具时,先过这里的规则;
   返回违反的规则列表,空列表表示通过。

消息统一用 dict 表示:
  user / assistant: {"role", "content", "tool_calls": [{"id","name","arguments"}]}
  tool:             {"role": "tool", "id", "content", "error"}
"""
import json
import re

# retail 里会改数据库的 7 个工具
WRITE_TOOLS = {
    "cancel_pending_order",
    "modify_pending_order_address",
    "modify_pending_order_items",
    "modify_pending_order_payment",
    "modify_user_address",
    "return_delivered_order_items",
    "exchange_delivered_order_items",
}
AUTH_TOOLS = {"find_user_id_by_email", "find_user_id_by_name_zip"}
PENDING_ONLY = {"cancel_pending_order", "modify_pending_order_address",
                "modify_pending_order_items", "modify_pending_order_payment"}
DELIVERED_ONLY = {"return_delivered_order_items", "exchange_delivered_order_items"}
NEED_PAYMENT = {"modify_pending_order_items", "modify_pending_order_payment",
                "return_delivered_order_items", "exchange_delivered_order_items"}

# 所有规则的名字,做消融和统计时用。
# one_write(写操作一次只调一个)默认不开:离线回放发现基线成功的对话里有 211 次同时发多个写操作,评测并不扣分,开了只会多绕一轮。
ALL_RULES = {"auth", "order", "confirm", "payment", "items", "reason", "one_write"}
DEFAULT_RULES = ALL_RULES - {"one_write"}


def new_memory():
    """空的已核实信息。"""
    return {"user_id": None, "user": None, "orders": {}, "products": {}}


def _load(content):
    try:
        return json.loads(content)
    except Exception:
        return None


def update_memory(memory, call, result):
    """call = {"name","arguments"};result = 工具消息 dict。出错的结果不记。"""
    if result.get("error"):
        return memory
    name, args, content = call["name"], call.get("arguments") or {}, result.get("content") or ""
    data = _load(content)
    if name in AUTH_TOOLS:
        memory["user_id"] = content.strip().strip('"')
    elif name in ("get_user_details", "modify_user_address") and isinstance(data, dict):
        # 只记已核验那个用户的资料(一次对话只服务一个用户)
        if data.get("user_id") == memory["user_id"]:
            memory["user"] = data
    elif isinstance(data, dict) and "order_id" in data and "status" in data:
        # 查订单、以及写操作返回的新订单,都更新订单状态
        memory["orders"][data["order_id"]] = data
    elif name == "get_product_details" and isinstance(data, dict):
        memory["products"][data.get("product_id")] = data
    return memory


def _user_texts(history):
    return [m.get("content") or "" for m in history if m.get("role") == "user"]


def _last_user_text(history):
    texts = _user_texts(history)
    return texts[-1] if texts else ""


def _last_agent_text(history):
    """Agent 最近一条发给用户的文字(通常就是列出操作细节、请用户确认的那条)。"""
    for m in reversed(history):
        if m.get("role") == "assistant" and (m.get("content") or "").strip():
            return m["content"]
    return ""


AFFIRM = re.compile(
    r"\b(yes|yeah|yep|yup|sure|ok|okay|confirm(ed)?|correct|proceed|go ahead|do it|sounds good|"
    r"please (cancel|return|exchange|modify|change|update|process|swap|switch|go)|fine)\b", re.I)
NEGATE = re.compile(r"^\s*(no\s*[,.!]|no\s*$|nope|not yet|wait|hold on|don'?t|do not)", re.I)

REASON_WORDS = {
    "no longer needed": re.compile(r"no longer need|don'?t need|do not need|not need|no need|no longer want|don'?t want", re.I),
    "ordered by mistake": re.compile(r"mistake|accident|wrong(ly)?\b", re.I),
}


def _payment_mentioned(pm_id, memory, history, order):
    """用户说过要用这个支付方式,或者 Agent 列出的确认细节里写了它、用户回答了同意。"""
    text = (" ".join(_user_texts(history)) + " " + _last_agent_text(history)).lower()
    if pm_id.lower() in text:
        return True
    if pm_id.startswith("gift_card"):
        return bool(re.search(r"gift\s*card", text))
    if pm_id.startswith("paypal"):
        return bool(re.search(r"pay\s*pal", text))
    if pm_id.startswith("credit_card"):
        pm = ((memory.get("user") or {}).get("payment_methods") or {}).get(pm_id) or {}
        keys = [k for k in (pm.get("brand"), pm.get("last_four")) if k]
        if any(str(k).lower() in text for k in keys):
            return True
        if re.search(r"credit|visa|master\s*card|amex|discover|(?<!gift )\bcard\b", text):
            return True
    # "原支付方式 / 同一张卡" 也算明确,前提是选的确实是订单原来的支付方式
    if order and re.search(r"original|same (card|payment|method|account)", text):
        hist = order.get("payment_history") or []
        if hist and hist[0].get("payment_method_id") == pm_id:
            return True
    return False


def _item_product(memory, item_id):
    for pid, p in memory["products"].items():
        if item_id in (p.get("variants") or {}):
            return pid, p["variants"][item_id]
    return None, None


def check_write(call, memory, history, rules=DEFAULT_RULES, n_calls_in_msg=1):
    """返回违反的规则列表 [(规则名, 说明给模型看的英文原因)]。"""
    name, args = call["name"], call.get("arguments") or {}
    if name not in WRITE_TOOLS:
        return []
    v = []
    uid = memory.get("user_id")

    if "one_write" in rules and n_calls_in_msg > 1:
        v.append(("one_write", "Make write tool calls one at a time, never together with other tool calls."))

    if "auth" in rules:
        if not uid:
            v.append(("auth", "The user is not authenticated yet. Authenticate via email or name + zip code first."))
        elif name == "modify_user_address" and args.get("user_id") != uid:
            v.append(("auth", f"You can only act on the authenticated user {uid}."))

    order = memory["orders"].get(args.get("order_id")) if "order_id" in args else None
    if "order" in rules and "order_id" in args:
        if order is None:
            v.append(("order", f"Call get_order_details for {args.get('order_id')} and check its status before acting on it."))
        else:
            if uid and order.get("user_id") != uid:
                v.append(("order", "This order does not belong to the authenticated user."))
            st = order.get("status", "")
            if name in PENDING_ONLY and st != "pending":
                v.append(("order", f"Order status is '{st}'; this action needs a 'pending' order."))
            if name in DELIVERED_ONLY and st != "delivered":
                v.append(("order", f"Order status is '{st}'; this action needs a 'delivered' order."))

    if "confirm" in rules:
        last = _last_user_text(history)
        approved = AFFIRM.search(last) and not NEGATE.search(last)
        # 取消订单时,用户在 Agent 列出细节后直接回答原因,也算同意(基线这样做都判对)
        if name == "cancel_pending_order" and any(r.search(last) for r in REASON_WORDS.values()):
            approved = True
        if not approved:
            v.append(("confirm", "List the exact action details to the user and get an explicit 'yes' before this write action."))

    if "payment" in rules and name in NEED_PAYMENT:
        pm = args.get("payment_method_id")
        methods = ((memory.get("user") or {}).get("payment_methods") or {})
        if not pm:
            v.append(("payment", "A payment method is required."))
        elif methods and pm not in methods:
            v.append(("payment", f"{pm} is not one of the user's payment methods."))
        elif not _payment_mentioned(pm, memory, history, order):
            v.append(("payment", "The user has not explicitly chosen this payment method. Ask which payment method to use; do not choose for them."))

    if "items" in rules and name in ("exchange_delivered_order_items", "modify_pending_order_items"):
        old, new = args.get("item_ids") or [], args.get("new_item_ids") or []
        if len(old) != len(new):
            v.append(("items", "item_ids and new_item_ids must have the same length."))
        for o, n in zip(old, new):
            if o == n:
                v.append(("items", f"New item {n} is the same as the old item; pick a different option."))
                continue
            po, _ = _item_product(memory, o)
            pn, var = _item_product(memory, n)
            if pn is None:
                continue  # 没查过这个商品就不判断(可能用别的方式查到了 id)
            elif po is not None and po != pn:
                v.append(("items", f"New item {n} is a different product type than {o}."))
            elif var is not None and not var.get("available", False):
                v.append(("items", f"New item {n} is not available."))

    if "reason" in rules and name == "cancel_pending_order":
        reason = args.get("reason")
        text = " ".join(_user_texts(history)) + " " + _last_agent_text(history)
        if reason not in REASON_WORDS:
            v.append(("reason", "Reason must be 'no longer needed' or 'ordered by mistake'."))
        else:
            other = [r for r in REASON_WORDS if r != reason][0]
            said_this, said_other = REASON_WORDS[reason].search(text), REASON_WORDS[other].search(text)
            if not said_this:
                v.append(("reason", f"The user never stated the reason '{reason}'. Ask the user for the reason; do not infer it."))
            elif said_other and not said_this:
                v.append(("reason", f"The user's words point to '{other}', not '{reason}'."))
    return v
