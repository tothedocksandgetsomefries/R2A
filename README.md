# R2A

R2A 是 Research Reproduction Agent，一个用于论文复现工作流的 Python MVP。它把论文、源码和用户目标拆成可执行的阶段：Paper、Planner、Engineer、Manager、Reviewer 和 Final。

R2A 的默认目标不是第一次就跑完整论文 benchmark，而是用可审计证据逐步推进到当前能够支持的复现等级。真实 L3/L4 需要官方或论文关联输入、真实执行得到的指标、命令 provenance，以及 Reviewer/Manager 接受。

Web UI 首次运行建议：上传论文文件，填写可选 guidance（例如论文源码地址、必要输入文件或数据限制），选择 workspace 和 backend，检查 OpenClaw / runtime 配置后再点击 Run workflow。需要下载官方数据集、联网获取算法依赖或启用多轮自动迭代时，请先确认授权、数据规模、quota 和时间预算；不要把这类真实 workflow 当作最小 smoke test。

## 项目定位

R2A 是自动论文复现 multi-agent workflow 的 MVP，仍处于早期公开阶段；它不是通用 benchmark 平台，也不承诺对任意论文做到一键完整复现。它更关注把复现过程拆成可追踪、可审计、可回放的步骤，让每个阶段都留下 artifacts、manifest、provenance 和 Reviewer verdict。

R2A 的优先级是先跑 reduced、smoke、paper-aligned 任务，再逐步靠近更高复现等级。系统会保留证据等级和限制说明，避免把一次局部 smoke 误报成完整复现。

## 可能问题
- Planner 可能因模型输出不完整、schema 不合规或后端启动异常而缺少必需产物。
- OpenClaw 偶发启动失败、通信异常或 sub-agent yield/resume 挂起，可能导致阶段长时间停留或输出不完整。
- Reviewer 偶尔可能生成矛盾候选 verdict，R2A 会通过结构化校验拦截。
  
## 核心功能

- Paper 阶段保存论文相关输入、metadata、digest 和后续 agent 可引用的 artifacts。
- Planner 阶段把论文目标转成结构化计划，并输出 `PLANNER_OUTPUT.json`、`TASK_SPEC.md` 和 `EXPERIMENT_CONTRACT.md`。
- Engineer 阶段按计划执行本地、WSL 或 OpenClaw 任务，收集命令、日志、结果和 provenance。
- Manager 阶段做轻量工程产物检查，确认文件、命令和基本输出是否齐全；它不是 formal grading。
- Reviewer 阶段依据证据给出正式 reproduction level 和 verdict。
- Final 阶段聚合前序证据、限制和 verdict，不重新评分。
- Web UI 提供 workspace、backend、OpenClaw/runtime 配置、workflow 运行和 artifacts 浏览入口。

## 工作流阶段

- Paper：收集论文、源码、用户目标和基础 metadata，准备后续阶段的输入。
- Planner：生成可执行复现计划、任务规格和实验契约，并通过 canonical schema 校验。
- Human Approval / Router：在需要下载、执行、联网或扩大范围前保留人工确认和路由决策。
- Engineer：在 local、WSL 或 OpenClaw backend 中执行 reduced/smoke/paper-aligned 任务并记录证据。
- Manager：检查工程输出是否满足任务规格的基本要求，标记缺失证据或执行问题。
- Reviewer：基于 artifacts、manifests、provenance 和指标判断当前复现等级与 verdict。
- Final：汇总 workflow 结果、限制、证据位置和 Reviewer verdict，供用户审阅。

## 复现等级 L0-L6

- L0：只有论文或任务描述，尚未形成可执行证据。
- L1：完成论文解析、任务理解或计划草稿，但还没有真实运行结果。
- L2：有结构化 Planner 输出、任务规格或实验契约，执行仍未充分发生。
- L3：完成 reduced 或 smoke 级真实执行，产生可追踪命令、日志和局部结果。
- L4：完成 paper-aligned reduced reproduction，输入、指标和限制与论文目标有明确对应。
- L5：更接近完整论文设置，关键指标、配置和运行约束有充分 provenance 支撑。
- L6：高度完整、可复验的论文级复现，包含完整数据、配置、指标和审计证据。

Reviewer 决定当前正式复现等级和 verdict。Final 只聚合 Reviewer verdict 与证据摘要，不重新打分。

## 推荐环境

- Windows 10/11 + WSL2 Ubuntu。
- Python 3.10 或更高版本。
- Windows local 可以用于安装依赖、阅读代码、启动部分 Web UI 和轻量调试；复杂 C++、FAISS、GPU 或 Linux-first 脚本不保证能在 Windows local 成功。
- Docker 不是 Web UI 的 first-class execution backend；只有任务明确需要时再作为手动/辅助环境使用。

## 快速开始

从 GitHub clone 后：

```powershell
git clone <repo-url>
cd R2A

python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip
pip install -e ".[dev]"
```

复制本地配置模板：

```powershell
copy .env.example .env
```

`.env.example` 是可提交的配置模板；`.env` 是本机私有配置，通常包含路径、backend 或 API key，默认不应提交。

只跑基础单元测试时通常不需要真实 API key。然后执行最小编译检查：

```powershell
python -m py_compile r2a_web\app.py r2a_web\workspace_state.py r2a\workspace\manager.py r2a\tools\wsl.py r2a\agents\engineer_agent.py r2a\core\state.py r2a\cli.py
```

