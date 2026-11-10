# 忆谈

为老人和家人记录人生故事的手机产品。通过语音访谈留下原声，整理为可阅读的回忆录，并按授权与家人分享。

产品代码、启动说明和项目文档位于 [projects/忆谈](projects/忆谈/README.md)。根目录保留阶段管理与验证工具，产品目录中包含后端、前端、验证脚本和设计资料。

## 启动

后端在 `projects/忆谈/backend` 安装 `requirements.txt` 中的依赖，参考 `.env.example` 配置本机环境变量，然后运行：

```bash
.venv/bin/python -m uvicorn app.main:app_factory --factory --host 127.0.0.1 --port 8011
```

前端在 `projects/忆谈/frontend` 运行：

```bash
npm ci
npm run build
npm start
```

手机 HTTPS、首次引导、模型配置及验证方法见产品 README。密钥、证书私钥、数据库、录音、照片及含真实用户内容的验收截图保存在本机。

## 当前状态

手机品牌首页、引导、个人回忆录、家人阅读、双方音频合成及转写链路已实现。当前阶段为 3-3；真实模型冒烟记录早于最近前端更新，实体设备听感与双设备验证仍待完成。
