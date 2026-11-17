"""
RPA 退款执行器(Playwright)。Agent 只做决定(退哪个订单、退多少),真正去后台点按钮的是它。

四个版本,一个比一个多一层保护(做对比实验用):
  v1_fixed     固定脚本:按写死的选择器填表、点提交,看到 "successfully" 就当成功
  v2_retry     + 重试:超时、被踢回登录页时重新登录、整体重试(最多 3 次)
  v3_verify    + 幂等查重 + 回读核验:提交前按幂等键查有没有退过;提交后回读退款记录,
                 只有"恰好一条、订单和金额都对"才算成功;最后还失败就截图、转人工
  v4_selfheal  + LLM 自愈定位:按原选择器找不到 / 点不动,或者回读发现没退成时,
                 把页面上可点的元素列给模型,让它选下一步点哪个;点完照样回读核验
"""
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field

from playwright.sync_api import TimeoutError as PWTimeout

VERSIONS = ["v1_fixed", "v2_retry", "v3_verify", "v4_selfheal"]
USERNAME, PASSWORD = "admin", "admin123"  # 本地模拟后台的测试账号


@dataclass
class RefundRequest:
    order_id: str
    amount: float
    idem_key: str = field(default_factory=lambda: "rk-" + uuid.uuid4().hex[:10])


@dataclass
class Result:
    status: str = "failed"          # success / failed / transfer_to_human
    refund_id: str | None = None
    attempts: int = 0
    heal_clicks: list = field(default_factory=list)
    llm_calls: int = 0
    notes: list = field(default_factory=list)
    screenshot: str | None = None


# ---------------- LLM 自愈定位 ----------------
_client = None


def _llm():
    global _client
    if _client is None:
        from dotenv import load_dotenv
        from openai import OpenAI
        here = os.path.dirname(os.path.abspath(__file__))
        for p in [os.path.join(here, "..", ".env"), os.path.join(here, "..", "..", "新建文件夹", ".env")]:
            if os.path.exists(p):
                load_dotenv(p)
                break
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("OPENAI_BASE_URL"))
    return _client


COLLECT_JS = """() => {
  const els = [...document.querySelectorAll('button, a, input[type=submit], [role=button]')];
  const out = [];
  els.forEach((el, i) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    el.setAttribute('data-heal-idx', String(i));
    const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    const covered = !!top && top !== el && !el.contains(top);
    out.push({idx: i, tag: el.tagName.toLowerCase(), id: el.id || '', text: (el.innerText || el.value || '').trim().slice(0, 40),
              x: Math.round(r.x), y: Math.round(r.y), covered: covered});
  });
  return out;
}"""


def heal_click(page, goal: str, res: Result, timeout: int) -> bool:
    """把可点击元素列给模型,让它选一个点。返回是否点成功。"""
    try:
        page.wait_for_load_state(timeout=timeout)
        elements = page.evaluate(COLLECT_JS)
    except Exception as e:
        res.notes.append(f"heal: cannot read page ({type(e).__name__})")
        return False
    if not elements:
        return False
    listing = "\n".join(f"{e['idx']} | <{e['tag']}> id={e['id']} | text={e['text']} | pos=({e['x']},{e['y']})"
                        f"{' | COVERED by another element' if e['covered'] else ''}" for e in elements)
    prompt = (f"You are operating a web page. Goal: {goal}\n"
              f"Clickable elements (index | tag | id | text | position):\n{listing}\n\n"
              "If a popup or overlay covers the page, close it first (choose a close / no-thanks button, never a purchase button). "
              "Do NOT choose buttons that only save a draft. Reply with the index number only.")
    if os.getenv("RPA_HEAL_STUB") == "1":
        # 离线调试用:不调模型,按关键词选(正式实验不要开)
        pick = next((e for e in elements if re.search(r"no thanks|close|关闭", e["text"], re.I)), None) \
            or next((e for e in elements if re.search(r"confirm|submit", e["text"], re.I) and not e["covered"]), None)
        reply = str(pick["idx"]) if pick else ""
    else:
        res.llm_calls += 1
        reply = _llm().chat.completions.create(model=os.getenv("MODEL_NAME"), temperature=0,
                                               messages=[{"role": "user", "content": prompt}]).choices[0].message.content
    m = re.search(r"\d+", reply or "")
    if not m:
        res.notes.append(f"heal: unparsable reply {reply!r}")
        return False
    idx = m.group()
    chosen = next((e for e in elements if str(e["idx"]) == idx), None)
    res.heal_clicks.append(chosen or {"idx": idx})
    try:
        page.click(f"[data-heal-idx='{idx}']", timeout=timeout)
        try:
            page.wait_for_load_state(timeout=timeout)
        except Exception:
            pass
        return True
    except PWTimeout:
        res.notes.append(f"heal: click on {idx} timed out")
        return False


