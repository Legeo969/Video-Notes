# Windows Release 编译热修 v1.2.2

## 修复的问题

Rust 1.96 在 `main.rs` 的退出回调中报告 `E0597`。根因是 `if let` 作为块尾表达式时，`try_lock()` 产生的临时 `Result` 可能晚于 Tauri 状态守卫释放。热修在该语句后增加分号，使临时值提前销毁。

原构建脚本还会在 `npm run tauri build` 失败后继续打印“Build completed”。v1.2.2 对每个原生命令检查 `$LASTEXITCODE`，失败时立即停止；只有真实检测到 MSI/NSIS 文件才报告成功。

## 最快重试

若前一次日志已显示 `Sidecar ready`，不必再次运行耗时的 PyInstaller：

```powershell
cd D:\AiWork\Video-Notes-main
.\scripts\build_windows_release.ps1 -ReuseSidecar
```

也可以只重新编译桌面端：

```powershell
cd D:\AiWork\Video-Notes-main\desktop
npm run tauri build
```

成功后安装包位于 `desktop\src-tauri\target\release\bundle\`。
