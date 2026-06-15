# R2A Dry-run / Smoke 指南

Dry-run 用来检查依赖、路径、Web UI、workspace 创建、报告生成和 fail-closed 行为。它不证明论文已经复现。

## 1. 启动 Web UI

```powershell
python run_web.py
```

打开浏览器后，先进入 Settings / backend 相关区域，检查：

- workspace base path 是否可创建。
- cache path 是否可写。
- WSL distro 是否存在。
- OpenClaw executable/config path 是否能检测。
- provider/model 是否能通过 Test OpenClaw 检测。

## 2. 创建 Workspace

在 Web UI 中选择或上传 paper，填写 reproduction goal，然后创建 workspace。第一次建议使用小目标和本地安全 backend，不要选择 full benchmark。

可以从 CLI 做轻量 smoke：

```powershell
r2a workflow --repo ./target_repo --goal "run a conservative smoke workflow" --executor shell --auto-approve
```

## 3. Reduced workflow 与 full benchmark

Reduced workflow 只做有限输入、有限命令和可审计 evidence，例如 source verification、build/import smoke、runtime smoke、input contract 或 reduced metrics。

Full benchmark 可能需要完整数据集、长训练/搜索、基线矩阵、GPU/大内存/长时间运行和人工授权。第一次不要运行 full benchmark。

## 4. 真实 LLM Workflow 的不稳定来源

- 网络/API 失败。
- OpenClaw config path 错误。
- provider/model 不可用。
- LLM 输出不符合 schema。
- Paper fallback `LOW_CONFIDENCE` 导致后续 Planner 上下文较弱。
- Windows local 无法执行 Linux-first 构建或脚本。

这些失败不一定代表 R2A 主流程损坏。

## 5. Fail-closed 后先看什么

如果 workflow fail-closed，不要立刻大改代码。先查看：

- run manifest
- `planner_transaction.json`
- runtime record
- stdout/stderr raw output
- `.r2a/FINAL_REPORT.md`
- `.r2a/TASK_SPEC.md`
- `.r2a/EXPERIMENT_CONTRACT.md`

如果失败是 `PLANNER_SCHEMA_VALIDATION_FAILED`，优先检查 Planner JSON 是否符合当前 canonical `PlannerOutput` schema，而不是放宽 schema。
