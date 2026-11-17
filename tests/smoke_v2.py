"""
v2 冒烟测试(假模型,不花钱):内部查询工具 find_variants / find_orders、转人工前检查。
在 tau2-bench 目录运行:
  .venv\\Scripts\\python.exe D:\\learn\\aftersales-agent\\tests\\smoke_v2.py
"""
import json
import os
os.environ["MY_AGENT_LOG"] = os.path.join(os.path.dirname(__file__), "smoke_events.jsonl")

import tau2.agent.my_agent as MA
from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage, UserMessage
from tau2.registry import registry

ok_all = True
def check(cond, what):
    global ok_all
    print(("PASS " if cond else "FAIL ") + what)
    ok_all = ok_all and bool(cond)

script, seen = [], []
def fake_generate(model, messages, tools=None, tool_choice=None, call_name=None, **kw):
    seen.append({t.name for t in (tools or [])})
    out = script.pop(0)
    return out if isinstance(out, AssistantMessage) else AssistantMessage(role="assistant", content=out)
MA.generate = fake_generate

def call(cid, name, **args):
    return AssistantMessage(role="assistant", tool_calls=[ToolCall(id=cid, name=name, arguments=args, requestor="assistant")])

check("my_agent_v2" in registry.get_agents(), "my_agent_v2 已注册")
env = registry.get_env_constructor("retail")()
agent = registry.get_agent_factory("my_agent_v2")(tools=env.get_tools(), domain_policy=env.get_policy(), llm="fake", llm_args={}, task=None)

# 准备:已核验 Sofia Li,查过订单和空气净化器商品
hist = [AssistantMessage(role="assistant", content="Hi! How can I help you today?"),
        UserMessage(role="user", content="Hi, I'm Sofia Li, zip 78260.")]
for i, (n, a) in enumerate([("find_user_id_by_name_zip", {"first_name": "Sofia", "last_name": "Li", "zip": "78260"}),
                            ("get_user_details", {"user_id": "sofia_li_9219"}),
                            ("get_order_details", {"order_id": "#W8855135"}),
                            ("get_product_details", {"product_id": "3821016478"})]):
    tc = ToolCall(id=f"h{i}", name=n, arguments=a, requestor="assistant")
    hist += [AssistantMessage(role="assistant", tool_calls=[tc]), env.get_response(tc)]
state = agent.get_init_state(hist)
check(state.memory["user_id"] == "sofia_li_9219" and "3821016478" in state.memory["products"], "带历史开始时,已核实信息从历史里回放出来")

# 场景 1:模型调 find_variants → Agent 内部执行 → 模型再回复顾客
script[:] = [call("v1", "find_variants", product_id="3821016478", required_options={"room size": "large"}),
             "Here are the large air purifiers."]
out, state = agent.generate_next_message(UserMessage(role="user", content="Which large air purifiers do you have?"), state)
check(not out.tool_calls and "large" in out.content, "find_variants 在 Agent 内部执行,最后给顾客发消息")
check("find_variants" in seen[0] and "find_orders" in seen[0], "模型能看到两个查询工具")
res = [m for m in state.messages if isinstance(m, ToolMessage) and m.id == "v1"]
data = json.loads(res[0].content) if res else {}
check(res and data["matches"] and all(v["options"]["room size"] == "large" for v in data["matches"]),
      f"find_variants 只返回 room size = large 的款式(找到 {len(data.get('matches', []))} 个,按价格排序)")

# 场景 2:find_orders 按关键词找订单
script[:] = [call("o1", "find_orders", keyword="air purifier"), "Your order #W8855135 has the air purifier."]
out, state = agent.generate_next_message(UserMessage(role="user", content="Which order has my air purifier?"), state)
res = [m for m in state.messages if isinstance(m, ToolMessage) and m.id == "o1"]
data = json.loads(res[0].content) if res else {}
check(res and any(o["order_id"] == "#W8855135" for o in data["matches"]) and data["orders_not_looked_up_yet"],
      "find_orders 找到 #W8855135,并列出还没查过的订单")

# 场景 3:顾客没要求转人工,模型第一次想转 → 拦一次;模型坚持再调 → 放行
script[:] = [call("t1", "transfer_to_human_agents", summary="user wants a refund"),
             call("t2", "transfer_to_human_agents", summary="cannot be handled")]
out, state = agent.generate_next_message(UserMessage(role="user", content="I want my money back for the skateboard."), state)
check(out.tool_calls and out.tool_calls[0].id == "t2", "第一次转人工被拦,模型确认后第二次放行")

print("\n全部通过 ✅" if ok_all else "\n有失败项 ❌,把输出发给导师")