执行测试：

```powershell
python -m pytest tests -q --tb=short
```

当前全量测试应通过；可能显示两个 expected strict xfail，它们是 Manager 产品决策点，不是 active failure。

启动 Web UI：

```powershell
python run_web.py
```

直接启动 Streamlit 只是 debug fallback：

```powershell
streamlit run r2a_web/app.py
```

第一次不要把 full benchmark 当作 smoke test。先确认依赖、路径、WSL、OpenClaw detection、workspace 创建和最小 workflow 能正常工作。

## 常用文档

- [README_LOCAL_SETUP.md](README_LOCAL_SETUP.md)：本地安装、`.env`、WSL 路径和 OpenClaw 配置。
- [TESTING_GUIDE.md](TESTING_GUIDE.md)：编译检查、pytest、expected strict xfail 和常见失败解释。
- [DRY_RUN_GUIDE.md](DRY_RUN_GUIDE.md)：第一次 dry-run / smoke 怎么做。
- [README_DEPLOY.md](README_DEPLOY.md)：发布前不要提交什么，以及如何显式 staging。
- [AGENT_SETUP_PROMPT.md](AGENT_SETUP_PROMPT.md)：给 Codex / OpenClaw / coding agent 使用的跨机器配置提示词模板。

## Web UI

推荐入口：

```powershell
python run_web.py
```

Web UI 用于创建 workspace、选择 Paper/Planner/Engineer/Manager/Reviewer backend、检查 WSL/OpenClaw 配置、运行 workflow 并查看 `.r2a` 报告。默认 workspace/cache 路径可以通过 `.env` 或 shell 环境变量覆盖。

## OpenClaw

OpenClaw 是可选 backend；但如果要运行真实 Planner / Engineer / Reviewer agent，就需要配置 OpenClaw executable、config path、provider/model/runner/agent 或对应 API key。

常用变量：

```env
R2A_OPENCLAW_EXECUTABLE_PATH=
R2A_OPENCLAW_CONFIG_PATH=
R2A_OPENCLAW_PROVIDER=
R2A_OPENCLAW_MODEL=
R2A_OPENCLAW_RUNNER=
R2A_OPENCLAW_AGENT=
```

OpenClaw provider/model 后续可以在 Web UI 的 Test OpenClaw / Settings 中检测和选择；它们不是首次运行基础测试的必填项。

## Docker Guidance (Manual Only)

R2A does not currently provide an automatic Docker runner. Docker should be treated as a manual environment option when a target paper project is clearly Linux-first, dependency-heavy, or difficult to build on the host system.

Do not use Docker to bypass download approval, run full-scale benchmarks, pull large datasets, or expand an L4 reduced-scope task into full reproduction. 如果手动 Docker smoke 产生了有效证据，请把命令、exit code、日志路径和运行限制记录到 `.r2a/results/docker_runtime_smoke.csv`、相关 smoke CSV 或 `command_manifest.csv`。

## 真实 Workflow 稳定性说明

真实 workflow 会调用 LLM / OpenClaw。Planner、Engineer、Reviewer 依赖模型输出，不同模型可能有不同的格式服从性，并且会受到 API、网络、quota、timeout、Windows/WSL 路径转换、本地依赖和目标论文工程质量影响。

常见风险包括：论文源码不可用、Linux-first 脚本、复杂 C++/FAISS/GPU 依赖、数据集授权、PlannerOutput schema 不匹配、Engineer 只能完成 reduced/smoke 运行，以及 Reviewer 只能依据已保存证据给出 verdict。一次 workflow 失败不一定表示项目不可用，可能只是当前 backend、模型、依赖或任务范围不满足。

如果出现 `PLANNER_SCHEMA_VALIDATION_FAILED`，表示 Planner 已写出 staging artifacts，但 `PLANNER_OUTPUT.json` 没有通过 R2A 当前 canonical `PlannerOutput` schema。事务不会 commit，Engineer 不会读取未提交的 staging 内容，也不会继续执行，这是 fail-closed 保护。它不一定代表 OpenClaw 路径错误。

排查时先查看 `planner_transaction.json`、validation errors、OpenClaw returncode、raw artifacts 和 staging artifacts，再检查 provider/model、prompt/schema contract 与 Planner 输出格式。不要为了通过而放宽 schema，也不要让 Engineer 兼容 raw Planner JSON；应该强化 Planner prompt、schema contract 或模型配置，让 Planner 产出符合 canonical schema 的 `PLANNER_OUTPUT.json`。

## 不要提交本地内容

不要提交：

- `.env`
- `.r2a/`
- `runtime/`
- `.r2a_runtime/`
- workspace、cache、logs
- `web_settings.json`
- 真实 API key

发布前不要直接运行 `git add .`。请按显式路径 staging，并查看 [README_DEPLOY.md](README_DEPLOY.md)。

## 发布后 bug 修复建议

公开发布后，建议从 `fix/<issue-name>` 分支做小范围修复，保持 `main` 可测试。修改后先跑相关 pytest 或 smoke，再按显式路径 staging，不要使用 `git add .`。发布说明建议分成 `Fixed`、`Changed`、`Tests` 和 `Notes`；向后兼容修复通常走 patch version，行为或文档范围更明显的变化再考虑 minor version。
