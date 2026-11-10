# 忆谈

面向老人和家人的手机回忆录产品，支持语音访谈、原声保存、章节整理、照片和家人授权阅读。

- `backend/`：FastAPI、SQLite、加密文件保存、豆包语音、通义听悟转写、千问 Plus 写作。
- `frontend/`：Next.js 手机界面、品牌与首次引导、个人回忆录、家庭阅读和语音采集。
- `scripts/`：手机 HTTPS 启动及产品验证脚本。

## 启动后端

需要 Python 3.12。以下命令从仓库根目录开始执行：

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

在本机填写 `backend/.env` 的邀请码、加密密钥与模型配置。使用已有数据时必须保留原加密密钥。然后启动：

```bash
.venv/bin/python -m uvicorn app.main:app_factory --factory --host 127.0.0.1 --port 8011
```

## 启动前端

在另一个终端，从仓库根目录执行：

```bash
cd frontend
npm ci
npm run build
npm start
```

电脑打开 `http://127.0.0.1:3011`。直接查看产品介绍可访问 `/welcome`。后端默认由前端代理至 `127.0.0.1:8011`。

手机语音需要 HTTPS：安装 mkcert 后，在仓库根目录运行 `bash scripts/start-phone.sh`，使用它输出的同一 Wi-Fi 地址。公开证书下载服务通过 `node scripts/serve-phone-ca.mjs` 启动；iPhone 安装根证书后须在系统设置中开启信任。证书私钥仅存于本机。

## 验证

在 `frontend/` 运行 `npm run typecheck`、`npm run lint`、`npm test` 和 `npm run build`。在 `backend/` 运行 `.venv/bin/python -m pytest -q`。将 `TMPDIR` 设置为仓库中专用的忽略目录 `.intermediate/`。

写作测试需要本机的真实语料 `data/real-interviews/li-01-tingwu.txt`；此文件属于私人测试资料，不随仓库上传。当前电脑的独立项目目录已保留该测试资料。

运行服务后，`frontend/check-onboarding.mjs` 验证手机引导及登录流程；`check-mobile.mjs` 验证真实回忆录页面。它们需要本机已有真实验证资料。保存检查 `check-onboarding-save.mjs` 使用隔离后端 8013 与前端 3013，启动时同时设置隔离的 `DATA_DIR` 和 `DATABASE_URL`。

`smoke*.py`、`check_voice_stream.py` 和 `check_live_transcription.py` 等真实模型检查会产生对应服务费用。密钥、数据库、录音、照片、证书和运行证据均保存在本机忽略目录中。