# ---------------- 页面操作 ----------------
def login(page, base, t):
    page.goto(f"{base}/login", timeout=t)
    page.fill("#username", USERNAME, timeout=t)
    page.fill("#password", PASSWORD, timeout=t)
    page.click("#login-btn", timeout=t)
    page.wait_for_url("**/orders", timeout=t)


def ensure_page(page, url, base, t, relogin: bool):
    page.goto(url, timeout=t)
    if "/login" in page.url:
        if not relogin:
            raise RuntimeError("redirected to login")
        page.fill("#username", USERNAME, timeout=t)
        page.fill("#password", PASSWORD, timeout=t)
        page.click("#login-btn", timeout=t)
        page.wait_for_load_state(timeout=t)
        if "/login" in page.url:
            raise RuntimeError("re-login failed")


def read_refunds(page, base, req: RefundRequest, t) -> list:
    """回读 / 查重:打开"按幂等键查退款记录"页,读出所有记录。"""
    ensure_page(page, f"{base}/refunds?idem_key={req.idem_key}", base, t, relogin=True)
    page.wait_for_selector("#loaded", timeout=t)
    return page.evaluate("""() => [...document.querySelectorAll('tr.refund')].map(r => ({
        refund_id: r.dataset.refundId, order_id: r.dataset.orderId, amount: parseFloat(r.dataset.amount)}))""")


def verified(rows, req) -> bool:
    return len(rows) == 1 and rows[0]["order_id"] == req.order_id and abs(rows[0]["amount"] - req.amount) < 1e-6


def execute(page, req: RefundRequest, version: str, base: str = "http://127.0.0.1:8765",
            shot_dir: str | None = None) -> Result:
    res = Result()
    v = VERSIONS.index(version)
    t = 3000 if v == 0 else 10000            # 毫秒
    max_attempts = 1 if v == 0 else 3
    use_verify, use_heal = v >= 2, v >= 3
    force_heal = False
    try:
        login(page, base, t)
    except Exception as e:
        res.notes.append(f"login failed: {type(e).__name__}")
        if v == 0:
            return res
    for attempt in range(max_attempts):
        res.attempts = attempt + 1
        try:
            # 1) 幂等查重:已经退过就不再提交,直接回读
            if use_verify:
                rows = read_refunds(page, base, req, t)
                if rows:
                    if verified(rows, req):
                        res.status, res.refund_id = "success", rows[0]["refund_id"]
                        res.notes.append("precheck: already refunded, skip submit")
                        return res
                    res.notes.append(f"precheck: unexpected records {rows}")
                    break  # 已有不对的记录:不再动手,转人工
            # 2) 打开退款页、填表
            ensure_page(page, f"{base}/refund?order_id={req.order_id}", base, t, relogin=v >= 1)
            page.fill("#amount", str(req.amount), timeout=t)
            page.fill("#idem_key", req.idem_key, timeout=t)
            # 3) 点提交
            goal = f"Submit the refund for order {req.order_id} (amount {req.amount}). Do not save a draft."
            if force_heal:
                clicked = False
                for _ in range(3):
                    if heal_click(page, goal, res, t) and page.query_selector("#result"):
                        clicked = True
                        break
                    page.wait_for_timeout(300)
                if not clicked:
                    page.wait_for_selector("#result", timeout=t)
            else:
                try:
                    page.click("#submit-refund", timeout=t)
                except PWTimeout:
                    if not use_heal:
                        raise
                    res.notes.append("submit button not clickable -> self-heal")
                    for _ in range(3):  # 最多自愈 3 步(比如先关弹窗,再点提交)
                        heal_click(page, goal, res, t)
                        if page.query_selector("#result"):
                            break
                        try:
                            page.click("#submit-refund", timeout=2000)
                            break
                        except PWTimeout:
                            continue
            page.wait_for_selector("#result", timeout=t)
            # 4) 判断成功
            if not use_verify:
                text = page.inner_text("#result", timeout=t)
                if "successfully" in text:     # 固定脚本只看提示文字
                    res.status = "success"
                    el = page.query_selector("#refund-id")
                    res.refund_id = el.inner_text() if el else None
                    return res
                raise RuntimeError(f"unexpected result text: {text}")
            rows = read_refunds(page, base, req, t)  # 回读核验
            if verified(rows, req):
                res.status, res.refund_id = "success", rows[0]["refund_id"]
                return res
            if len(rows) > 1:
                res.notes.append(f"readback: {len(rows)} refunds for one request!")
                break
            res.notes.append("readback: no refund record after submit")
            if use_heal:
                force_heal = True  # 点了却没退成(比如点到了"保存草稿"),下一次让模型重新选按钮
        except Exception as e:
            res.notes.append(f"attempt {attempt + 1}: {type(e).__name__}: {str(e)[:80]}")
    # 都失败了
    if use_verify:
        res.status = "transfer_to_human"
        if shot_dir:
            os.makedirs(shot_dir, exist_ok=True)
            res.screenshot = os.path.join(shot_dir, f"{req.idem_key}.png")
            try:
                page.screenshot(path=res.screenshot)
            except Exception:
                res.screenshot = None
    return res
