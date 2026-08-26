# 变更记录

## 2026-08-26

- 新增屏幕覆盖层协议：下方 ASR/门控窗口由屏幕服务单一持有状态，可通过受限的本地接口显示或隐藏；WebSocket 与轮询恢复时都会同步该选择，不开放任意 DOM 控制。
- 将 1 号机当前生效的屏幕服务、眼睛页面、表情脚本和 kiosk 部署配置导入独立 `ZebaoScreen` 仓库。
- 排除设备备份、缓存、日志与运行数据，建立后续独立开发和发布基线。

## 2026-06-23

- 在树莓派 `/home/roger/eyes_server/pages/eyes_live2d.html` 限制自动眨眼：只有 `default/normal` 稳态允许自动眨眼，其他表情和表情过渡期间固定眼高，避免眨眼压缩叠加到 morph 过渡导致显示异常。
- 同步补充同类眼睛形态的稳定绘制分支，避免 `arc`、`squeeze`、`thinking` 稳态落入通用兜底画法。
- 已备份改动前版本到 `/home/roger/app_backups/20260623_181033_before_blink_normal_only`，并用 Chromium headless 加载 `/tmp/eyes_live2d_blink_normal_only_test.html` 验证新页面可解析。

- 在树莓派 `/home/roger/eyes_server/pages/eyes_live2d.html` 改造表情过渡：取消上一表情和目标表情的透明交叉渐变，改为眼睛尺寸、眼皮形态、瞳孔参数和嘴巴轮廓点的形状插值；非默认表情之间切换时先经由默认表情再过渡到目标表情。
- 已按要求保留改造前版本，备份目录为 `/home/roger/app_backups/20260623_175751_before_eye_morph_transition`。
- 已用 Chromium headless 加载 `/tmp/eyes_live2d_morph_test.html` 验证新页面可解析，未触碰麦克风、音量、VAD、ASR/LLM/TTS、声音、舵机基线、动作参数和 7861 展厅对话应用。

- 在树莓派 `/home/roger/eyes_server/pages/eyes_live2d.html` 增强隐藏鼠标逻辑：页面加载、鼠标移动、webview 切换和 iframe 加载后都会重新应用透明光标。
- 在树莓派 `/home/roger/.config/labwc/environment` 增加 `XCURSOR_PATH`、`XCURSOR_THEME=zebao-invisible`、`XCURSOR_SIZE=1`，让 labwc 图形会话下次启动时使用透明光标主题。
- 已备份原文件到 `/home/roger/app_backups/20260623_173537_before_hide_cursor_labwc`。
- 已验证 `eyes-server.service` 仍为 active，`http://127.0.0.1:8080/api/mood` 返回 200，页面包含 `HIDDEN_CURSOR` 和 `hideCursor`，Chromium kiosk 进程包含 `--hide-cursor`、`--ash-hide-cursor`、`--kiosk`、`--ozone-platform=wayland`。
- 未触碰麦克风、音量、VAD、ASR/LLM/TTS、声音、舵机基线、动作参数和 7861 展厅对话应用。
- 说明：本机未找到 `C:\project_lyq\Robot` 本地源仓库，本次为树莓派现场修复，并已把修改副本保存在当前 Codex 工作区 `work/zebao_eyes/`。
