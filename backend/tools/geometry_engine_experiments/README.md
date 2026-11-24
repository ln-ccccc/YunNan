# 几何引擎实验与定稿回归脚本

2026-11-24 统一几何引擎这一轮用来做技术选型和验收的脚本。它们不是产品代码，不参与服务运行；
放在仓库里是为了让 [对照实验报告](../../../docs/geometry-engine-simplify-ablation.md) 和
[定稿回归报告](../../../docs/geometry-engine-regression-20261124.md) 里的每个数字都能被复现。

## 共同约定

- **生产只读。** 唯一接触生产库的脚本是 `dump_production.py`（只读查询，输出到标准输出）。
  其余脚本都只读这份导出，在一次性容器里运行，不连库。
- **被测源码钉在 commit 上。** `run_final_regression.sh <commit>` 用 `git archive` 把该 commit 的 `backend/`
  导出到仓库外，再只读挂载进容器，所以回归结果与 commit 一一对应，不受工作区改动影响。
- **运行时同生产。** 镜像 `yunnan-runtime:current`，Shapely 1.8.5.post1 / GEOS 3.10.3。
  `shapely2_experiment.py` 例外：它在一次性容器里把 Shapely 2.1 临时装到容器内的目录，不碰镜像和生产依赖。
- **数据与输出放仓库外。** 默认 `D:/YunNan-Handoff/zcode-20261124-geometry`，可用环境变量 `ZC_GEOM_WORK` 改。

## 脚本

| 脚本 | 做什么 |
| --- | --- |
| `dump_production.py` | 只读导出全部 ready 成果：ROI、每个图斑的几何、label 像元尺寸。NDJSON，一行一条成果 |
| `final_regression.py` | **定稿回归**：判定对账（引擎 vs 改造前表达式、前端旧判定）、coverage 普查、简化结果与分类统计、不依赖引擎代码的交叉验证、耗时、GEOS 不确定性度量 |
| `run_final_regression.sh` | 定稿回归的运行器：导出 commit、挂载、记录 commit / 文件哈希 / 依赖版本 / 数据指纹 |
| `final_overlay_measure.py` | 被接受的简化结果用叠加运算算出的 ROI 外面积有多大（谓词与叠加是 GEOS 的两条路径） |
| `ablation_simplify.py` | **对照实验**主体：A 已部署 / B 原生简化 / C 原生+校验+重试 / D 边界保护，按图斑类别打标并逐个计量 |
| `ablation_summarize.py` | 把对照结果汇总成分类统计表 |
| `ablation_followup.py` | 补充分析：稳健的贴边口径、面积变化按图斑大小分档、C 与 D 的配对压缩 |
| `ablation_detachment.py` | 抽样量「图斑从矿山边界上拉开」的距离分布 |
| `covers_timing.py` | 普通 `covers` 与 prepared `covers` 的单次耗时；原始图斑上两者是否有分歧 |
| `debug_tps_determinism3.py` | GEOS 3.10.3 拓扑保持简化是否确定：同一输入用新建对象连算多次，统计不同输出 |
| `shapely2_experiment.py` | 独立实验：Shapely 2.1 / GEOS 3.13 的 `coverage_simplify` 三种用法与向量化批量计算 |
| `shapely2_coverage_diagnose.py` | 诊断 GEOS 为什么把多数生产成果判为「不是有效 coverage」 |
| `browser/before-verdicts.mjs`、`browser/a-timing.mjs` | 改造前前端判定（采样式检查、`booleanWithin`）与已部署简化流程的离线复算、逐图斑计时 |
| `browser/vector-edit-real.cjs` | 真实数据浏览器测试基线（真实鼠标键盘、真实保存） |
| `browser/make_real_test.py` | 由基线生成指向隔离沙盒栈的版本，并把「消除锯齿」一步改为直接询问后端简化接口 |
| `browser/debug-validate-outside.cjs` | 最小探针：画面 → 校验接口回越界 → 看提示是否真的出现 |
| `zc_sandbox_up.sh` / `zc_sandbox_down.sh` | 起停隔离沙盒栈：临时 MySQL（只复制需要的表）+ 定稿 commit 的隔离后端。不碰生产容器与生产库 |
| `engine-D-variant.py` | **仅作参考，不被任何代码引用。** 对照实验里被否决的 D 方案快照（104 行自定义几何逻辑） |

## 复现顺序

```bash
WORK=D:/YunNan-Handoff/zcode-20261124-geometry        # 仓库外
# 1. 只读导出（在生产后端容器里执行，输出重定向到文件）
MSYS_NO_PATHCONV=1 docker exec -i -w /app/backend yunnan-backend \
  /opt/conda/envs/MMSeg310/bin/python - < dump_production.py > $WORK/production-results.ndjson
# 2. 改造前前端的判定与计时（必须在删除旧前端几何模块之前的代码上跑；本轮的结果已记录在报告里）
node browser/before-verdicts.mjs $WORK/production-results.ndjson $WORK/before-verdicts.ndjson
node browser/a-timing.mjs $WORK/production-results.ndjson $WORK/a-timing.ndjson
# 3. 定稿回归（绑定 commit）
bash run_final_regression.sh <commit> 3
# 4. 对照实验（在被测的工作区上运行；本轮用的是 D 方案还在时的引擎）
MSYS_NO_PATHCONV=1 docker run --rm --network none --cpus 3 --memory 5g --workdir /app/backend \
  --entrypoint /opt/conda/envs/MMSeg310/bin/python -e PYTHONUTF8=1 \
  -v "D:/项目/YunNan:/app:ro" -v "$WORK:/work" yunnan-runtime:current \
  /work/ablation_simplify.py /work/production-results.ndjson /work/before-verdicts.ndjson /work/out/ablation 3
```

容器内路径固定为 `/app/backend`（源码）与 `/work`（数据与输出）。脚本里的 `/work/...` 参数按此填写；
`run_final_regression.sh` 已经处理好了宿主路径与容器路径的对应。
