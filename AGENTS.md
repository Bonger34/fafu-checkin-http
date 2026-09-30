# AGENTS.md

本仓库面向 agent 的入口。用户使用说明见 [README.md](README.md)。

代码在 `src/`，离线测试在 `tests/`，配置与会话态在仓库根目录。

| 任务 | 先读 |
|---|---|
| 改代码 / 接手维护 | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) —— 不变量与模块边界，改前必读 |
| 拿不准某个词 | [CONTEXT.md](CONTEXT.md) —— 领域词汇表，代码/日志/文档共用一套词 |
| 动登录链 / 签名 / 限流 | [docs/reverse-notes.md](docs/reverse-notes.md) —— 接口参数与实测证据 |
| 部署到服务器 / NAS / Termux | [docs/deploy.md](docs/deploy.md) |

改代码前先跑 `python3 -m unittest discover -s tests`：离线、秒级、不发真实请求。

`config.ini` 与 `state.json` 存的是**真实凭据**（已 gitignore）；对话、示例与提交信息一律用占位值。
