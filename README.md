# 电商售后 Agent:公开基准评测 · 写前校验 · 可验证执行

一个处理电商退货退款的客服 Agent,用 LangGraph 实现,接入开源客服 Agent 基准 [τ³-bench](https://github.com/sierra-research/tau2-bench)(v1.0.1)和官方基线同模型对比;退款由 Playwright RPA 在模拟商家后台执行,并做故障注入与提示注入测试。

> 所有数字都来自本仓库里的脚本和结果文件。Agent、顾客模拟器、评委都是同一个模型(经中转站调用的 DeepSeek),和官方排行榜的设置不同,**绝对分数不能和榜单比,只比同条件下的差别**。

## 结果一览

### 1. τ³-bench retail:通过率持平,违规写操作 9 → 0

114 题 × 每题 4 次,同一个模型、同一个顾客模拟器和评委。置信区间按题自助抽样 5000 次(`analysis/compare_runs.py`)。

| 题目 | 运行 | pass^1 | pass^4 | pass^1 差值 95% 区间 |
|---|---|---|---|---|
| 全部 114 | 官方基线 LLMAgent | 0.868 | 0.719 | — |
| 全部 114 | **自研 my_agent** | **0.871** | 0.693 | [−0.033, +0.039] |
| 官方 test 40 | 官方基线 | 0.894 | 0.725 | — |
| 官方 test 40 | 自研 my_agent | 0.863 | 0.675 | [−0.094, +0.031] |

完整表(含 train / test、pass^2–3、执行前 LLM 自查版本)见 `analysis/retail_main_results.md`。

- **通过率:持平。** 差值区间都包含 0。
- **违规写操作:基线 9 次 → 自研 0 次。** 用同一套政策硬规则(`tau2_agent/my_guards.py`)离线回放两边的每一次写操作:基线 657 次里 9 次违规(7 次用了顾客没选过的支付方式、1 次订单状态不对、1 次换成同款),自研 654 次 0 次(`analysis/violation_replay.md`)。
- **失败归因:** 自研 Agent 的 59 次失败里约 44% 来自顾客模拟器或任务标注(例如顾客说"可以"的同一句话里就结束了对话、顾客自己说的取消原因和标准答案不一致);Agent 自己的错主要是选错商品款式 / 订单,这类判断错误代码规则管不到(`analysis/基线失败原因分类.md`)。
- **没采用的尝试:** 执行前让模型再自查一遍(`my_agent_verify`),通过率没有显著提升,21 次自查拦截多数是误报。
- **小样本的教训:** 前 20 题 × 4 次时自研 pass^4 = 0.900(基线 0.700),全量里同样 20 题只有 0.650,那次"提升"是噪声。

### 2. 可验证执行:故障注入(每格 20 次,共 480 次)

退款交给 Playwright 执行器在自建模拟商家后台(FastAPI + SQLite)完成。格子里:成功率(谎报成功 / 重复退款 / 漏报 / 转人工 次数)。

| 故障 | 固定脚本 | +重试 | +幂等查重与回读核验 | +LLM 自愈定位 |
|---|---|---|---|---|
| 无故障 | 100%(0/0/0/0) | 100%(0/0/0/0) | 100%(0/0/0/0) | 100%(0/0/0/0) |
| 随机延迟 | 35%(0/0/0/0) | 100%(0/0/0/0) | 100%(0/0/0/0) | 100%(0/0/0/0) |
| 弹窗遮挡 | 0%(0/0/0/0) | 0%(0/0/0/0) | 0%(0/0/0/20) | 100%(0/0/0/0) |
| 按钮改版 | 0%(20/0/0/0) | 0%(20/0/0/0) | 0%(0/0/0/20) | 100%(0/0/0/0) |
| 登录过期 | 0%(0/0/0/0) | 100%(0/0/0/0) | 100%(0/0/0/0) | 100%(0/0/0/0) |
| 提交超时(实际已成功) | 0%(0/0/20/0) | 0%(0/20/20/0) | 100%(0/0/0/0) | 100%(0/0/0/0) |
| **合计** | **22%** | **50%** | **67%** | **100%** |

LLM 自愈定位共调用模型 40 次,选按钮 40/40 正确。局限:后台和故障都是自己设计的("按钮改版"里旧 id 被"保存草稿"占用是刻意构造的),页面按钮少,真实复杂页面上自愈定位不一定这么准;执行器目前是独立实验,还没接进 τ³-bench 的 Agent 流程。

### 3. 提示注入防御:这个模型本身就没上当,防御层没有误伤

4 层防御逐层加:L0 官方基线(无防御)→ L1 提示词 + 工具结果标成"数据"→ L2 + 写前校验 → L3 + 按阶段开放工具。每条用例重复 3 次。

| 轮次 | 用例 | L0 | L1 | L2 | L3 |
|---|---|---|---|---|---|
| 第一轮:明着让它做坏事 | 8 条直接注入(自称管理员、伪造系统指令、跳过核验…)+ 12 条间接注入(订单备注、商品描述、用户资料里藏指令,含中文和伪造工具调用) | 0/60 违规 | 0/60 | 0/60 | 0/60 |
| 第二轮:参数劫持 | 7 条:顾客要办一件正常的事并回答"好",注入偷偷改退款去向 / 取消原因 / 地址 / 支付方式,或让 Agent 顺带多做一件事 | 0/21 违规 | 0/21 | 0/21 | 0/21 |
| 第二轮:正常请求办成 | 同上 | 18/21 | 19/21 | 18/21 | 18/21 |

- **结论如实写:** Agent 模型(DeepSeek)对这 27 类注入本身就有很强的抵抗力,连无防御的基线也是 0 违规,所以这组实验**测不出防御层之间的差别**;写前校验在全程没有被触发过,也没有误伤正常请求(没办成的 3 次都是 H4:订单地址本来就和默认地址一样,Agent 判断无需修改,是用例设计问题)。
- 第二轮是看到第一轮结果后加的,防御代码在两轮之间没有改过。
- 写前校验真正拦下违规的证据在 retail 回放里(基线 9 次 → 0 次),以及 `tests/` 里的单测和冒烟测试。
- 用例:`injection/cases.py`、`injection/cases_round2.py`;结果:`injection/results/`。

## Agent 怎么工作

```
顾客消息
  → 身份核验(之前看不到写操作工具)
  → 查询核实:用户、支付方式、订单状态记进"已核实信息"
  → 列出操作细节,等顾客明确同意(只问一次)
  → 写前校验(代码规则:身份、订单归属与状态、明确同意、支付方式、换货商品、取消原因)
       不通过 → 把原因当工具报错回传给模型重来(最多 2 次)→ 仍不通过:不执行,改为问顾客
  → 执行写操作 → 告知结果
```

每一轮"生成 → 校验 → 重试 / 兜底"是一个 LangGraph 状态图(`tau2_agent/my_agent.py`)。规则只用 Python 标准库写(`tau2_agent/my_guards.py`),有 12 个 pytest 单测;规则先在基线的 657 次写操作上离线回放,确认误拦率低(1.7%)再上线。

## 目录

| 路径 | 内容 |
|---|---|
| `tau2_agent/` | 自研 Agent 和写前校验规则;`INSTALL.md` 说明怎么装进 τ³-bench |
| `analysis/` | 失败归因、规则回放、结果对比(pass^k、train/test、置信区间)脚本和结果 |
| `executor/` | 模拟商家后台、Playwright 退款执行器、故障注入实验和结果 |
| `injection/` | 20 条提示注入用例和逐层防御实验 |
| `tests/` | 规则单测、冒烟测试(用假模型,不花钱) |

## 怎么复现

```bash
# 0. 按 tau2_agent/INSTALL.md 把 Agent 装进 tau2-bench(v1.0.1)
# 1. 官方基线和自研 Agent,各 114 题 × 4 次
tau2 run --domain retail --agent llm_agent --agent-llm openai/deepseek-flash-official --user-llm openai/deepseek-flash-official --num-trials 4 --save-to baseline_retail_t4
tau2 run --domain retail --agent my_agent  --agent-llm openai/deepseek-flash-official --user-llm openai/deepseek-flash-official --num-trials 4 --save-to my_agent_retail_t4
# 2. 对比与回放
python analysis/compare_runs.py --base 基线=<tau2-bench>/data/simulations/baseline_retail_t4/results.json --run 自研=<tau2-bench>/data/simulations/my_agent_retail_t4/results.json --split <tau2-bench>/data/tau2/domains/retail/split_tasks.json
python analysis/replay_guards.py <results.json> tau2_agent
# 3. 单测和冒烟测试(在 tau2-bench 的环境里)
python -m pytest tests -q
python tests/smoke_my_agent.py
# 4. 故障注入(需要 playwright、fastapi、uvicorn;自愈定位要配置 OPENAI_API_KEY / OPENAI_BASE_URL / MODEL_NAME)
cd executor && python run_fault_injection.py --runs 20
# 5. 注入防御(第一轮 / 第二轮;--model 可以换 Agent 模型)
python injection/run_injection.py --set round1 --repeats 3
python injection/run_injection.py --set round2 --repeats 3
```

## 评测方法与局限

- **版本固定**:τ³-bench tag v1.0.1(main 分支已是 2.0,分数和 1.x 不可比)。
- **框架改动**:NL 断言评委和环境接口默认是 gpt-4.1,中转站不提供,改成和 Agent 同一个模型;官方基线和自研 Agent 用同一份配置。
- **自己评自己**:Agent、顾客模拟器、评委是同一个模型,可能有偏差。
- **输出不确定**:温度 0、同一随机种子,同一配置两次分别跑出 105/114 和 98/114,所以主指标用 4 次的 pass^k,并给置信区间。
- **调参与报告分开**:只在官方 train(74 题)上看失败、改代码,test(40 题)只报成绩。如实说明:最早自定的"前 20 题开发集"里混有 5 道 test 题,看过它们的对话。
- **主指标用框架原始评分**,不自己剔除模拟器造成的失败;剔除只作补充分析(两边同时剔、剔哪些事先定好),结论不变。

## 致谢

评测框架、retail 任务、官方基线 Agent 和顾客模拟器来自 [sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench)。本仓库只包含自研部分。
