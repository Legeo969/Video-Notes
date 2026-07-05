# Video Notes AI v1.2.1 启动热修

## 修复内容

1. 替换原有蓝橙棋盘占位图标，生成 Windows 多分辨率 ICO 和 PNG 图标。
2. 将同步 `setup` 中的 `tokio::spawn` 改为 `tauri::async_runtime::spawn`，避免 release GUI 进程在没有进入 Tokio 上下文时直接 panic、且因隐藏控制台表现为“双击没反应”。
3. 桌面壳启动、引擎路径解析和退出过程写入：
   `%LOCALAPPDATA%\Video Notes AI\logs\desktop-startup.log`。
4. Tauri 壳层构建失败时显示 Windows 原生错误对话框，不再静默退出。
5. Python 引擎失败不会关闭桌面窗口；界面顶部展示错误、日志路径和“重新连接”。
6. release EXE 在源码目录内直接运行且未准备 sidecar 时，可回退到本机 Python + `src.engine`；正式安装包仍应打包 sidecar。
7. 新增 sidecar 准备、Windows release 构建和双击启动诊断脚本。

## 本机验证

```powershell
.\scripts\verify_product.ps1
.\scripts\prepare_tauri_sidecar.ps1
.\scripts\build_windows_release.ps1 -SkipSidecarInstall
```

## 双击仍无窗口

```powershell
.\scripts\diagnose_desktop.ps1
```

脚本会解析桌面快捷方式目标，判断 EXE 是否已经被移动或删除，并输出启动日志。
