# R2A Agent Setup Prompt

下面这份提示词可直接复制给 Codex、OpenClaw、Claude Code 或其他 coding agent，用于在一台新机器上检测并配置 R2A。请把所有尖括号内容替换成你的本机占位值，不要写入真实密钥或私人路径到版本库。

````text
你现在协助我在一台新机器上配置 R2A。R2A 是论文复现 multi-agent workflow 的 Python MVP，不是通用 benchmark 平台，也不保证任意论文一键完整复现。请按下面步骤工作，先检测、再给出建议，只有我确认后才执行会修改系统或项目状态的操作。

## 4.1 先做只读项目检测

1. 定位项目根目录，示例占位路径如下：
   - Windows: C:\path\to\R2A
   - Linux/WSL/macOS: /path/to/R2A
   - WSL 访问 Windows 项目时: /mnt/<drive-letter>/path/to/R2A
2. 只读检查这些文件是否存在：
   - pyproject.toml
   - README.md
   - README_LOCAL_SETUP.md
   - TESTING_GUIDE.md
   - DRY_RUN_GUIDE.md
   - README_DEPLOY.md
   - .env.example
   - run_web.py
   - r2a/
   - r2a_web/
   - tests/
3. 在完成检测前，不要改文件、不要安装依赖、不要运行 full benchmark、不要提交或推送 git。
4. 如果目录不是 git 仓库，或者缺少核心文件，请先报告缺失项和建议，不要猜测补齐。

## 4.2 环境检查

请检查并报告以下信息：

- OS 类型和版本：Windows、WSL、Linux 或 macOS。
- Python 版本：优先 Python 3.10 或更高。
- pip 是否可用。
- Git 是否可用。
- Node.js 是否可用；R2A 基础 Python workflow 不强制依赖 Node，但某些 agent 或外部工具可能需要。
- Codex CLI 或当前 coding agent 环境是否可用。
- OpenClaw 是否可用，以及 executable/config/provider/model/runner/agent 是否能检测到。
- 当前 shell：PowerShell、cmd、bash、zsh 或其他。

请用只读命令检查，例如：

Windows PowerShell:
    python --version
    python -m pip --version
    git --version
    node --version
    where python
    where git

Linux/WSL/macOS shell:
    python3 --version
    python3 -m pip --version
    git --version
    node --version
    which python3
    which git

如果某个命令不存在，只报告结果和建议，不要直接全局安装。

## 4.3 安装命令建议

在我确认后，按对应平台给出或执行安装步骤。

Windows PowerShell:
    cd C:\path\to\R2A
    python -m venv .venv
    .\.venv\Scripts\activate
    python -m pip install -U pip
    pip install -e ".[dev]"

Linux/WSL/macOS shell:
    cd /path/to/R2A
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -U pip
    pip install -e ".[dev]"

如果 Windows local 安装失败，并且错误来自 Linux-first 依赖、C++ 扩展、FAISS、GPU 或论文仓库脚本，请建议使用 WSL 或手动 Docker 环境，而不是强行改 R2A 主流程。

## 4.4 .env 安全

1. 如果需要本地配置，只从模板复制：
   Windows PowerShell:
       copy .env.example .env

   Linux/WSL/macOS shell:
       cp .env.example .env

2. 不要把 .env 加入 git。
3. 不要在聊天、日志、README、测试输出或提交中打印真实 API key。
4. 如果需要填写 key，只说明变量名和位置，让我自己填写。
5. 如果发现 .env、真实 token、真实 cookie、私有路径或个人账号信息被暂存，请立刻报告并停止发布相关操作。

## 4.5 路径配置变量

请检查或指导我配置这些变量，示例必须使用占位符，不要使用真实个人路径：

    R2A_WORKSPACE_ROOT=C:\path\to\r2a-workspaces
    R2A_CACHE_DIR=C:\path\to\r2a-cache
    R2A_OPENCLAW_EXECUTABLE_PATH=C:\path\to\openclaw.exe
    R2A_OPENCLAW_CONFIG_PATH=C:\path\to\openclaw-config.yaml
    R2A_OPENCLAW_PROVIDER=<provider-name>
    R2A_OPENCLAW_MODEL=<model-name>
    R2A_OPENCLAW_RUNNER=<runner-name>
    R2A_OPENCLAW_AGENT=<agent-name>

