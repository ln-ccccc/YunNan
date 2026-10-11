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
node tests/browser/geoview-entry-smoke.cjs
```

没有新增仓库依赖。可用 `AUDIT_BROWSER_EXECUTABLE` 指定测试 Chromium。脚本总在 finally 关闭本次浏览器，不启动服务或系统安装。

Miner `GUI_NAVIGATION_ONLY=1` 仅做相同 fixture/1440×1000/42 次导航的性能采样，关闭录像和 trace，以免截图编码改变测量；保留 rAF 帧间隔、longtask、页面可见性和 CDP 任务/布局/脚本时长。可切换 `MINER_UI_URL` 对比两个生产构建。`GUI_DISABLE_BLUR=1` 仅在采样浏览器临时关闭模糊，`GUI_REDUCED_MOTION=1` 使用减少动态效果，用于诊断，不修改项目样式。完整验收前清除这些诊断变量。

GeoView 分别记录首次切换和已加载页面切换。脚本发现 dirty 编辑重新加载直接丢弃时写入 `knownBugs`；验收时必须检查该字段为空，不能仅看退出码。所有未知 API 返回 404 并使验收失败。

必须结合截图/录像判断渲染与过渡；本机采样不代表客户设备、浏览器组合或真实影像规模。

首帧脚本阻断 JS 下载，再延迟会话响应，分别截图检查脚本加载前、会话等待及就绪画面；旧蓝色启动页必须不存在，背景必须使用平台的浅色。

Miner 完整验收另覆盖 km² 换算、统计图、矿山详情标签连续切换、移动端变化矩阵、趋势 CSV 下载及减少动态效果下的真实按压。可设置 `GUI_PLATFORM_HANDOFF=1` 验收实际点击「解译平台」后浏览器后退：测试 Miner 构建使用既有 `VITE_GEOVIEW_URL=http://127.0.0.1:4502/segmentation`，GeoView 预览使用 `VUE_APP_BACKEND_PORT=9500`、`VUE_APP_MINER_URL=http://127.0.0.1:4501`，只用于隔离预览，不改部署配置。

## 真实服务回归

`editor-save-regression.cjs` 验证脏编辑取消重载/离开、真实保存回包延迟期间的编辑锁定、保存后 API 重读和重载。准备独立数据库、存储和会话，分类成果包含地图中心可点选的面及至少两个未使用类别；脚本会生成一个正式修订，仅延迟已由真实服务执行的保存回包。

`real-imagery-regression.cjs` 使用已登记的真实影像与矿山边界，验证 0/100/250/500m 缓冲、带缓冲的零面积范围拒绝、按候选影像 bounds 框选重试及 1024 像素切片。脚本会登记裁剪和切片数据；源影像每边大于 1024、每边不超过 2048，使四片的断言成立。这些回归使用隔离项目，不触发模型前向。

本地私有 `DEEP_AUDIT_CONFIG` JSON 包含 `username`、`password`、`project_id`、`result_id`；真实影像脚本另用 `real_cases: [[项目ID, 矿山FID], ...]`。影像名称须包含 FID，以便点击对应原影像。凭据文件、数据库和可能含会话的 trace 不提交或公开。

沿用上述 Playwright 设置，再配置实际测试地址；每个脚本使用不同输出目录：

```powershell
$env:MINER_UI_URL='http://127.0.0.1:14712'
$env:GEOVIEW_UI_URL='http://127.0.0.1:14713'
$env:BACKEND_URL='http://127.0.0.1:15068'
$env:DEEP_AUDIT_CONFIG='D:/Temp/yunnan-isolated-gui-config.json'
$env:GUI_AUDIT_OUTPUT='D:/Temp/yunnan-editor-regression'
node tests/browser/editor-save-regression.cjs
$env:GUI_AUDIT_OUTPUT='D:/Temp/yunnan-imagery-regression'
node tests/browser/real-imagery-regression.cjs
```

真实服务回归证明会话、API、持久化与浏览器操作；仍需结合截图判断渲染，独立校验影像像素和空间范围，真实模型前向另行验证。
