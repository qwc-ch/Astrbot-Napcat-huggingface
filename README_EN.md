# AstrBot + NapCat on Hugging Face, with full data backup

Package **AstrBot** (AI chatbot framework) + **NapCat** (QQ / OneBot bridge) into a single Docker image that runs **for free on Hugging Face Spaces**, with a built-in **Git full-data backup** (small files + LFS for large files) so nothing is lost across restarts/rebuilds.

No server, no ICP filing — the CPU Basic free tier is enough.

## Features

- 🚀 **One-click deploy**: just push to an HF Space (Docker SDK)
- 🤖 **AstrBot v4.x**: visual dashboard to configure models (OpenAI / Claude / Gemini / local Ollama, etc.) and plugins
- 💬 **NapCat QQ**: QR-code login through the OneBot11 protocol
- 💾 **Full data backup**: config, sessions, QQ login state are committed & pushed every 3 minutes (large files via LFS)
- 🔀 **Git / HF dual remotes**: point your backup at GitHub or Hugging Face (`GIT_BACKEND` toggles)
- 🔐 **OpenResty gateway**: Lua dynamic routing, single port (7860)
- 🛠 **Runtime toggles**: `config.env` controls NapCat / Git push / LFS push — edit a line, push, done

## Architecture & ports

| Component | Port | Notes |
| --- | --- | --- |
| OpenResty gateway | 7860 (public) | single entry point |
| AstrBot | 6185 | chatbot + WebUI |
| NapCat | 6099 | QQ / OneBot bridge (WebUI) |
| sync daemon | - | periodic pull → commit → push + LFS upload |
| FileBrowser | 8888 | container file manager (via `/filebrowser/`) |

Default routes (editable at `/admin/ui/`):
- `/` → AstrBot dashboard
- `/webui/`, `/api/ws/` → NapCat
- `/admin/ui/` → Router admin UI (default password `admin`)

## Quick start (Hugging Face Spaces)

1. Create a Space on huggingface.co: SDK **Docker** (Private recommended)
2. Push this repo to it (Space auto-builds on push)
3. **Required secrets** (Settings → Variables and secrets):
   - `HF_REPO`: your backup repo ID (e.g. `yourname/astrbot-backup`; create an empty model repo on HF first)
   - `HF_TOKEN`: write-capable HF token (huggingface.co/settings/tokens, `write` role)
4. **Optional** — if backing up to GitHub instead:
   - `GITHUB_REPO`: `yourname/astrbot-backup`
   - `GITHUB_PAT`: GitHub token (`repo` scope)
5. After the build, visit the Space URL:
   - `/` → AstrBot WebUI (first-run logs show initial username `astrbot` + one-time password)
   - `/webui/` → NapCat panel, scan QR to log in to QQ
   - `/admin/ui/` → change the router admin password (default `admin`)

> With no backup repo configured, the sync daemon safely skips persistence.

## Quick start (local Docker)

```bash
docker build -t astrbot-napcat-hf:latest .
docker run -d -p 7860:7860 --name astrbot-napcat astrbot-napcat-hf:latest
```

Open `http://localhost:7860/`.

## Runtime toggles (config.env)

| Variable | Default | Notes |
| --- | --- | --- |
| `ENABLE_NAPCAT` | `true` | set `false` to run AstrBot only |
| `GIT_BACKEND` | `github` | backup metadata remote: `github` or `hf` |
| `GIT_HF_REPO` | same as `HF_REPO` | metadata repo when `GIT_BACKEND=hf` |
| `ENABLE_GIT_PUSH` | `true` | push to remote git repo (off = data stays in container) |
| `ENABLE_HF_PUSH` | `true` | scan & upload large files to LFS |

## Backup mechanism

- On boot: pull → align → migrate data dirs to symlinks → restore LFS files
- Runtime: every 3 minutes (`SYNC_INTERVAL`) `git pull --rebase` → commit → push
- Large files (default > 60MB, `LFS_THRESHOLD`) go through LFS: GitHub Release assets or HF LFS (git-lfs batch protocol)
- Default targets (`SYNC_TARGETS`):
  - `home/user/AstrBot/data/`, `home/user/config/`
  - `app/napcat/config/`, `app/.config/QQ/`
  - `home/user/nginx/admin_config.json`, `home/user/filebrowser-data/filebrowser.db`

## Layout

```
├── Dockerfile                  # builds all deps & runtime
├── config.env                  # runtime toggles (NapCat / Git push / LFS push)
├── supervisor/supervisord.conf # nginx, Xvfb, sync, NapCat, AstrBot
├── nginx/                      # OpenResty dynamic routing + admin API
├── scripts/                    # service launchers
├── sync/                       # backup daemon (Git + LFS, GitHub / HF backends)
│   └── web/                    # sync status Web UI
└── docs/add-process.md         # how to add new services
```

## Router admin API

```bash
curl -H "X-Admin-Password: admin" https://<host>/admin/routes.json
curl -X POST -H "X-Admin-Password: admin" -H "Content-Type: application/json" \
  -d '{"default_backend":"http://127.0.0.1:6185","rules":[...]}' \
  https://<host>/admin/routes.json
curl -X POST -H "X-Admin-Password: <old>" -H "Content-Type: application/json" \
  -d '{"new_password":"<new>"}' https://<host>/admin/password
```

## Troubleshooting

- **502 / blank page**: check `/admin/ui/`, ensure default backend is `http://127.0.0.1:6185`
- **NapCat exits immediately**: `--no-sandbox` is already set for non-root; try `NAPCAT_FLAGS=--disable-gpu`
- **Slow HF build**: apt mirror defaults to `mirror.netcologne.de`; override with `APT_MIRROR`
- **Empty backup repo**: confirm `HF_REPO` + `HF_TOKEN` (or `GITHUB_REPO` + `GITHUB_PAT`) are set; only files above `LFS_THRESHOLD` go through LFS

## Credits

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- [NapCat](https://github.com/NapNeko/NapCatAppImageBuild)

## License

[MIT](./LICENSE). Upstream projects keep their own licenses.