Linux/WSL/macOS 示例：

    R2A_WORKSPACE_ROOT=/path/to/r2a-workspaces
    R2A_CACHE_DIR=/path/to/r2a-cache
    R2A_OPENCLAW_EXECUTABLE_PATH=/path/to/openclaw
    R2A_OPENCLAW_CONFIG_PATH=/path/to/openclaw-config.yaml

如果使用 WSL 访问 Windows 文件，请确认 Windows 路径和 WSL 路径可以互相转换，例如 C:\path\to\R2A 对应 /mnt/c/path/to/R2A。不要把某台机器的真实绝对路径写入 README 或提交。

## 4.6 缺少 WSL、OpenClaw 或 API key 时的处理

- 没有 WSL：可以先运行基础 Python 单元测试和 Web UI smoke；不要承诺 Linux-first 论文工程能在 Windows local 完成。
- 没有 OpenClaw：可以先跑不需要真实 agent backend 的测试、schema 检查和 UI 启动；真实 Planner/Engineer/Reviewer agent 需要后续配置。
- 没有 API key：不要伪造 key，不要联网试错；只运行不需要真实模型调用的测试。
- 没有论文数据授权：不要下载或替代真实数据；先做 dry-run、smoke 或 reduced 任务，并记录限制。
- OpenClaw 配置不完整：报告缺失 executable、config、provider、model、runner 或 agent，不要把错误归咎于 R2A 主流程。

## 4.7 验证命令

安装完成后，先跑轻量验证，不要直接跑 full benchmark。

Windows PowerShell:
    python -m py_compile r2a_web\app.py r2a_web\workspace_state.py r2a\workspace\manager.py r2a\tools\wsl.py r2a\agents\engineer_agent.py r2a\core\state.py r2a\cli.py
    python -m pytest tests -q --tb=short

Linux/WSL/macOS shell:
    python -m py_compile r2a_web/app.py r2a_web/workspace_state.py r2a/workspace/manager.py r2a/tools/wsl.py r2a/agents/engineer_agent.py r2a/core/state.py r2a/cli.py
    python -m pytest tests -q --tb=short

如果测试出现 expected strict xfail，请区分它和 active failure。若出现 active failure，请先报告失败测试、错误摘要和可能原因，再建议最小修复。

## 4.8 Web UI smoke

Web UI 推荐入口：

    python run_web.py

如果需要 debug fallback：

    streamlit run r2a_web/app.py

Smoke 只需确认服务能启动、首页可访问、workspace/backend/settings 相关页面不崩溃。不要把 Web UI smoke 扩大成 full workflow、full benchmark 或大量下载。

## 4.9 OpenClaw 检测

请检查：

- executable path 是否存在并可执行。
- config path 是否存在。
- provider/model/runner/agent 是否已配置。
- Web UI 的 Test OpenClaw / Settings 是否能检测到配置。
- 如果命令失败，请记录 returncode、stderr 摘要和使用的占位路径，不要打印密钥。

如果 OpenClaw 不可用，先报告如何补齐配置；不要修改 R2A schema 或让 Engineer 兼容 raw Planner JSON 来绕过 Planner 校验。

## 4.10 Workflow 稳定性判断

真实 workflow 受这些因素影响：

- LLM/OpenClaw 对输出格式的服从性。
- API、网络、quota、timeout。
- Windows/WSL 路径转换。
- 论文源码是否可用。
- Linux-first 脚本、C++、FAISS、GPU 或系统依赖。
- 数据集授权与下载限制。
- PlannerOutput canonical schema。
- Engineer 是否只完成 reduced/smoke 范围。
- Reviewer 是否有足够 artifacts、manifest、provenance 和指标证据。

如果出现 PLANNER_SCHEMA_VALIDATION_FAILED，请说明：Planner 可能已经写出 staging artifacts，但 PLANNER_OUTPUT.json 没有通过 canonical schema；事务不会 commit；Engineer 不会读取未提交 staging；这是 fail-closed。排查 planner_transaction、validation errors、returncode、raw/staging artifacts、prompt/schema contract 和模型配置。不要直接放宽 schema。

