import axios from "axios";

import global from "@/global";
import { hideFullScreenLoading, showFullScreenLoading } from "@/utils/loading";
import { ElMessage } from "element-plus";
import { redirectToLegacyLogin } from "@/utils/authRedirect";

// 错误种类标记（借鉴江西 request.js）：
// auth=登录失效 / backend=业务 code!==0 / http=有响应的 4xx-5xx /
// network=无响应的网络层断链 / aborted=调用方主动取消。
// 调用方（isDisconnectError、轮询断链自愈、上传取消）按 kind 精确分支，不再解析 message 字符串。
function createRequestError(kind, message, cause) {
  const error = new Error(message);
  error.kind = kind;
  if (cause) error.cause = cause;
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

      if (response.data.code === 401) {
        redirectToLegacyLogin("expired");
        return Promise.reject(createRequestError("auth", response.data.msg || "登录已失效"));
      }
      if (response.data.code !== 0) {
        ElMessage.error(response.data.msg);
        return Promise.reject(createRequestError("backend", response.data.msg || "请求失败"));
      }

      return response;
    },
    (error) => {
      hideFullScreenLoading();

      // 主动取消（上传 AbortController）：不弹错误提示，由调用方给出友好文案
      if (isCancelledRequest(error)) {
        return Promise.reject(createRequestError("aborted", "请求已取消", error));
      }
      if (error?.response?.status === 401) {
        redirectToLegacyLogin("expired");
        return Promise.reject(createRequestError("auth", error?.response?.data?.msg || "登录已失效"));
      }
      if (!error?.response) {
        // 网络层断链：任务可能仍在后端执行，调用方按 disconnect 语义区分提示
        const message = "网络异常，请检查后端服务是否启动";
        ElMessage.error(message);
        return Promise.reject(createRequestError("network", message, error));
      }
      // HTTP 层错误（4xx/5xx/超时）：给出提示，大量调用点自行 .catch 处理时也不会重复弹窗
      const message = error?.response?.data?.msg || "网络异常，请检查后端服务是否启动";
      ElMessage.error(message);
      return Promise.reject(createRequestError("http", message, error));
    },
  );

  return instance(config);
}
