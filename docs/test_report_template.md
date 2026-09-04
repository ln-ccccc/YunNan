# 测试报告模板

## 基本信息

- 测试日期：
- 测试人：
- 分支/提交：
- 测试范围：

## 测试结果总览

| 结论 | 数量 |
| --- | ---: |
| 通过 | 0 |
| 失败 | 0 |
| 未执行 | 0 |

## 测试明细

| 测试项 | 命令或操作 | 结果 | 失败原因 | 备注 |
| --- | --- | --- | --- | --- |
| Node 单元测试 | `cd miner && npm test` | 未执行 |  |  |
| Miner API 静态检查 | `cd miner && node --check server.js` | 未执行 |  |  |
| Miner 构建 | `cd miner && npm run build` | 未执行 |  |  |
| Frontend 构建 | `cd frontend && npm run build` | 未执行 |  |  |
| 后端光谱指数测试 | `cd backend && python -m unittest test_spectral_indices.py` | 未执行 |  |  |
| 后端新增功能测试 | `cd backend && python -m unittest test_new_features.py` | 未执行 |  |  |
| KML 上传接口 | `POST /api/kml/upload` | 未执行 |  |  |
| 趋势统计接口 | `GET /api/mines/trend-report?class_name=forest&direction=all` | 未执行 |  |  |
| 推理年份参数 | `POST /api/inference/kml-roi` | 未执行 |  |  |
| 首页 UI 检查 | 浏览器访问 Miner Web | 未执行 |  |  |
| 趋势弹窗 UI 检查 | 打开趋势统计弹窗 | 未执行 |  |  |
| 错误态检查 | 模拟 API 失败 | 未执行 |  |  |

## 未测项与风险

- 未测项：
- 未测原因：
- 潜在风险：
- 建议后续检查：

## 结论

填写本次测试是否满足交付要求，以及是否存在需要阻塞发布的问题。
