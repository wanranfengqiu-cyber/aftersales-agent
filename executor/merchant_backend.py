"""
模拟商家后台(只在本机 127.0.0.1 运行),给 RPA 退款执行器练习和做故障注入。

页面:登录 → 退款表单 → 结果页;另有"按幂等键查退款记录"页,执行器提交前查重、提交后回读都用它。
故障开关(每次运行前由实验脚本设置,一次只开一种):
  none            无故障
  delay           页面和提交随机延迟 1.5–4 秒
  popup           退款页弹出遮挡全屏的推广弹窗,不关掉就点不到按钮
  element_change  界面改版:提交按钮换了 id、文字和位置;旧 id 被"保存草稿"按钮占用
  login_expired   打开退款页时登录过期,被踢回登录页
  submit_timeout  提交后其实已经退款成功,但页面 15 秒不返回(执行器会以为超时失败)
注意:后台本身不防重复退款(同一个幂等键可以退多次),防重复要靠执行器自己查重。

启动(实验脚本会自动启动,不用手动):
  python -m uvicorn merchant_backend:app --port 8765
"""
import asyncio
import json
import os
import random
import sqlite3
import time
import uuid
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "merchant.db")
USERNAME, PASSWORD = "admin", "admin123"  # 本地模拟后台的测试账号
FAULTS = ["none", "delay", "popup", "element_change", "login_expired", "submit_timeout"]

app = FastAPI()
STATE = {"fault": "none", "sessions": set(), "expired_once": False, "rng": random.Random(0)}

CSS = """<style>body{font-family:Arial;margin:30px} input{margin:6px} button{margin:6px;padding:6px 14px}
#promo{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.6);z-index:99}
#promo div{background:#fff;width:320px;margin:120px auto;padding:20px}</style>"""


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def reset_db():
    conn = db()
    conn.executescript("""
        DROP TABLE IF EXISTS orders; DROP TABLE IF EXISTS refunds;
        CREATE TABLE orders(order_id TEXT PRIMARY KEY, customer TEXT, total REAL, status TEXT);
        CREATE TABLE refunds(refund_id TEXT PRIMARY KEY, order_id TEXT, amount REAL, idem_key TEXT, created_at REAL);
    """)
    for i in range(1, 21):
        conn.execute("INSERT INTO orders VALUES (?,?,?,?)", (f"W{1000 + i}", f"customer_{i}", round(50 + i * 13.7, 2), "delivered"))
    conn.commit()
    conn.close()


reset_db()


def page(body: str) -> HTMLResponse:
    return HTMLResponse(f"<html><head><meta charset='utf-8'>{CSS}</head><body>{body}</body></html>")


def logged_in(request: Request) -> bool:
    return request.cookies.get("sid") in STATE["sessions"]


async def maybe_delay():
    if STATE["fault"] == "delay":
        await asyncio.sleep(STATE["rng"].uniform(1.5, 4.0))


async def form_of(request: Request) -> dict:
    try:
        body = (await request.body()).decode("utf-8")
    except Exception:  # 浏览器已经离开页面(比如执行器超时后跳走了),忽略
        return {}
    return {k: v[0] for k, v in parse_qs(body).items()}


# ---------------- 登录 ----------------
@app.get("/login")
async def login_page(next: str = "/orders"):
    return page(f"""<h2>商家后台登录</h2>
      <form method="post" action="/login?next={quote(next)}">
        <input id="username" name="username" placeholder="用户名">
        <input id="password" name="password" type="password" placeholder="密码">
        <button id="login-btn" type="submit">登录</button></form>""")


@app.post("/login")
async def login(request: Request, next: str = "/orders"):
    f = await form_of(request)
    if f.get("username") == USERNAME and f.get("password") == PASSWORD:
        sid = uuid.uuid4().hex
        STATE["sessions"].add(sid)
        r = RedirectResponse(next, status_code=303)
        r.set_cookie("sid", sid)
        return r
    return page("<p id='error'>用户名或密码错误</p>")


