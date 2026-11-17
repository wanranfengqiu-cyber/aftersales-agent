"""
自研 Agent 冒烟测试:不调真实模型(用假模型按剧本回复),检查接入和"生成 → 校验 → 重试"流程。
不花钱,几秒跑完。在 tau2-bench 目录运行:
  .venv\\Scripts\\python.exe D:\\learn\\aftersales-agent\\tests\\smoke_my_agent.py
"""
import os
os.environ["MY_AGENT_LOG"] = os.path.join(os.path.dirname(__file__), "smoke_events.jsonl")

import tau2.agent.my_agent as MA
from tau2.agent import my_guards as G
from tau2.data_model.message import AssistantMessage, ToolCall, UserMessage
from tau2.registry import registry

ok_all = True
def check(cond, what):
    global ok_all
    print(("PASS " if cond else "FAIL ") + what)
    ok_all = ok_all and bool(cond)

# ---- 假模型:按顺序吐出剧本里的回复,并记下每次拿到了哪些工具 ----
script, seen_tools = [], []
def fake_generate(model, messages, tools=None, tool_choice=None, call_name=None, **kw):
    seen_tools.append({t.name for t in (tools or [])})
    out = script.pop(0)
    return out if isinstance(out, AssistantMessage) else AssistantMessage(role="assistant", content=out)
MA.generate = fake_generate

def call(cid, name, **args):
    return AssistantMessage(role="assistant", tool_calls=[ToolCall(id=cid, name=name, arguments=args, requestor="assistant")])

env = registry.get_env_constructor("retail")()
check("my_agent" in registry.get_agents() and "my_agent_no_guard" in registry.get_agents(), "my_agent 和消融版已注册")
agent = registry.get_agent_factory("my_agent")(tools=env.get_tools(), domain_policy=env.get_policy(),
                                              llm="fake", llm_args={}, task=None)
state = agent.get_init_state()

# 场景 1:没核验身份就想取消订单 → 被拦 → 模型改成先问身份
script[:] = [call("c1", "cancel_pending_order", order_id="#W9348897", reason="ordered by mistake"),
             "Sure. First, may I have your email, or your name and zip code?"]
out, state = agent.generate_next_message(UserMessage(role="user", content="Cancel #W9348897, ordered by mistake. Yes."), state)
check(not out.tool_calls and "zip" in out.content, "未核验身份的写操作被拦下,模型改为先核验身份")
check(not (seen_tools[0] & G.WRITE_TOOLS), "核验身份前,模型看不到写操作工具(按阶段开放)")

# 场景 2:正常核验 → 查订单 → 顾客同意后取消(工具由真实 retail 环境执行)
def run_tool(msg):
    return env.get_response(msg.tool_calls[0])
script[:] = [call("c2", "find_user_id_by_name_zip", first_name="Daiki", last_name="Sanchez", zip="46236")]
out, state = agent.generate_next_message(UserMessage(role="user", content="Daiki Sanchez, 46236."), state)
script[:] = [call("c3", "get_order_details", order_id="#W9348897")]
out, state = agent.generate_next_message(run_tool(out), state)
check(state.memory["user_id"] == "daiki_sanchez_3253", "核验结果记进了已核实信息")
script[:] = ["Order #W9348897 is pending. Cancel it with reason 'ordered by mistake'? Please reply yes."]
out, state = agent.generate_next_message(run_tool(out), state)
check(state.memory["orders"]["#W9348897"]["status"] == "pending", "订单状态记进了已核实信息")
check(seen_tools[-1] & G.WRITE_TOOLS, "核验身份后,写操作工具开放")
script[:] = [call("c4", "cancel_pending_order", order_id="#W9348897", reason="ordered by mistake")]
out, state = agent.generate_next_message(UserMessage(role="user", content="Yes, please cancel it."), state)
check(out.tool_calls and out.tool_calls[0].name == "cancel_pending_order", "顾客明确同意后,写操作放行")

# 场景 3:顾客没同意就想执行 → 重试 2 次都不通过 → 兜底改成请顾客确认,不执行
script[:] = [call("c5", "cancel_pending_order", order_id="#W9348897", reason="ordered by mistake")] * 3 + \
            ["Before I cancel, could you confirm with 'yes'?"]
state2 = agent.get_init_state(); state2.memory = state.memory
out, state2 = agent.generate_next_message(UserMessage(role="user", content="What is the refund amount?"), state2)
check(not out.tool_calls, "重试用完仍不合规:不执行写操作,改为给顾客发消息")

print("\n全部通过 ✅" if ok_all else "\n有失败项 ❌,把输出发给导师")
