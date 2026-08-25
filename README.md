# ZebaoScreen

泽宝实体屏幕程序的独立源码仓库。当前基线来自 1 号机正在运行的屏幕服务，负责：

- 800×480 眼睛与表情页面；
- 对话 Streaming、Final 与链路状态覆盖层；
- 说话、动作和情绪状态同步；
- 局域网页面嵌入；
- 与 Reachy Mini daemon 的只读状态同步。

屏幕程序与 `ZebaoApp` 分开发布。这里不包含语音模型、机器人动作实现、密钥、运行日志或设备备份。

## 目录

```text
src/server.py                 FastAPI 屏幕服务
src/eye_scripts.py            表情脚本
src/intent_overlay.js         状态覆盖层
src/pages/eyes_live2d.html    当前正式屏幕页面
deploy/systemd/               树莓派服务样例
deploy/labwc/                 Chromium kiosk 启动样例
```

## 本地运行

需要 Python 3.11 或更新版本：

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
cd src
..\.venv\Scripts\python server.py
```

打开 `http://127.0.0.1:8080/`。若没有 daemon，眼睛页面仍可独立预览；状态同步会保持断开。

可配置环境变量：

- `DAEMON_HOST`：daemon 地址，实体机默认 `127.0.0.1`。
- `DAEMON_PORT`：daemon 端口，默认 `8000`。

## 设备发布边界

1. 从已提交版本构建或复制，不直接在设备上长期改源码。
2. 发布前备份设备当前生效目录和 systemd unit。
3. 先检查 `/api/mood` 和主页，再重启 kiosk。
4. 屏幕发布不得修改麦克风、语音链路、舵机参数或 daemon。
5. 仓库不保存 `.bak`、`__pycache__`、日志和设备隐私数据。

当前导入基线对应设备路径 `/home/roger/eyes_server`，部署样例保留该路径以便无行为差异地接管现有设备。后续如改为标准安装目录，应通过单独迁移版本完成。