## 接手项目时的架构边界、失败解释和修复规范

R2A 的核心 agent 流程是 Paper -> Planner -> Human Approval / Router -> Engineer -> Manager -> Reviewer -> Final。接手任务前，请先明确当前请求属于只读审计、小范围修复、发布整理还是 workflow 调查。

必须保持这些架构边界：

- Reviewer-only 正式判级：正式 reproduction level 和 verdict 只由 Reviewer 决定。
- Manager 只做轻量工程输出检查，不恢复复杂 Manager，也不做 formal grading。
- Final 不重新判级，只聚合 Reviewer verdict、证据摘要和限制说明。
- EvidenceDecision / FinalDecision 只聚合，不替代 Reviewer 的正式判级。
- Planner transaction 是 Planner 到 Engineer 的边界；只有 committed canonical artifacts 才能进入后续阶段。
- Engineer 只读 committed `PLANNER_OUTPUT.json`、`TASK_SPEC.md` 和 `EXPERIMENT_CONTRACT.md`，不要读取 Planner staging artifacts。
- UI 只展示 workflow 状态、verdict、level 和 artifacts，不写回 verdict、level 或 workflow state。

禁止事项：

- 不要让 rules backend 重新正式判级。
- 不要让 Final 重新判级。
- 不要让 Engineer 兼容 raw Planner JSON 或读取未提交 staging。
- 不要放宽 `PlannerOutput` schema，除非用户明确批准。
- 不要修改历史 run artifacts。
- 不要跑 full benchmark，除非用户明确要求。
- 不要运行 `git add .`。

常见失败解释：

- `PLANNER_SCHEMA_VALIDATION_FAILED`：Planner JSON 不符合 canonical schema；transaction 不 commit；Engineer 不运行。这是 fail-closed 保护。
- `OpenClaw not detected`：通常是 executable、config、provider/model/runner/agent 或环境变量缺失；单元测试通常不需要真实 key。
- `current_stage=final` 但 `failed_stage=planner`：Final 汇总了失败状态，不代表 Planner 成功，也不代表 Reviewer 已正式判级。
- expected strict xfail：这是已知产品决策点或受控测试预期，不等同于 active failure。

推荐接手流程：

1. 先读 README.md、README_LOCAL_SETUP.md、TESTING_GUIDE.md、DRY_RUN_GUIDE.md 和 README_DEPLOY.md。
2. 明确任务类型和允许修改范围。
3. 小范围修改后报告修改文件、测试命令、是否修改历史 run、是否跑 full benchmark、是否执行 git add/commit/push。
4. 如果需要 staging，只按用户允许的显式路径执行。

发布后 bug 修复规范：

- 从 `fix/<issue-name>` 分支做小范围修复，保持 `main` 可测试。
- patch version 用于向后兼容 bug fix；minor version 用于更明显的行为、接口或文档范围变化。
- release notes 建议包含 `Fixed`、`Changed`、`Tests` 和 `Notes`。

## 4.11 最终配置报告和安全要求

完成后请给我一份简短配置报告，包含：

- 项目根目录是否识别成功。
- OS、Python、Git、Node、Codex/coding agent、OpenClaw 检测结果。
- 是否创建 .venv。
- 是否安装 editable dev 依赖。
- 是否复制 .env.example 到 .env。
- 是否发现真实 secret、真实私有路径或不应提交文件。
- py_compile 结果。
- pytest 结果。
- Web UI smoke 结果。
- OpenClaw 检测结果。
- 当前不能运行真实 workflow 的阻塞项。
- 建议下一步命令。

安全要求：

- 不要运行 git add .。
- 不要提交 .env、.r2a、runtime、cache、logs、web_settings.json、真实 API key 或机器私有配置。
- 不要 commit 或 push，除非我明确要求。
- 不要下载大数据集、运行 full benchmark 或扩大任务范围，除非我明确批准。
- 不要把一次 smoke 说成完整论文复现。
````