# ---------------- 业务页面 ----------------
@app.get("/orders")
async def orders(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    rows = "".join(f"<tr><td>{o['order_id']}</td><td>{o['customer']}</td><td>{o['total']}</td><td>{o['status']}</td></tr>"
                   for o in db().execute("SELECT * FROM orders"))
    return page(f"<h2>订单列表</h2><table id='orders'>{rows}</table>")


@app.get("/refund")
async def refund_form(request: Request, order_id: str):
    if STATE["fault"] == "login_expired" and not STATE["expired_once"]:
        STATE["expired_once"] = True
        STATE["sessions"].clear()  # 登录过期
    if not logged_in(request):
        return RedirectResponse(f"/login?next={quote('/refund?order_id=' + order_id)}", status_code=303)
    await maybe_delay()
    fault = STATE["fault"]
    fields = f"""<input id="order_id" name="order_id" value="{order_id}" readonly>
        <input id="amount" name="amount" placeholder="退款金额">
        <input id="idem_key" name="idem_key" placeholder="幂等键">"""
    if fault == "element_change":
        # 改版:真正的提交按钮换了 id、文字和位置;旧 id 现在属于"保存草稿"
        new_id = "confirm-" + uuid.uuid4().hex[:6]
        buttons = f"""<button id="{new_id}" type="submit" formaction="/refund">Confirm refund</button>
            <p>请核对金额后提交</p>
            <button id="submit-refund" type="submit" formaction="/draft">Save draft</button>"""
        form = f'<form method="post" action="/refund">{buttons}{fields}</form>'
    else:
        form = f"""<form method="post" action="/refund">{fields}
            <button id="submit-refund" type="submit">Submit refund</button></form>"""
    popup = ""
    if fault == "popup":
        # 页面一打开就盖着全屏弹窗,不关掉就点不到下面的按钮
        popup = """<div id="promo"><div><h3>限时活动!开通会员享 8 折</h3>
          <button id="promo-join" type="button">立即开通</button>
          <button id="promo-close" type="button" onclick="document.getElementById('promo').remove()">No thanks</button></div></div>"""
    return page(f"<h2>退款申请</h2>{form}{popup}")


@app.post("/refund")
async def submit_refund(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    await maybe_delay()
    f = await form_of(request)
    try:
        amount = float(f.get("amount", ""))
    except ValueError:
        return page("<p id='error'>金额格式错误</p>")
    refund_id = "R-" + uuid.uuid4().hex[:8]
    conn = db()
    conn.execute("INSERT INTO refunds VALUES (?,?,?,?,?)", (refund_id, f.get("order_id"), amount, f.get("idem_key", ""), time.time()))
    conn.commit()
    conn.close()
    if STATE["fault"] == "submit_timeout":
        await asyncio.sleep(15)  # 已经退款成功,但迟迟不返回页面
    return page(f"<p id='result'>Refund submitted successfully.</p><p>Refund ID: <span id='refund-id'>{refund_id}</span></p>")


@app.post("/draft")
async def save_draft(request: Request):
    return page("<p id='result'>Draft saved successfully.</p>")


@app.get("/refunds")
async def list_refunds(request: Request, idem_key: str = ""):
    """按幂等键查退款记录:执行器提交前查重、提交后回读都用这个页面。"""
    if not logged_in(request):
        return RedirectResponse(f"/login?next={quote('/refunds?idem_key=' + idem_key)}", status_code=303)
    await maybe_delay()
    rows = "".join(
        f"<tr class='refund' data-refund-id='{r['refund_id']}' data-order-id='{r['order_id']}' data-amount='{r['amount']}'>"
        f"<td>{r['refund_id']}</td><td>{r['order_id']}</td><td>{r['amount']}</td></tr>"
        for r in db().execute("SELECT * FROM refunds WHERE idem_key=?", (idem_key,)))
    return page(f"<h2>退款记录</h2><table id='refunds'>{rows}</table><p id='loaded'>ok</p>")


# ---------------- 实验用的管理接口(不经过页面) ----------------
@app.get("/admin/health")
async def health():
    return {"ok": True, "fault": STATE["fault"]}


@app.post("/admin/config")
async def config(request: Request):
    cfg = json.loads((await request.body()) or b"{}")
    fault = cfg.get("fault", "none")
    assert fault in FAULTS, fault
    STATE.update(fault=fault, expired_once=False, rng=random.Random(cfg.get("seed", 0)))
    return {"ok": True, "fault": fault}


@app.post("/admin/reset")
async def reset():
    reset_db()
    STATE["sessions"].clear()
    return {"ok": True}


@app.get("/admin/refunds")
async def admin_refunds(idem_key: str):
    rows = [dict(r) for r in db().execute("SELECT * FROM refunds WHERE idem_key=?", (idem_key,))]
    return JSONResponse(rows)
