import axios from "axios";

import global from "@/global";
import { hideFullScreenLoading, showFullScreenLoading } from "@/utils/loading";
import { ElMessage } from "element-plus";
import { redirectToLegacyLogin } from "@/utils/authRedirect";

// 错误种类标记（借鉴江西 request.js）：
// auth=登录失效 / backend=业务 code!==0 / http=有响应的 4xx-5xx /
// network=无响应的网络层断链 / aborted=调用方主动取消。
// silent=true 表示请求是静默的（拦截器未弹 toast），调用方 catch 据此判断是否需要自己补提示。
// 调用方（isDisconnectError、轮询断链自愈、上传取消）按 kind 精确分支，不再解析 message 字符串。
function createRequestError(kind, message, cause, silent = false) {
  const error = new Error(message);
  error.kind = kind;
  error.silent = Boolean(silent);
  if (cause) {
    error.cause = cause;
    // 保留原始 axios response：存量调用点（getUploadImg/SpectralIndices 的
    // err?.response?.data?.msg 分支）依赖它读取后端详情
    if (cause.response) error.response = cause.response;
  }
  return error;
}

function isCancelledRequest(error) {
  return Boolean(
    axios.isCancel(error)
    || error?.code === "ERR_CANCELLED"
    || error?.code === "ERR_ABORTED"
    || error?.code === "canceled",
  );
}

export function request(config) {
  const instance = axios.create({
    baseURL: global.BASEURL,
    withCredentials: true,
    // 默认超时（2026-11-05 迁移审查 P1-4，江西 2.2 同款）：防后端 worker 被长请求
    // 占满时非 silent 请求无限 pending 叠加全屏遮罩死锁。长同步端点（analysis 系列 /
    // 大文件上传/分片合并）由调用方显式传 timeout: 0 或长值豁免——axios 的 request
    // 级 timeout 优先于实例默认；豁免场景后端 gunicorn 1800s 兜底。
    timeout: 60000,
  });

  instance.interceptors.request.use((reqConfig) => {
    // 轮询/后台请求可携带 silent 标记，避免每次触发全屏锁死 loading
    if (!reqConfig.silent) {
      showFullScreenLoading();
    }
    // 请求关联 ID（2026-11-05 生产化检查）：后端回显同值并在 5xx 日志携带，
    // 用户报障时给出此 ID 即可跨层检索
    reqConfig.headers["X-Request-Id"] = reqConfig.headers["X-Request-Id"]
      || `fe-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    return reqConfig;
  });

  instance.interceptors.response.use(
    (response) => {
      // silent 轮询/后台请求：失败也不弹 toast——轮询断线自愈期间每秒一条提示会刷屏，
      // 终态/断链由调用方（waitForInferenceJob 等）统一给出结论
      const silent = Boolean(response.config?.silent);
      // 与请求拦截器的 show 对称：silent 请求未计数却 hide 会把并发可见请求的
      // loading 计数提前扣掉，全屏加载提前消失（2026-10-24 审查计数失衡）
      if (!silent) hideFullScreenLoading();

      if (response.data.code === 401) {
        redirectToLegacyLogin("expired");
        return Promise.reject(createRequestError("auth", response.data.msg || "登录已失效"));
      }
      if (response.data.code !== 0) {
        if (!silent) ElMessage.error(response.data.msg);
        return Promise.reject(createRequestError("backend", response.data.msg || "请求失败", null, silent));
      }

      return response;
    },
    (error) => {
      const silent = Boolean(error?.config?.silent);
      // 同上：silent 请求未 show 则不 hide，保持计数对称
      if (!silent) hideFullScreenLoading();

      // 主动取消（上传 AbortController）：不弹错误提示，由调用方给出友好文案
      if (isCancelledRequest(error)) {
        return Promise.reject(createRequestError("aborted", "请求已取消", error));
      }
      if (error?.response?.status === 401) {
        redirectToLegacyLogin("expired");
        return Promise.reject(createRequestError("auth", error?.response?.data?.msg || "登录已失效"));
      }
      if (!error?.response) {
        // 超时（2026-11-05）：与断链分开文案——任务可能仍在后端排队/执行，
        // 提示"服务未启动"会误导排障方向
        const isTimeout = error?.code === "ECONNABORTED"
          || error?.code === "ETIMEDOUT"
          || /timeout of/i.test(error?.message || "");
        if (isTimeout) {
          const message = "请求超时，任务可能仍在后端处理，请稍后查看结果";
          if (!silent) ElMessage.error(message);
          return Promise.reject(createRequestError("network", message, error, silent));
        }
        // 网络层断链：任务可能仍在后端执行，调用方按 disconnect 语义区分提示
        const message = "网络异常，请检查后端服务是否启动";
        if (!silent) ElMessage.error(message);
        return Promise.reject(createRequestError("network", message, error, silent));
      }
      // HTTP 层错误（4xx/5xx/超时）：非 silent 请求给出提示（silent 轮询由调用方统一收口，
      // 避免断线自愈窗口内每秒刷屏）；附后端回显的请求编号便于报障检索
      const requestId = error?.response?.headers?.["x-request-id"];
      const baseMessage = error?.response?.data?.msg || "网络异常，请检查后端服务是否启动";
      const message = requestId ? `${baseMessage}（编号 ${requestId}）` : baseMessage;
      if (!silent) ElMessage.error(message);
      return Promise.reject(createRequestError("http", message, error, silent));
    },
  );

  return instance(config);
}
