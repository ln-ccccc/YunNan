# 可见浏览器验收

`liquid-glass-smoke.cjs` 验证 Miner；`geoview-glass-smoke.cjs` 验证 GeoView；`geoview-editor-save-race-smoke.cjs` 回归分类编辑器保存竞态（2026-10-31 审计 P1：保存挂起期间改类入口冻结、绘图新修改被保留并可再次保存、绘制中未闭合手势由快照比对兜底）；`geoview-editor-tile-zoom-smoke.cjs` 回归编辑器底图 zoom 收敛（2026-10-31 审计 P2：manifest min_zoom/max_zoom 应用于 raster source，放大不越级请求）。使用可见 Chromium、真实鼠标/键盘及下载，生成截图、视频、trace 和结果 JSON。HTTP fixture 仅在该浏览器上下文拦截；不登录真实账号，不写入真实项目，不调用实际推理。

`geoview-editor-vertex-guard-smoke.cjs` 回归分类编辑器的顶点删除防护和几项不依赖后端即可验证的编辑行为（2026-11-24 审计 A1/E 组）：三角面用删除按钮和 Delete 键都必须被拦且弹出看得见的说明；四边形 Shift 选两个顶点被拦、删一个生效、再删被拦；脏状态导出的是草稿；放弃全部修改要确认；无底图提示浮层不拦截地图点击；异类选中时合并置灰；保存 422 时提示序号并定位。2026-11-24 通读绘制库后追加（共 21 组）：绘制中 Backspace / Delete / Ctrl+Z 退回上一个点、空草稿才丢弃；Esc 取消绘制、退出顶点模式并清除选中；未完成绘制时保存与导出草稿被拦截且不发请求；放弃全部修改会连同画到一半的草稿一起丢弃；撤销过的草稿仍可继续绘制；叠在大图斑上的小图斑能被单独点中、相邻顶点只隔约 9px 时点顶点恰好选中它（库的"小面优先命中"与点目标择近都由项目代码补上）；间距宽松时点边上的小圆点新增顶点；键盘说明默认收起、展开不撑高工作区；贴边的小图斑不抢点击；被取代的成果显示告警且仍可保存；后端回越界时画面一结束就提示、回合法时不提示。无头 + 真 GPU，只出截图和结果 JSON，默认连 4402 端口的 GeoView 预览：

```powershell
$env:GEOVIEW_UI_URL='http://127.0.0.1:4502'
node tests/browser/geoview-editor-vertex-guard-smoke.cjs
```

编辑期几何接口的夹具（2026-11-24）：面的合法性（越界 / 自相交）、简化、合并改走后端几何引擎，与保存共用同一个判定函数，前端不再自带几何算法。脚本为成果下的 `geometry/validate`、`geometry/union`、`geometry/simplify` 三个 POST 接口提供固定应答，**夹具里不实现任何几何算法**：validate 按事先知道的结论回答——这个夹具的地图定位在林地大图斑上（四边各留 64px），矿山边界比它大 3 倍，初始视野下整个画布都在界内，脚本里画的又都是简单的三角形 / 矩形 / 锯齿面，所以一律回"合法"；union / simplify 回预先写好的固定几何，不由输入算出。脚本在末尾核对：每次新建面、删顶点、加顶点之后确实发出了校验请求（至少 11 次），请求体只有 `geometries`、每个几何只带 `type` 与 `coordinates`。编辑器对校验请求失败是静默的（保存时后端仍会校验），少了这条核对，"合法删除不应有任何提示"会在校验请求根本没发出去时假通过。

这条回归目前**没有**覆盖三样东西：几何无效（自相交）的提示文案、点"合并选中"、点"消除锯齿"——现有的合并用例只核对按钮的可用与置灰，所以 union / simplify 两条应答在夹具里没有调用方（真实数据上的合并、简化、保存由 `backend/tools/geometry_engine_experiments` 里的隔离沙盒测试覆盖）。给别的编辑器脚本加同样的接口时，`geometry/validate` 必须有应答，否则新画面会落进"未提供 fixture 的接口"。

**越界提示这一条是补出来的教训（2026-11-24）。** 第一版夹具只数"校验请求发出去了"，没验证"提示出出来了"；而当时前端把接口返回的 `{ items }` 对象当成结论数组往下传，调度器发现不是数组就静默丢弃——请求照发、后端照答越界，界面上一条提示都没有，夹具和源码守卫式的单测却全是绿的。是真实数据测试（画面 → 等提示 → 没等到）把它抓出来的。所以第 21 组用 `validateVerdictOverride` 预先放一个越界结论，断言提示文案真的出现、并且撤回后再画一个合法的面不提示；`classificationEditorValidation.test.mjs` 里 `matchVerdicts` 的单测钉住对接方式。**数请求次数不等于验证了行为，涉及界面结果的地方必须断言看得见的结果。**

写这类地图脚本踩过的四个坑（键盘事件只认画布焦点、浮层吞掉地图点击、重叠图斑点击选中的是最上面那个、消息框淡入完成前截图是空的）记在 `docs/testing-playbook.md` 技巧 33。

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

Miner 侧栏 2026-10-26 起收敛为 3 个主入口（项目工作台/矿山地图/解译平台）加"更多工具"折叠组（6 个工具入口）；脚本导航循环只走工具入口（6×6=36 次），展开折叠组按 `aria-expanded` 当前状态操作。`GUI_NAVIGATION_ONLY=1` 仅做相同 fixture/1440×1000/36 次导航的性能采样，关闭录像和 trace，以免截图编码改变测量；保留 rAF 帧间隔、longtask、页面可见性和 CDP 任务/布局/脚本时长。可切换 `MINER_UI_URL` 对比两个生产构建。`GUI_DISABLE_BLUR=1` 仅在采样浏览器临时关闭模糊，`GUI_REDUCED_MOTION=1` 使用减少动态效果，用于诊断，不修改项目样式。完整验收前清除这些诊断变量。

GeoView 分别记录首次切换和已加载页面切换。dirty 编辑点重新加载会双向验证丢弃确认框（取消保留编辑 / 确认丢弃后重载），确认框未出现即验收失败（P1-7 修复后的守护断言）。所有未知 API 返回 404 并使验收失败。

必须结合截图/录像判断渲染与过渡；本机采样不代表客户设备、浏览器组合或真实影像规模。

首帧脚本阻断 JS 下载，再延迟会话响应，分别截图检查脚本加载前、会话等待及就绪画面；旧蓝色启动页必须不存在，背景必须使用平台的浅色。

Miner 完整验收另覆盖 km² 换算、统计图、矿山详情标签连续切换、移动端变化矩阵、趋势 CSV 下载及减少动态效果下的真实按压。可设置 `GUI_PLATFORM_HANDOFF=1` 验收实际点击「解译平台」后浏览器后退：测试 Miner 构建使用既有 `VITE_GEOVIEW_URL=http://127.0.0.1:4502/segmentation`，GeoView 预览使用 `VUE_APP_BACKEND_PORT=9500`、`VUE_APP_MINER_URL=http://127.0.0.1:4501`，只用于隔离预览，不改部署配置。
