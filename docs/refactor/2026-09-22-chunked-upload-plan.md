# 分片续传（100GB 级影像上传）方案与执行记录 2026-09-22

## 需求与决策

用户要求把影像上传上限从 8GB 提到 ~100GB。排查结论：8GB 常量只是当时对齐江西验收的工程上限，**真正的卡点是传输链路**——gunicorn worker 超时 120s（`docker/start-backend.sh:29`，单请求超两分钟即被杀，8GB 也只在极快内网才勉强活）、无断点续传（小时级单发 POST 一次抖动全重来）、Werkzeug 先落 `/tmp` 再拷贝的双倍磁盘。经确认走**分片续传**方案。

## 方案

**三端点**（`/api/file/upload/`，复用 file_api 鉴权）：
- `init`：幂等初始化。会话标识=客户端算的 `sha256(文件名:大小:修改时间)`（hex 16-64 校验）；同 key 同文件返回已收分块索引 → **失败重试/页面重开自动续传**；key 冲突（文件变了）400。
- `chunk/<sid>/<idx>`：octet-stream 原始体逐块落 `UPLOADED_PHOTOS_DEST/.chunks/<sid>/chunk_<idx>.bin`（流式读、超预期字节即拒、`X-Chunk-Sha256` 可选逐块校验、重传覆盖幂等）。
- `complete/<sid>`：逐块尺寸校验 → **同卷就地合并**（临时文件+os.replace，避免"分块+组装+终稿"三倍磁盘）→ 走 `upload_one_from_path`（与单发通道同源的校验/切片/photo 记录/响应形状，零契约漂移）→ 清理暂存。

**上限体系**：单发 multipart 通道保持 8GB（MAX_CONTENT_LENGTH 不动，避开长请求超时）；分片通道 `UPLOAD_SESSION_MAX_TOTAL_MB=102400`（100GB）；分块 1MB~512MB（默认 64MB——每块秒级完成天然避开 gunicorn 超时，弱链路 1MB/s 也在 120s 内）。

**前端**：`uploadChunking.mjs` 纯函数（阈值 512MB/分块规划/续传字节聚合）+ `api/upload.js` 编排（computeUploadKey→init→跳过已收→逐块 sha256→complete，返回与 createSrc 同构响应）+ `getUploadImg` 分流（任一文件超阈值整批走分片，通知标题带"分片续传"，进度按总字节聚合；小文件批次保持单发零回归）；守卫上限 100GB 同步。

**运维**：gunicorn 超时无需动（分块秒级）；complete 的 100GB 合并是纯磁盘 IO（同卷 rename 级别除外，顺序写 ~4-8 分钟@400MB/s），如实测命中 worker 超时，设 `WEB_TIMEOUT_SECONDS=1800` 即可（env 已支持）。每次 init 顺带清扫 >7 天陈旧会话。

## 执行结果

| 门 | 结果 |
| --- | --- |
| 后端单测（test_file_upload_chunks.py，12 例） | 生命周期/幂等续传/尺寸与 sha 校验/缺块/上限/鉴权/键冲突/非 tif/常量对齐 全绿 |
| 后端容器全量 | **382 passed / 0 failed / 10 skipped / 149 subtests**（基线 370 + 12） |
| 前端 | **42/42**（+5 分块规划；守卫断言 8GB→100GB）+ build 零错误 |
| curl E2E | 2.5MB 真 tif 3 块（含一次故意超尺寸被拒）→ 幂等 init received=[0,1] → complete 快速路径（原文件名 + raw_tiff_path 保留），与单发契约同构 |
| GUI E2E（真实栈） | **572MB 合法影像**（超 512MB 阈值强制走分片）→ init+9×64MB 块+complete → 推理任务衔接 → 取消（DB cancelled+cancel_requested=1）→ **600,060,974 字节原 tif 完整落盘**、暂存清理、零 JS 错误、通知正常关闭 |

## 排障记录（工具链坑，与代码无关）

E2E 首两次异常均为测试工具链问题：①git-bash GBK 控制台下 `curl -d` 中文 JSON 按 GBK 字节发出，Flask `get_json(silent=True)` 静默解析失败回退空字典，三个开关全落默认 False（表象：keepRawTiff"不生效"，实为传参未达）；②`dd bs=N skip=1` 的 skip 按 bs 计数，切错偏移；③Windows Python 的 `/tmp` 与 git-bash 的 `/tmp` 解析到不同位置。已沉淀 testing-playbook 技巧 20。

## 遗留

- 100GB 满量级实传未做（本机无此数据；572MB 已覆盖全部代码路径，块数差异仅是循环次数）。首次真传 100GB 时留意 complete 合并耗时与 worker 超时（见上"运维"）。
- 上传端点不校验 tif 魔数的既有观察项不变（分片通道同样放行任意字节，坏文件由推理端拒绝）。
