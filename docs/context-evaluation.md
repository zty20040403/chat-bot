# 上下文评测

评测分为两层，不能混用它们的结果：

1. `tests/test_context_accuracy_eval.py` 检查连续原文是否进入上下文、检索结果是否经过范围过滤。
   其中语义结果是测试替身，不代表真实 BGE-M3 检索质量。
2. `tools/context_eval.py` 对实际生成的回答评分：关联对象、证据编号、正文必要信息、
   不相关内容、个人事实归属、跨群内容，以及真实返回的前五条检索结果。

评测不会改变线上上下文策略，不会截断成固定条数，也不会增加追问澄清阈值。

## 不调用模型：评分已有结果

```sh
python tools/context_eval.py --predictions /tmp/context-answers.json --output /tmp/context-report.json
```

输入是 JSON 数组，每条包含 `case_id`（场景名加冒号和变体编号，从 0 开始）、
`answer`、`focus_id`、`evidence_ids`、`scope_key`。
需要检索的场景还要提供 `retrieved_ids`；涉及个人事实时提供
`personal_facts: [{"user_id": 7, "evidence_id": 1}]`。
关联编号使用场景里的原始 `id`，不是数据库自增编号。

缺失、报错和超时的样例也计入分母，不会因为失败被剔除而得到虚高分数。
重复或未知的 case_id 会直接报错。退出码 0 表示通过，1 表示未通过。

## 显式调用真实模型和 BGE-M3

先在环境变量中提供已有模型配置与 embedding 配置，不要将 Key 写进命令或提交到仓库。
运行一个受限样本批次：

```sh
python tools/context_eval.py --live --profile qwen-local --limit 5 --embeddings --output /tmp/context-live.json
```

`--limit` 是场景数；一个场景可能包含多个追问变体。`--live` 才会产生模型调用费用。
`--embeddings` 使用配置中的 embedding 模型，当前项目配置为 BGE-M3。
向量由实际模型生成，再在隔离样本内用精确余弦相似度排序，不读取预设 `semantic_order`。
它测试语义模型，不测试生产 pgvector 索引性能。省略该开关时不能声称检索达标。

使用生产的 Ledger/ContextStore 构造连续时间线，并通过现有模型网关生成回答。
模型看不到 `expected_focus`、`required`、`forbidden` 等答案标签。
运行数据保存在临时目录与内存数据库中，不写入线上聊天记录，不发送 QQ 消息。
默认最多 5 个场景。完整批次可以把 limit 增大到场景总数。

## 如何读报告

- `fixture_count` 是不同场景数，`prompt_count` 是包含变体的样本数，不能把变体说成独立真实案例。
- `focus_accuracy` 是关联对象匹配率；`recall_at_five` 是期望原文出现在检索前五条的比例。
- `strict_answer_pass_rate` 是规则验收通过率，不等同于人工判断的整体聪明程度。
- `cross_scope_count`、`wrong_personal_fact_count` 分别记录串群与结构化个人事实归属错误。
- `cases[].reasons` 给出每个样例失败原因，原始生成内容保留在 `predictions` 中方便人工核验。

当前验收门槛：关联匹配和规则通过率至少 90%；存在检索场景时 Recall@5 至少 90%；
串群和个人事实归属错误为 0；不相关正文低于 5%。这些是目标，不是已测得的成绩。

关键词规则可能误伤合理改写，也可能漏掉隐含的张冠李戴。因此上线前还应人工抽查
失败案例和一部分通过案例。这个隔离基准不是完整 QQ 工具调用、沙盒或多轮对话的端到端评测。

本轮改动只运行离线评分和回归测试，未花费 API Token 跑完整模型基准。
