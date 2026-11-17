"""
中文领域冒烟测试(不调真实模型,几秒跑完):
1) 领域和 50 个任务能被 τ³-bench 识别;2) 每个任务的参考动作在真实环境里都能执行、不报错;
3) 自研 Agent 在中文领域会用中文规则拦截(用假模型)。
在 tau2-bench 目录运行:
  .venv\\Scripts\\python.exe D:\\learn\\aftersales-agent\\tests\\smoke_zh.py
"""
import os
os.environ["MY_AGENT_LOG"] = os.path.join(os.path.dirname(__file__), "smoke_events.jsonl")

import tau2.agent.my_agent as MA
from tau2.data_model.message import AssistantMessage, ToolCall, UserMessage
from tau2.registry import registry

ok_all = True
def check(cond, what):
    global ok_all
    print(("PASS " if cond else "FAIL ") + what)
    ok_all = ok_all and bool(cond)

check({"zh_aftersales", "zh_aftersales_rag"} <= set(registry.get_domains()), "两个中文领域已注册")
tasks = registry.get_tasks_loader("zh_aftersales")()
check(len(tasks) == 50, f"读到 {len(tasks)} 个任务(应为 50)")
env = registry.get_env_constructor("zh_aftersales")()
check("七日" in env.get_policy() and "第十条" in env.get_policy(), "政策是中文、含条款")
rag = registry.get_env_constructor("zh_aftersales_rag")().get_policy()
check("get_policy_article" in rag and "第十条 选择无理由退货" not in rag, "按条检索版政策:不含条款全文,要用工具查")
names = {t.name for t in env.get_tools()}
check({"find_user_by_phone", "get_order_details", "get_logistics", "get_policy_article", "apply_return_refund",
       "transfer_to_human_agents", "get_user_details"} <= names, f"7 个工具都在:{sorted(names)}")

# 每个任务的参考动作在新环境里执行一遍,都不应该报错
bad = []
for t in tasks:
    e = registry.get_env_constructor("zh_aftersales")()
    for a in t.evaluation_criteria.actions:
        r = e.get_response(ToolCall(id="x", name=a.name, arguments=a.arguments, requestor="assistant"))
        if r.error:
            bad.append((t.id, a.name, r.content[:80]))
check(not bad, f"50 个任务的参考动作都能执行{'' if not bad else ':' + str(bad[:3])}")

# 自研 Agent:超过七日的订单,模型想直接提交 → 被中文规则拦下 → 重试 2 次仍不行 → 改为回复顾客
script = []
def fake_generate(model, messages, tools=None, tool_choice=None, call_name=None, **kw):
    out = script.pop(0)
    return out if isinstance(out, AssistantMessage) else AssistantMessage(role="assistant", content=out)
MA.generate = fake_generate
t = next(x for x in tasks if x.id == "B5")
oid = t.evaluation_criteria.actions[1].arguments["order_id"]
agent = registry.get_agent_factory("my_agent")(tools=env.get_tools(), domain_policy=env.get_policy(), llm="fake", llm_args={}, task=t)
check(agent.zh and "七日" in agent.system_prompt and "标准流程" in agent.system_prompt, "自研 Agent 识别出中文领域,用中文 SOP")
hist = [AssistantMessage(role="assistant", content="您好,请问有什么可以帮您?"), UserMessage(role="user", content="我想退台灯。")]
for k, a in enumerate(t.evaluation_criteria.actions[:3]):
    tc = ToolCall(id=f"h{k}", name=a.name, arguments=a.arguments, requestor="assistant")
    hist += [AssistantMessage(role="assistant", tool_calls=[tc]), env.get_response(tc)]
state = agent.get_init_state(hist)
check(state.memory["logistics"].get(oid, {}).get("delivered_at") == "2026-11-09", "签收日期记进了已核实信息")
call = AssistantMessage(role="assistant", tool_calls=[ToolCall(id="w", name="apply_return_refund", requestor="assistant", arguments={
    "order_id": oid, "item_ids": [f"{oid}-1"], "refund_method": f"wechat_{int(oid[2:]) - 1000:02d}", "deduct_shipping": False, "unreturned_gift_ids": []})])
script[:] = [call, call, call, "抱歉,您的订单签收已超过七天,按第十条不能办理七日无理由退货。"]
out, state = agent.generate_next_message(UserMessage(role="user", content="确认,提交吧。"), state)
check(not out.tool_calls and "七天" in out.content, "超过七日的提交被拦下,改为向顾客说明")

print("\n全部通过 ✅" if ok_all else "\n有失败项 ❌,把输出发给导师")
