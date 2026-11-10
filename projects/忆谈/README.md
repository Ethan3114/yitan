# 忆谈

## 手机首页与首次引导

首次访问 `/` 显示忆谈品牌首页，依次进入回忆卡、书页版式、家人阅读说明、记录对象选择、邀请码登录与建档。`/welcome` 可随时重新查看，已登录用户也可从“我的 → 认识忆谈”进入。公开引导只展示功能与版式说明。

有效登录会话重新打开首页时，会校验本机上次选择的回忆录是否仍在授权列表内，然后进入该书；只有一本时直接进入，多本且没有选择记录时进入家庭选择页。设备偏好仅保存档案 ID，后端继续执行现有账户及家人授权。注销清除该偏好。

建档只要求称呼，其余背景与避谈话题可以展开填写。家属建档后进入交接页，再由老人本人同意录音。书封统一使用浅色纸张、书脊、记忆插画与书签；真实正文、录音、照片及家人展示沿用原有接口。

前端目录执行 `CHECK_URL=http://127.0.0.1:3011 node check-onboarding.mjs`，验证 20 个手机引导页面与真实登录、阅读恢复、交接及录音同意保护。`CHECK_URL=http://127.0.0.1:3011 CHECK_READ_ONLY=1 node check-mobile.mjs` 检查 28 个现有页面。请将 `TMPDIR` 设置为产品的 `.intermediate` 绝对路径。

`check-onboarding-save.mjs` 使用隔离后端 8013 与前端 3013，验证实际建档保存。启动隔离后端时必须同时指定 `DATA_DIR=<产品绝对路径>/.intermediate/onboarding-data` 与 `DATABASE_URL=sqlite:///<产品绝对路径>/.intermediate/onboarding-data/yitan.db`，覆盖本地 `.env` 中的数据库地址；以 `--host 127.0.0.1 --port 8013` 启动 Uvicorn。前端使用 `PORT=3013 BACKEND_URL=http://127.0.0.1:8013 npm start`。确认隔离目录出现 `yitan.db` 后执行此保存检查。

## 手机 HTTPS

新访谈保存双方对话合成录音，按手机实际播放时序保留停顿和打断。访谈记录中“双方对话录音”用于完整收听，展开区域保留独立音轨。历史访谈缺少时间信息时维持原有录音。听悟对新访谈转写合成录音，写作继续使用千问 Plus。

时间轴验证：后端目录运行 `.venv/bin/python ../scripts/check_timeline.py prepare`，前端目录运行 `node check-timeline.mjs`，再在后端运行 `.venv/bin/python ../scripts/check_timeline.py`。脚本需要已存在的真实音频验证数据，产物保存在 `.intermediate/timeline/`。真实浏览器与豆包连接验证使用 `check-live-timeline.mjs` 和隔离服务 8093/3444；`check_live_transcription.py` 校验其保存结果并实际调用听悟，会产生正常模型费用。

后端继续监听 `127.0.0.1:8011`。在产品目录执行 `bash scripts/start-phone.sh` 启动手机 HTTPS（端口 3443，依赖本机 mkcert）；脚本为当前 Wi-Fi IP 生成证书。另一个终端在产品目录执行 `node scripts/serve-phone-ca.mjs`，仅在端口 3080 提供公开根证书。

iPhone 使用 Safari 下载 `http://192.168.120.117:3080/yitan-ca.crt`，在设置中安装描述文件，再到“通用 → 关于本机 → 证书信任设置”开启完全信任。随后访问 `https://192.168.120.117:3443`。IP 变化后重新运行启动脚本。私钥只保存在忽略目录，不能传到手机。

后端目录执行 `.venv/bin/python ../scripts/check_phone_https.py` 严格校验证书链与登录；前端目录执行 `node check-phone.mjs` 检查 HTTPS 页面手机宽度。后者允许浏览器加载本地证书，仅作为页面检查，不能证明实体手机已信任证书。实体麦克风、外放及打断必须实际验证。

手机页面切到后台会结束并保存访谈。需持续使用本机服务时保留证书；结束本地验证后，可在 iPhone 设置中的“通用 → VPN 与设备管理”移除该描述文件。

手机上的家庭回忆录：建立老人档案、保存老照片、连续语音访谈、整理章节、阅读原话与播放录音。数据保存在本机 SQLite，照片和录音加密保存。

## 运行环境

- Python 3.12、Node.js 20.9 以上；当前前端使用 Next.js 16.4.5 与 React 19.3.1。
- 页面按 390px 手机尺寸设计，检查 360、375、390、430px。浏览器要求 Safari 16.4 以上或 Chrome 111 以上。
- 服务配置位于 `backend/.env`，沿用已有邀请码、加密密钥和模型配置。密钥不得提交到仓库。已有数据的加密密钥必须保留。
- 回忆录撰写与审查默认 `WRITING_PROVIDER=qwen`、`QWEN_WRITING_MODEL=qwen3.7-plus`，启用思考模式，使用北京地域 `DASHSCOPE_API_KEY`。`DASHSCOPE_BASE_URL` 默认北京兼容接口。按调用记录保存实际模型、token 用量、结束原因和耗时。事件缓存按用户、模型、原文和提示词区分；已有回忆录保留。

