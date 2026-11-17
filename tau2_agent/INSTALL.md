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

## 中文法规领域 zh_aftersales(自建)

1. 把 `zh_domain/tau2_domain/` 里的 5 个文件复制到 tau2-bench 的 `src/tau2/domains/zh_aftersales/`;把 `tau2_agent/my_guards_zh.py` 复制到 `src/tau2/agent/`。
2. 生成数据:`python zh_domain/build_zh_domain.py <tau2-bench>/data/tau2/domains/zh_aftersales`(会写 db.json、tasks.json、split_tasks.json、policy.md、policy_rag.md)。
3. 在 `src/tau2/registry.py` 里注册 retail 那几行前面加:
   ```python
   try:
       from tau2.domains.zh_aftersales.environment import get_environment as zh_get_env
       from tau2.domains.zh_aftersales.environment import get_environment_rag as zh_get_env_rag
       from tau2.domains.zh_aftersales.environment import get_tasks as zh_get_tasks
       from tau2.domains.zh_aftersales.environment import get_tasks_split as zh_get_splits

       registry.register_domain(zh_get_env, "zh_aftersales")
       registry.register_tasks(zh_get_tasks, "zh_aftersales", get_task_splits=zh_get_splits)
       registry.register_domain(zh_get_env_rag, "zh_aftersales_rag")
       registry.register_tasks(zh_get_tasks, "zh_aftersales_rag", get_task_splits=zh_get_splits)
   except Exception as e:
       logger.warning(f"zh_aftersales 领域注册失败,已跳过: {e}")
   ```
4. 运行:`tau2 run --domain zh_aftersales --agent my_agent ...`(`zh_aftersales_rag` 是条款不放进政策、要按条查询的版本)。
