# 忆谈

## 公网发布进度（2026-11-12）

公网地址：https://yitan-web-341879-6-1525683845.sh.run.tcloudbase.com 。使用已有 CloudBase 体验环境，两个服务均为 0.25 核、0.5 GiB、0～1 实例。账户采用账号密码和一次性邀请码注册；每个账户拥有独立家庭空间。管理员页面为 `/admin`，由后端检查管理员角色。首次访问平台测试域名时，等待倒计时结束并点击“确定访问”。

初始管理员登录资料保存在本机 `.intermediate/private/忆谈登录资料.txt`，权限 0600；账号密码不进入仓库。管理员可以在“账户邀请”创建一次性邀请码，家属注册后建立自己的家庭。iPhone 与 Android 使用公网 HTTPS，无需安装本机证书。

账户、运营页面及埋点说明见 [运营监控与埋点方案](docs/运营监控与埋点方案.md)。已完成本机真实 HTTP 和浏览器验证 36 项，覆盖注册、密码登录、邀请码复用拒绝、家庭隔离、管理员隔离、密码恢复、旧会话撤销、埋点去重与重启恢复；320、360、375、390、430px 后台页面及九项公网浏览器检查通过。云端注册与密码恢复事务验证六项通过，33 张忆谈表启用 RLS，匿名角色无读取权限。

验证命令（项目根目录执行，前端需先完成生产构建）：

```bash
TMPDIR="$PWD/.intermediate/tmp" backend/.venv/bin/python scripts/check_accounts_live.py
TMPDIR="$PWD/.intermediate/tmp" backend/.venv/bin/python scripts/check_cloud_accounts.py
```

第二个命令使用本机云端配置，在忆谈专用数据表中创建真实验证账户，完成后只清理本次创建的记录，不调用写作或语音模型。验证结果保存在已忽略的 `.intermediate/`。云端模式全部业务通过 HTTPS 数据接口和数据库事务函数保存，不创建容器 SQLite。实时音频分片加密保存；生成任务有数据库领取权限、心跳、检查点和按需定时处理。未知结果的收费请求会停止自动重复执行。

历史迁移使用 `scripts/migrate_cloud_book.py` 和 `scripts/migrate_cloud_files.py`，只处理已核实的李奶奶资料；不会调用写作或转写模型。正文迁移事务保留完整来源结构，文件按 8 MiB 分块加密保存于私有 `yitan-private` 存储；逐个下载解密并核对 SHA-256，匿名读取必须被拒绝。迁移进度保存在忽略目录 `data/migration/li/manifest.json`，只有正文及全部文件核对通过才标记 `uploaded=true`。本机原件始终保留。

2026-11-12 已完成上述私有迁移：7 章、5366 字、完整原文和逐段引用一致；4 个源文件共 79,820,291 字节下载校验通过。本机原件保留，完整正文与原声仅家庭授权可读。

2026-11-12 公网真实验证已通过录音、听悟与 Plus 完整流程；用户原声、豆包原声、统一时间轴录音均逐字节核对一致。安全升级后 45 个依赖包扫描为零漏洞；本机账户 36 项、云端业务 32 项、录音范围读取 10 项回归通过。浏览器实际验证了完整私有成书、公开 379 字节选、管理面板、主阅读入口统计及 320/360/375/390/430px。重新发布前后，成稿、完整转写、合成录音 SHA-256 一致。实体手机听感验收单独记录，不能由浏览器自动检查代替。

公开样书为 `frontend/content/public-samples.json` 中已获准展示的少量原文节选。家庭完整正文与原始录音通过后端权限读取。原始 M4A 和新录音支持 Range 与 HEAD，读取时逐段校验解密，可在手机上拖动播放。

发布及运行方法见 [公网部署操作](docs/公网部署操作.md)，转化和留存口径见 [运营监控与埋点方案](docs/运营监控与埋点方案.md)。

面向老人和家人的手机回忆录产品，支持语音访谈、原声保存、章节整理、照片和家人授权阅读。

- `backend/`：FastAPI、SQLite、加密文件保存、豆包语音、通义听悟转写、千问 Plus 写作。
- `frontend/`：Next.js 手机界面、品牌与首次引导、个人回忆录、家庭阅读和语音采集。
- `scripts/`：手机 HTTPS 启动及产品验证脚本。

