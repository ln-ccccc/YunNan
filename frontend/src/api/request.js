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
  });

  instance.interceptors.request.use((reqConfig) => {
    // 轮询/后台请求可携带 silent 标记，避免每次触发全屏锁死 loading
    if (!reqConfig.silent) {
      showFullScreenLoading();
    }
    return reqConfig;
  });

  instance.interceptors.response.use(
    (response) => {
      hideFullScreenLoading();

      // silent 轮询/后台请求：失败也不弹 toast——轮询断线自愈期间每秒一条提示会刷屏，
      // 终态/断链由调用方（waitForInferenceJob 等）统一给出结论
      const silent = Boolean(response.config?.silent);

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
      hideFullScreenLoading();

      // 主动取消（上传 AbortController）：不弹错误提示，由调用方给出友好文案
      if (isCancelledRequest(error)) {
        return Promise.reject(createRequestError("aborted", "请求已取消", error));
      }
      const silent = Boolean(error?.config?.silent);
      if (error?.response?.status === 401) {
        redirectToLegacyLogin("expired");
        return Promise.reject(createRequestError("auth", error?.response?.data?.msg || "登录已失效"));
      }
      if (!error?.response) {
        // 网络层断链：任务可能仍在后端执行，调用方按 disconnect 语义区分提示
        const message = "网络异常，请检查后端服务是否启动";
        if (!silent) ElMessage.error(message);
        return Promise.reject(createRequestError("network", message, error, silent));
      }
      // HTTP 层错误（4xx/5xx/超时）：非 silent 请求给出提示（silent 轮询由调用方统一收口，
      // 避免断线自愈窗口内每秒刷屏）
      const message = error?.response?.data?.msg || "网络异常，请检查后端服务是否启动";
      if (!silent) ElMessage.error(message);
      return Promise.reject(createRequestError("http", message, error, silent));
    },
  );

  return instance(config);
}