## 启动

在工具包根目录打开终端，分别在两个终端执行：

```bash
cd projects/忆谈/backend
.venv/bin/python -m uvicorn app.main:app_factory --factory --host 127.0.0.1 --port 8011
```

```bash
cd projects/忆谈/frontend
npm ci
npm run build
HOST=0.0.0.0 npm start
```

本机浏览器打开 `http://127.0.0.1:3011`。同一 Wi-Fi 的手机使用电脑的局域网 IP 与端口 3011 查看页面。电脑必须保持运行。

网页、API 和 WebSocket 通过同一个入口访问。后端默认地址 `http://127.0.0.1:8011`，可用 `BACKEND_URL` 修改。开发运行使用 `npm run dev`，一个工程只能运行一个开发服务。

手机录音要求可信 HTTPS。`server.mjs` 支持 `TLS_CERT`、`TLS_KEY` 指向本机证书文件，配合 `HOST=0.0.0.0` 启动；证书放在已忽略的 `.intermediate/`。手机信任证书和实体设备录音检查纳入阶段 3-3。本机 localhost 可以请求麦克风权限。

## 验证

在工具包根目录执行：

```bash
mkdir -p projects/忆谈/.intermediate/verify
TMPDIR="$PWD/projects/忆谈/.intermediate/verify" bash verify.sh 忆谈 阶段3-2
```

后端测试与前端检查：

```bash
cd projects/忆谈/backend
TMPDIR="$PWD/../.intermediate/verify" .venv/bin/python -m pytest -q --basetemp=../.intermediate/pytest-3-1
```

```bash
cd projects/忆谈/frontend
npm run typecheck
npm run lint
npm test
npm run build
npm audit --omit=dev
```

真实业务验证使用隔离副本，避免影响原档案。在后端目录运行 `../scripts/prepare_validation.py` 会复制已有数据库与加密文件到 `.intermediate/validation/`。需要已有真实访谈、章节和照片。用相同密钥启动隔离后端 8012，设置 `DATA_DIR` 和 `DATABASE_URL` 指向副本；前端端口 3012 的 `BACKEND_URL` 指向 8012。禁止让两个开发前端同时运行。

- `scripts/smoke_mobile.py`：真实 DeepSeek 事件整理、写作与审查，HTTP 整理任务、重复提交和结果读取；会在隔离副本内重新整理选中的访谈。
- `scripts/smoke_voice_mobile.py`：把已有真实录音的前 30 秒按原速送入真实豆包，验证同源 WebSocket、回答声音、录音保存和创建幂等。此项与实体麦克风测试分别记录。
- `frontend/check-mobile.mjs`：使用系统 Chrome 检查四种手机宽度与真实页面操作，截图自动保存到阶段证据目录。
- `scripts/check_task_restart.py prepare`：创建真实整理任务；终止并重新启动隔离后端后运行 `check`，验证中断状态、已完成任务、原章节与录音。
- `scripts/check_writing_results.py`：读取选定的真实重写结果，验证事件去向、全书审查、持久化正文与多处原文引用，自动生成阶段 3-2 验证表和模型用量记录。运行位置为后端目录。本次验收使用李奶奶完整转写。

撰写依次处理完整事件、章节规划、逐句证据、章节审查与全文审查。所有章节和段落完整保存。模型输出达到单次长度限制时，事件抽取和章节撰写自动缩小批次继续；其他未完整返回的结果会阻止发布。人物、时间和听写疑点保留在核对事项中。处理记录加密存放在 `data/writing/`，数据库 `writing_runs` 保存任务状态与文件位置；已有版本在替换前进入加密档案。

真实冒烟脚本会调用已配置模型并产生用量，记录由脚本自动写入 `docs/evidence/阶段3-1/冒烟记录.md`。

## 当前交付范围

家人页支持选定章节和照片、预览后确认展示、一次性邀请、独立家人身份、留言和撤销权限。管理者从手机可访问的局域网地址进入家人页，生成邀请后自行复制给家人；双方连接同一 Wi-Fi，电脑保持运行。每份邀请七天有效、一次使用，家人会话三十天有效。原声需单独授权，授权范围为整次访谈。

展示版本独立加密保存，重新生成和历史恢复都不会自动更新家人所读正文。章节阅读页可以查看历史并确认恢复；恢复前保存当前正文，原话、核对记录和留言保留。正文生成记录中的历史版本也可读取。

`scripts/check_family.py` 读取真实李奶奶数据的数据库副本，通过实际接口验证展示、权限、留言、历史与应用恢复。`scripts/smoke_plus.py` 使用李奶奶真实转写中的一段，通过正式 Plus 适配运行抽取、写作和审查，并自动追加阶段 3-2 冒烟记录；会产生模型费用。

浏览器手机宽度验证与两台实体设备验证分别记录。实体手机麦克风、外放回声和浏览器差异在阶段 3-3 验收。