## 本机使用

2026-11-10，当前手机界面阶段已验收通过，本机交付收尾完成。独立项目位于 `/Users/ethan/Documents/yitan`，本机已配置依赖、数据及手机证书。

打开终端，执行以下命令，同时启动后端、电脑访问、手机 HTTPS 和公开证书下载服务：

```bash
cd /Users/ethan/Documents/yitan
python3.12 scripts/start-local.py
```

保持终端运行，同一 Wi-Fi 下用手机打开终端显示的 HTTPS 地址。按 Control+C 停止全部服务，再次执行相同命令即可恢复。电脑休眠或关闭服务期间无法访问；Wi-Fi 地址变化后重新启动即可更新服务器证书。首次安装的手机仍需安装并信任根证书。

启动前会检查依赖、前端构建及四个服务端口。服务异常退出时会停止其他服务，日志保存在 `.intermediate/runtime/`。已运行服务时再次启动会提示端口占用。

个人资料位于 `data/`，解密配置位于 `backend/.env`，手机证书位于 `.intermediate/tls/`。备份时先停止服务，把这三个位置一起复制到受保护的备份目录；恢复时保留相同加密密钥。原工作目录中的资料副本继续保留，日常使用独立目录。

产品目前通过本机和同一 Wi-Fi 使用。语音、转写、写作继续按各服务实际调用计费；本次本机交付检查未调用收费模型。线上部署另行安排。

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

产品中的六页引导位于 `/welcome`，成书入口位于 `/welcome?step=3`，真实回忆录书架位于 `/books`，可进入独立书封、目录与分页阅读。个人回忆录页面通过“翻开我的回忆录”进入相同的阅读版式，原有原话核对、编辑、录音及家人访问功能保留。阅读支持大字模式与手机安全区域。

2026-11-11，Figma 第二版原始首页与连续卷页已核对，35 个产品页面和状态已完成设计。整套设计与连续卷页尚未全部接入产品。设计工具、页面映射和构建方法保存在 `tools/figma/`。

展示用成稿单独保存在本机 `data/showcase-books.json`，只包含选定展示的书名、讲述者、章节及正文。此文件与 `data/` 一起备份，不提交仓库；恢复运行环境时需要同时恢复。`/books` 不扫描用户数据库，也不提供任意文件访问。私人回忆录继续通过原有登录和归属验证读取。

手机语音需要 HTTPS：安装 mkcert 后，在仓库根目录运行 `bash scripts/start-phone.sh`，使用它输出的同一 Wi-Fi 地址。公开证书下载服务通过 `node scripts/serve-phone-ca.mjs` 启动；iPhone 安装根证书后须在系统设置中开启信任。证书私钥仅存于本机。

## 验证

在 `frontend/` 运行 `npm run typecheck`、`npm run lint`、`npm test` 和 `npm run build`。在 `backend/` 运行 `.venv/bin/python -m pytest -q`。将 `TMPDIR` 设置为仓库中专用的忽略目录 `.intermediate/`。

分页阅读检查使用本机 Chrome 和真实展示资料。在 `frontend/` 完成生产构建后运行 `npm run test:browser`，它自动在 3012 端口启动独立验证服务，结束后关闭。检查覆盖 320、390、430px 宽度、九章原文完整性、最后一个字可见、目录跳转、大字模式与无效书名 404；结果保存在 `.intermediate/book-tests/`。`npm test` 运行 Vitest 单元检查。

写作测试需要本机的真实语料 `data/real-interviews/li-01-tingwu.txt`；此文件属于私人测试资料，不随仓库上传。当前电脑的独立项目目录已保留该测试资料。

运行服务后，`frontend/check-onboarding.mjs` 验证手机引导及登录流程；`check-mobile.mjs` 验证真实回忆录页面。它们需要本机已有真实验证资料。保存检查 `check-onboarding-save.mjs` 使用隔离后端 8013 与前端 3013，启动时同时设置隔离的 `DATA_DIR` 和 `DATABASE_URL`。

`smoke*.py`、`check_voice_stream.py` 和 `check_live_transcription.py` 等真实模型检查会产生对应服务费用。密钥、数据库、录音、照片、证书和运行证据均保存在本机忽略目录中。
