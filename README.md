---
title: AstrBot + NapCat
emoji: 🤖
colorFrom: blue
colorTo: purple
sdk: docker
pinned: false
---

<!-- 默认中文文档。English version: README_EN.md -->

# AstrBot + NapCat 一键部署到 Hugging Face，数据全量备份

一个把 **AstrBot**（AI 聊天机器人框架）+ **NapCat**（QQ / OneBot 桥接）打包进单一 Docker 镜像的项目，**免费托管在 Hugging Face Spaces**，内置 **Git 全量数据备份**（小文件 + 大文件 LFS），重启 / 重建不丢数据。

不需要服务器、不需要备案、CPU Basic 免费档即可运行。

## 功能特性

- 🚀 **一键部署**：推送到 HF Space（Docker SDK）即可，开箱即用
- 🤖 **AstrBot v4.x**：可视化面板配置模型（OpenAI / Claude / Gemini / 本地 Ollama 等）、插件系统
- 💬 **NapCat QQ 接入**：扫码登录，OneBot11 协议接入 AstrBot
- 💾 **数据全量备份**：配置、会话、QQ 登录态每 3 分钟自动提交推送（Git），大文件自动走 LFS
- 🔀 **Git / HF 双远端**：备份可指向 GitHub 仓库或 Hugging Face 仓库（`GIT_BACKEND` 一键切换）
- 🔐 **OpenResty 网关**：Lua 动态路由，单端口（7860）暴露所有服务
- 🛠 **运行开关**：`config.env` 控制 NapCat / Git 推送 / LFS 推送，改一行推一次即可生效

## 架构与端口

| 组件 | 端口 | 说明 |
| --- | --- | --- |
| OpenResty 网关 | 7860（对外） | 所有服务统一入口 |
| AstrBot | 6185 | 聊天机器人框架 + WebUI |
| NapCat | 6099 | QQ / OneBot 桥接（WebUI 面板） |
| sync 守护 | - | 周期 pull → commit → push，大文件 LFS 上传 |
| FileBrowser | 8888 | 容器文件管理（经网关 `/filebrowser/`） |

默认路由（`nginx/default_admin_config.json`，可在 `/admin/ui/` 修改）：
- `/` → AstrBot 控制台
- `/webui/`、`/api/ws/` → NapCat
- `/admin/ui/` → 路由管理界面（默认密码 `admin`）

## 快速开始（Hugging Face Spaces）

1. 在 huggingface.co 创建 Space：SDK 选 **Docker**（建议 Private）
2. 把本仓库推上去（`git push` 即可，Space 自动构建）
3. **必须配置的 Secrets**（Settings → Variables and secrets）：
   - `HF_REPO`：你的备份仓库 ID（如 `yourname/astrbot-backup`，需先在 HF 建一个空 model 仓库）
   - `HF_TOKEN`：HF 写权限 token（huggingface.co/settings/tokens 创建，`write` 角色）
4. **可选**：如果备份走 GitHub：
   - `GITHUB_REPO`：`yourname/astrbot-backup`
   - `GITHUB_PAT`：GitHub token（`repo` 权限）
5. 构建完成后访问 Space 地址：
   - `/` → AstrBot WebUI（首次启动日志里有初始用户名 `astrbot` 和一次性密码）
   - `/webui/` → NapCat 面板，扫码登录 QQ
   - `/admin/ui/` → 修改路由管理密码（默认 `admin`）

> 没有配置任何备份仓库时，sync 守护会安全跳过，容器内数据不持久化。

## 快速开始（本地 Docker）

```bash
docker build -t astrbot-napcat-hf:latest .
docker run -d -p 7860:7860 --name astrbot-napcat astrbot-napcat-hf:latest
```

打开 `http://localhost:7860/` 即可。

## 配置开关（config.env）

修改后推送（HF 或 GitHub）会自动重新构建生效：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ENABLE_NAPCAT` | `true` | 是否启动 NapCat（设为 `false` 可只跑 AstrBot） |
| `GIT_BACKEND` | `github` | 备份元数据远端：`github` 或 `hf` |
| `GIT_HF_REPO` | 同 `HF_REPO` | `GIT_BACKEND=hf` 时的元数据仓库 |
| `ENABLE_GIT_PUSH` | `true` | 是否推送到远端 git 仓库（关闭则数据仅存容器内） |
| `ENABLE_HF_PUSH` | `true` | 是否扫描上传大文件到 LFS |

## 数据备份机制

- 每次启动：拉取远端 → 对齐 → 迁移数据目录为符号链接 → 恢复 LFS 大文件
- 运行期：每 3 分钟（`SYNC_INTERVAL` 可调）`git pull --rebase` → commit → push
- 大文件（默认 > 60MB，`LFS_THRESHOLD` 可调）自动转 LFS：GitHub Release asset 或 HF LFS（git-lfs batch 协议）
- 备份目标默认（`SYNC_TARGETS` 可调）：
  - `home/user/AstrBot/data/`、`home/user/config/`
  - `app/napcat/config/`、`app/.config/QQ/`
  - `home/user/nginx/admin_config.json`、`home/user/filebrowser-data/filebrowser.db`

## 目录结构

```
├── Dockerfile                  # 构建全部依赖与运行时
├── config.env                  # 运行开关（NapCat / Git 推送 / LFS 推送）
├── supervisor/supervisord.conf # 进程编排（nginx、Xvfb、sync、NapCat、AstrBot）
├── nginx/                      # OpenResty 动态路由 + 管理 API
├── scripts/                    # 各服务启动脚本
├── sync/                       # 数据备份守护（Git + LFS，GitHub / HF 双后端）
│   └── web/                    # 同步状态 Web UI
└── docs/添加新进程.md           # 如何扩展新服务
```

## 路由管理 API

```bash
# 查看路由
curl -H "X-Admin-Password: admin" https://<host>/admin/routes.json
# 修改路由（default_backend 等）
curl -X POST -H "X-Admin-Password: admin" -H "Content-Type: application/json" \
  -d '{"default_backend":"http://127.0.0.1:6185","rules":[...]}' \
  https://<host>/admin/routes.json
# 修改密码
curl -X POST -H "X-Admin-Password: <old>" -H "Content-Type: application/json" \
  -d '{"new_password":"<new>"}' https://<host>/admin/password
```

## 常见问题

- **访问 502 / 空白页**：到 `/admin/ui/` 确认默认后端为 `http://127.0.0.1:6185`
- **NapCat 启动即退出**：非 root 容器已内置 `--no-sandbox`；如仍异常可设 `NAPCAT_FLAGS=--disable-gpu`
- **HF Space 构建慢**：apt 源默认使用德国镜像 `mirror.netcologne.de`，可用 `APT_MIRROR` 覆盖
- **AstrBot 首次 WebUI 慢**：会自动下载前端资源，耐心等待
- **备份仓库为空 / 没有数据**：确认 `HF_REPO` + `HF_TOKEN`（或 `GITHUB_REPO` + `GITHUB_PAT`）已配置，且数据大于 `LFS_THRESHOLD` 才走 LFS

## 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)：AI 聊天机器人框架
- [NapCat](https://github.com/NapNeko/NapCatAppImageBuild)：QQ / OneBot 协议桥接

## License

[MIT](./LICENSE)。本仓库集成上游项目，各自遵循其许可协议。
