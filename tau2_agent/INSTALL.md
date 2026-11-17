# 把自研 Agent 装进 τ³-bench(v1.0.1)

1. 克隆 τ³-bench 并切到 v1.0.1:
   ```bash
   git clone https://github.com/sierra-research/tau2-bench.git
   cd tau2-bench && git switch -c my-tau3 v1.0.1 && uv sync
   ```
2. 复制本目录的两个文件到 `src/tau2/agent/`:`my_agent.py`、`my_guards.py`。
3. 在 `src/tau2/registry.py` 里,注册 `discrete_time_audio_native_agent` 的那几行后面加:
   ```python
   # 自研电商售后 Agent(完整版 + 消融版),见 agent/my_agent.py
   from tau2.agent.my_agent import register_my_agents

   register_my_agents(registry)
   ```
4. 本项目只有一个中转站,提供不了框架默认的 gpt-4.1,所以把 `src/tau2/config.py` 里的
   `DEFAULT_LLM_NL_ASSERTIONS` 和 `DEFAULT_LLM_ENV_INTERFACE` 改成和 Agent 相同的模型(本项目是 `openai/deepseek-flash-official`)。
   官方基线和自研 Agent 用的是同一份配置。
5. 在 τ³-bench 根目录的 `.env` 里配置 OpenAI 兼容接口的 `OPENAI_API_KEY`、`OPENAI_API_BASE`(不要提交)。
6. 运行:
   ```bash
   tau2 run --domain retail --agent my_agent --agent-llm openai/deepseek-flash-official \
            --user-llm openai/deepseek-flash-official --num-trials 4 --save-to my_agent_retail_t4
   ```

注册的 Agent:`my_agent`(主版本)、`my_agent_verify`(+执行前 LLM 自查,未采用)、`my_agent_bare`(全关,检查接入)、
`my_agent_no_*`(消融)、`my_agent_def1/2/3`(注入防御逐层)。
