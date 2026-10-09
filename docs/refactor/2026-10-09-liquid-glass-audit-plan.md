# 前端材质、动效与功能审查计划

主分支：`codex/liquid-glass-audit-20261009`；基线：`dcdf9bed`。
原工作目录 `backend/test_project_management.py` 的用户修改纳入后端审查，但不覆盖或移入交付分支。

已阅读 AGENTS.md、项目结构与低耦合契约、开发规范和测试手册。
主模块是 Miner Web，联动模块为 GeoView 的视觉层。输入仍为公开 DTO，输出为页面和用户事件；依赖者为现有 BFF/Flask API 与浏览器。HTTP、状态字段、存储格式、部署变量不变。

## 设计

服务矿山生态修复监测人员，保留项目、地图、解译与成果审校的既有任务结构。
采用冰川白 `#f2f6f7`、墨青 `#16383c`、矿山绿 `#246c59`、雾灰 `#61767b`、警示红 `#b43c2f`。
中文使用本机系统字体；标题强化层级，数据使用等宽数字。导航和摘要采用玻璃高光、内边缘和半透明材料，长表格与表单保持清晰底色。
相比整屏 SVG 折射或全面低透明度玻璃，选用有限 CSS 模糊，兼顾离线使用、地图和低性能设备；地图侧栏已有 GPU 伪影记录，不重启其 backdrop-filter。

材质与动效配套：按钮按压约 120ms、导航/状态反馈 180ms、页面和面板约 240ms；动效只传达操作与内容变化，地图不缩放。空间资源面板以 grid 行展开，退出期禁止点击。减少动态效果时关闭位移/展开动画，减少透明度时使用实色；不支持模糊或强制颜色环境有回退。

浏览器验收后补充：Miner 将整页位移收敛为标题入场；侧栏材质独立绘制。手机地图页头、图例、导航字形与 GeoView 内联缩进按实际截图修正；地图工具通过 MapContainer slot 在地图区域定位。无项目时不挂载地图，保留有效项目的 v-show/key 生命周期。性能原始值、绘制开销与未修缺陷见最终审查报告。
布局依据 [Apple Layout](https://developer.apple.com/cn/design/human-interface-guidelines/layout)、[Materials](https://developer.apple.com/design/human-interface-guidelines/materials)、[Motion](https://developer.apple.com/design/human-interface-guidelines/motion)。参考 [CSS/SVG 玻璃演示](https://kube.io/blog/liquid-glass-css-svg/) 的边缘高光；不引入仅 Chromium 支持的折射实现。

## 执行与验证

1. 只读审查 Miner/BFF、GeoView 与后端；隔离数据库/输出，运行现有测试和构建。
2. 浏览器复现原窄屏空白导航与其他失效按钮；仅修复已取证的前端事件和异步门控。
3. App.vue 保留工作台/地图 v-show 生命周期，补充图标与可访问名称；新增 Miner 样式文件、有限面板过渡。GeoView 独立工作树提交后集成。
4. Miner `npm test` / `npm run build`；GeoView 同样验证。低风险样式使用浏览器验收，不新增镜像实现的单测。
5. 可见 Chromium 连续操作七模块、表单、空间面板、快速导航、响应式和减少动态效果，保存截图、动效/帧间隔记录与控制台错误。明确 mock 页面与真实 HTTP/数据库/GPU 验证边界。
6. 汇总代码问题、已修项目、测试统计、未验证范围及截图，交付独立分支，不推送 main 或部署。

## 风险

真实 GIS/GPU 推理依赖运行镜像、模型和影像；单测通过不表示每项功能真实可用。玻璃模糊的性能数据只代表本次参考环境。布局需检验 390/1024/1440px，地图移动端的专业操作能力单独说明。
