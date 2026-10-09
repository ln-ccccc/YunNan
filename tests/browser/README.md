# 可见浏览器验收

`liquid-glass-smoke.cjs` 验证 Miner；`geoview-glass-smoke.cjs` 验证 GeoView。使用可见 Chromium、真实鼠标/键盘及下载，生成截图、视频、trace 和结果 JSON。HTTP fixture 仅在该浏览器上下文拦截；不登录真实账号，不写入真实项目，不调用实际推理。

前提：启动对应前端或本地生产构建预览，具备独立 Playwright 安装及其 Chromium。GeoView 预览使用项目既有 `VUE_APP_BACKEND_PORT` 环境配置，否则 API URL 不合法。测试提供鉴权和业务 HTTP fixture；不能把这些页面测通当作真实后端/GPU测通。

从仓库根目录运行（PowerShell；路径换成自己的独立安装/输出目录）：

```powershell
$env:PLAYWRIGHT_PACKAGE='D:/Temp/YunNan-visual-audit-20261009/node_modules/playwright'
$env:PLAYWRIGHT_BROWSERS_PATH='D:/Temp/YunNan-visual-audit-20261009/browsers'
$env:GUI_AUDIT_OUTPUT='D:/Temp/YunNan-browser-results'
$env:MINER_UI_URL='http://127.0.0.1:4501'
$env:GEOVIEW_UI_URL='http://127.0.0.1:4502'
node tests/browser/liquid-glass-smoke.cjs
node tests/browser/geoview-glass-smoke.cjs
```

没有新增仓库依赖。可用 `AUDIT_BROWSER_EXECUTABLE` 指定测试 Chromium。脚本总在 finally 关闭本次浏览器，不启动服务或系统安装。

Miner `GUI_NAVIGATION_ONLY=1` 仅做相同 fixture/1440×1000/42 次导航的性能采样，关闭录像和 trace，以免截图编码改变测量；保留 rAF 帧间隔、longtask、页面可见性和 CDP 任务/布局/脚本时长。可切换 `MINER_UI_URL` 对比两个生产构建。`GUI_DISABLE_BLUR=1` 仅在采样浏览器临时关闭模糊，`GUI_REDUCED_MOTION=1` 使用减少动态效果，用于诊断，不修改项目样式。完整验收前清除这些诊断变量。

GeoView 分别记录首次切换和已加载页面切换。脚本发现 dirty 编辑重新加载直接丢弃时写入 `knownBugs`；这是存量缺陷证据，不表示数据保护已通过。所有未知 API 返回 404 并使验收失败。

必须结合截图/录像判断渲染与过渡；本机采样不代表客户设备、浏览器组合或真实影像规模。
