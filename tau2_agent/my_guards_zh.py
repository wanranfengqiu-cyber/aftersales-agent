"""
中文法规领域(zh_aftersales)的写前策略校验。接口和 my_guards.py(retail)一样:
new_memory / update_memory / check_write / WRITE_TOOLS / DEFAULT_RULES。只用标准库。
规则来自 policy.md:七日期限(签收次日起算)、第六条不适用商品、第八九条完好、第十八条包邮扣运费、
第十二条赠品、第十四条原路退回、超 2000 元转人工、执行前明确同意。
"""
import json
import re
from datetime import date

TODAY = date(2026, 11, 17)
WRITE_TOOLS = {"apply_return_refund"}
AUTH_TOOLS = {"find_user_by_phone"}
EXCLUDED = {"定制商品", "生鲜", "数字商品", "报刊"}
INTACT = {"完好", "拆封查验"}
ALL_RULES = {"auth", "order", "confirm", "window", "category", "condition", "shipping", "gift", "amount", "refund_method"}
DEFAULT_RULES = set(ALL_RULES)


def new_memory():
    return {"user_id": None, "user": None, "orders": {}, "products": {}, "logistics": {}}


def _load(content):
    try:
        return json.loads(content)
    except Exception:
        return None


def update_memory(memory, call, result):
    memory.setdefault("logistics", {})
    if result.get("error"):
        return memory
    name, content = call["name"], result.get("content") or ""
    data = _load(content)
    if name in AUTH_TOOLS:
        memory["user_id"] = content.strip().strip('"')
    elif name == "get_user_details" and isinstance(data, dict):
        if data.get("user_id") == memory["user_id"]:
            memory["user"] = data
    elif name == "get_logistics" and isinstance(data, dict):
        memory["logistics"][data.get("order_id")] = data
    elif isinstance(data, dict) and "order_id" in data and "status" in data and "items" in data:
        memory["orders"][data["order_id"]] = data
    return memory


def _user_texts(history):
    return [m.get("content") or "" for m in history if m.get("role") == "user"]


AFFIRM = re.compile(r"确认|同意|可以|好的|好啊|没问题|是的|对的|行的|办吧|提交吧|退吧|就这样|麻烦了|\bok\b|\byes\b", re.I)
NEGATE = re.compile(r"不同意|不可以|不行|不要|先别|别提交|等一下|等等|不对|不是", re.I)
PAY_WORDS = {"wechat": r"微信", "alipay": r"支付宝", "card": r"银行卡|储蓄卡|信用卡|尾号"}


def check_write(call, memory, history, rules=DEFAULT_RULES, n_calls_in_msg=1):
    """返回违反的规则列表 [(规则名, 给模型看的原因)]。"""
    if call["name"] not in WRITE_TOOLS:
        return []
    a = call.get("arguments") or {}
    v = []
    uid = memory.get("user_id")
    if "auth" in rules and not uid:
        v.append(("auth", "顾客还没核验身份。先用手机号和姓名调用 find_user_by_phone。"))
    order = memory["orders"].get(a.get("order_id"))
    if "order" in rules:
        if order is None:
            v.append(("order", f"先用 get_order_details 查询订单 {a.get('order_id')}。"))
        else:
            if uid and order.get("user_id") != uid:
                v.append(("order", "这个订单不属于已核验的顾客。"))
            if order.get("status") != "delivered":
                v.append(("order", f"订单状态是 {order.get('status')},只有已签收(delivered)的订单能办七日无理由退货。"))
    if order is None:
        return v
    items = {i["item_id"]: i for i in order.get("items", [])}
    ids = a.get("item_ids") or []
    gifts = a.get("unreturned_gift_ids") or []
    if "window" in rules:
        lg = (memory.get("logistics") or {}).get(order["order_id"])
        if not lg or not lg.get("delivered_at"):
            v.append(("window", "先用 get_logistics 查到签收日期,再判断是否在七日内。"))
        else:
            days = (TODAY - date.fromisoformat(lg["delivered_at"])).days
            if days > 7:
                v.append(("window", f"签收日期 {lg['delivered_at']},到今天已是签收后第 {days} 天,超过七日(第十条:自签收次日起算),不能七日无理由退货。"))
    for iid in ids:
        it = items.get(iid)
        if it is None:
            continue
        if "category" in rules and it.get("category") in EXCLUDED:
            v.append(("category", f"{it['name']} 属于{it['category']},按第六条不适用七日无理由退货。"))
        if "condition" in rules and it.get("condition") not in INTACT:
            v.append(("condition", f"{it['name']} 的状态是“{it['condition']}”,按第八、九条视为不完好,不能七日无理由退货。"))
    if "shipping" in rules:
        left = sum(i["price"] * i["quantity"] for i in order.get("items", []) if not i.get("is_gift") and i["item_id"] not in ids)
        need = bool(order.get("free_shipping")) and left < order.get("free_shipping_threshold", 99)
        if bool(a.get("deduct_shipping")) != need:
            v.append(("shipping", f"运费扣除不对:订单{'包邮' if order.get('free_shipping') else '未包邮'},退货后剩余商品 {left:g} 元,"
                                  f"deduct_shipping 应为 {str(need).lower()}(第十八条)。"))
    if "gift" in rules:
        for g in gifts:
            if g not in items or not items[g].get("is_gift"):
                v.append(("gift", f"{g} 不是这个订单的赠品。"))
    if "amount" in rules:
        goods = sum(items[i]["price"] * items[i]["quantity"] for i in ids if i in items)
        if goods > 2000:
            v.append(("amount", f"退款商品价款 {goods:g} 元,超过 2000 元,不能自己提交,要转人工审核。"))
    if "refund_method" in rules:
        pm = a.get("refund_method")
        methods = (memory.get("user") or {}).get("payment_methods") or {}
        if methods and pm not in methods:
            v.append(("refund_method", "退款去向必须是顾客自己的支付方式。"))
        elif pm != order.get("payment_method"):
            kind = (pm or "").split("_")[0]
            said = " ".join(_user_texts(history))
            if not re.search(PAY_WORDS.get(kind, r"$^"), said):
                v.append(("refund_method", "第十四条:原路退回。只有顾客明确要求退到他自己的另一个支付方式时才能改。"))
    if "confirm" in rules:
        texts = _user_texts(history)
        last = texts[-1] if texts else ""
        if not AFFIRM.search(last) or NEGATE.search(last):
            v.append(("confirm", "提交前要向顾客列出订单号、退货商品、退款金额(含扣减项)和退款去向,并得到顾客明确同意。"))
    return v
