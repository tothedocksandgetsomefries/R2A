# R2A 本地安装指南

本文面向刚从 GitHub clone 下来的新用户。推荐环境是 Windows 10/11 + WSL2 Ubuntu。

## 1. 下载和安装

```powershell
git clone <repo-url>
cd R2A

python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip
pip install -e ".[dev]"
```

`pyproject.toml` 中已经定义 `dev` extra，包含 pytest 和 streamlit。只安装运行包时可以使用：

```powershell
pip install -e .
```

## 2. 创建本地配置

`.env.example` 是可提交的模板，`.env` 是本地真实配置，不要提交。

```powershell
copy .env.example .env
```

只跑基础单元测试时通常不需要真实 API key。真实 agent workflow 才需要配置 OpenClaw 和 provider key。

常用路径变量：

```env
R2A_WORKSPACE_BASE=
R2A_WSL_CACHE_DIR=
R2A_WEB_SETTINGS_PATH=
R2A_OPENCLAW_EXECUTABLE_PATH=
R2A_OPENCLAW_CONFIG_PATH=
```

可选模型和 provider 变量：

```env
R2A_OPENCLAW_PROVIDER=
R2A_OPENCLAW_MODEL=
R2A_OPENCLAW_RUNNER=
R2A_OPENCLAW_AGENT=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=
```

OpenClaw provider/model 后续可以通过 Web UI 的 Test OpenClaw 检测和选择，因此不是首次运行测试的必填项。

## 3. WSL 路径怎么填

### Workspace base dir

这是 Windows 侧路径，用来放 R2A 创建的 workspace。例如：

```text
C:\Users\<you>\AppData\Local\R2A\workspaces
C:\R2A_WORKSPACES_SAMPLE
```

可通过下面变量覆盖：

```env
R2A_WORKSPACE_BASE=
```

### WSL cache dir on Windows drive

这也是 Windows 侧路径，用于 WSL 相关 cache。例如：

```text
C:\Users\<you>\AppData\Local\R2A\cache
C:\R2A_CACHE_SAMPLE
```

可通过下面变量覆盖：

```env
R2A_WSL_CACHE_DIR=
```

R2A 在 WSL 执行时会把 Windows drive 路径映射为 WSL 可访问路径，例如 `/mnt/c/...`。用户通常不需要手动在这些变量里填写 `/mnt/c/...`。

### OpenClaw executable/config path

如果 OpenClaw 安装在 WSL，推荐填写 WSL POSIX 路径：

```text
/home/<user>/.nvm/versions/node/<version>/bin/openclaw
/home/<user>/.openclaw/openclaw.json
```

不推荐优先填写 UNC 路径：

```text
\\wsl.localhost\Ubuntu\home\<user>\.openclaw\openclaw.json
```

因为 WSL runtime 内执行命令时更适合 `/home/...` 路径。

## 4. 没有 WSL 怎么办

R2A 当前推荐 Windows + WSL2 Ubuntu。没有 WSL 时：

- 可以安装依赖、阅读代码、启动部分 Web UI。
- 可以运行一部分纯 Python 单元测试。
- 可以选择 Engineer execution environment = windows 做轻量调试。
- 不保证复杂论文复现、FAISS/C++、Linux-first scripts 能在 Windows local 成功。

如果目标是跑真实论文复现 workflow，建议安装 WSL2 Ubuntu。如果只是看项目或跑基础测试，可以先不配置 OpenClaw 和真实 API key。

检查 WSL：

```powershell
wsl -l -v
```

如果显示 `Ubuntu`，Web UI 里填 `Ubuntu`；如果显示 `Ubuntu-22.04`，就填 `Ubuntu-22.04`。

## 5. 验证安装

最小编译检查：

```powershell
python -m py_compile r2a_web\app.py r2a_web\workspace_state.py r2a\workspace\manager.py r2a\tools\wsl.py r2a\agents\engineer_agent.py r2a\core\state.py r2a\cli.py
```

全量测试：

```powershell
python -m pytest tests -q --tb=short
```

全量测试应通过；当前可能显示两个 expected strict xfail，它们是 Manager 产品决策点，不是 active failure。

## 6. 启动 Web UI

推荐入口：

```powershell
python run_web.py
```

直接 Streamlit 启动只是 debug fallback：

```powershell
streamlit run r2a_web/app.py
```

## 7. 不要提交本地内容

不要提交 `.env`、`.r2a/`、`runtime/`、`.r2a_runtime/`、workspace、cache、logs、`web_settings.json` 或真实 API key。发布前不要直接 `git add .`。
