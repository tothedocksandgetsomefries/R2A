# R2A 测试指南

这些命令用于确认项目能导入、基础 workflow 代码可运行、Web/workspace 相关模块没有明显语法或依赖问题。它们不等同于论文复现成功。

## 1. 测试前准备

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip
pip install -e ".[dev]"
```

只跑基础单元测试通常不需要真实 API key，也不要求 OpenClaw 已经配置完成。

## 2. 最小 py_compile

```powershell
python -m py_compile r2a_web\app.py r2a_web\workspace_state.py r2a\workspace\manager.py r2a\tools\wsl.py r2a\agents\engineer_agent.py r2a\core\state.py r2a\cli.py
```

## 3. 全量 pytest

```powershell
python -m pytest tests -q --tb=short
```

当前全量测试应通过；可能显示两个 expected strict xfail。它们是 Manager 产品决策点，不是 active failure：

- 是否让 Manager 解析 `project_tests.csv` 的 critical failure。
- 是否让 Manager 强制检查 `source_verification.csv` 的 commit provenance mismatch。

## 4. 常见失败分类

- 缺依赖：重新运行 `pip install -e ".[dev]"`。
- WSL 不可用：运行 `wsl -l -v`，确认 Web UI 中的 distro 名称与系统一致。
- OpenClaw 未配置：基础单元测试通常可以先跳过真实 OpenClaw；真实 agent workflow 需要配置 executable/config/provider/model。
- 路径不可写：检查 `R2A_WORKSPACE_BASE`、`R2A_WSL_CACHE_DIR`、`R2A_WEB_SETTINGS_PATH`。
- Windows local 无法构建 Linux-first 项目：这是环境限制，不一定是 R2A 主流程 bug。
- `PLANNER_SCHEMA_VALIDATION_FAILED`：通常是 LLM 输出没有符合 canonical `PlannerOutput` schema。先看 validation errors、`planner_transaction.json`、OpenClaw 配置和 Planner prompt/schema contract，不要直接放宽 schema。

## 5. 不要把 full benchmark 当成首次测试

Full benchmark 可能需要大数据、长时间运行、GPU、Docker、外部下载和人工授权。第一次 clone 后请先跑 py_compile、pytest、Web UI smoke 和 reduced workflow。
