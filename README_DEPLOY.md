# R2A 发布前检查

本文用于 GitHub 发布前的最后人工检查。不要把它理解成自动 staging 脚本。

## 不要提交这些内容

- `.env`
- `.r2a/`
- `runtime/`
- `.r2a_runtime/`
- `workspace/`
- `cache/`
- `logs/`
- `web_settings.json`
- `CODEX_*.md`
- 本地 archive / backup

`.env.example` 可以提交，但不能包含真实 key。`.env` 是本地真实配置，必须保持私有。

`docs/` 和 `rep/` 历史报告已经私有归档，不是默认公开内容。不要为了发布把它们恢复回仓库。

`GITHUB_STAGING_WHITELIST.md` 可以作为本地 staging 参考，但默认不建议公开该文件，除非已经人工确认里面没有本地 archive 上下文。

## 推荐入口

Web UI 推荐启动入口：

```powershell
python run_web.py
```

直接 Streamlit 启动只是 debug fallback：

```powershell
streamlit run r2a_web/app.py
```

当前推荐运行环境是 Windows 10/11 + WSL2 Ubuntu。Windows local 适合轻量调试；真实论文复现 workflow 不保证在 Windows local 完整成功。

Docker 不是 Web UI first-class execution backend。

## 发布前 smoke

```powershell
python -m py_compile r2a_web\app.py r2a_web\workspace_state.py r2a\workspace\manager.py r2a\tools\wsl.py r2a\agents\engineer_agent.py r2a\core\state.py r2a\cli.py
```

```powershell
python -m pytest tests -q --tb=short
```

如果全量测试通过且只有 expected strict xfail，可以记录为 publication smoke passed。不要把 full benchmark 当成发布前 smoke。

## 显式 Staging

不要使用：

```powershell
git add .
```

使用显式路径，并在 staging 后检查：

```powershell
git status --short
git diff --cached --stat
git diff --cached --name-only
```

推荐候选和禁止候选见 `GITHUB_STAGING_WHITELIST.md`。该白名单是本地参考，不会执行任何 git 命令。
