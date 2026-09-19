import axios from "axios";

import global from "@/global";
import { hideFullScreenLoading, showFullScreenLoading } from "@/utils/loading";
import { ElMessage } from "element-plus";
import { redirectToLegacyLogin } from "@/utils/authRedirect";

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
        return Promise.reject(new Error(response.data.msg || "登录已失效"));
      }
      if (response.data.code !== 0) {
        ElMessage.error(response.data.msg);
        return Promise.reject(new Error(response.data.msg || "请求失败"));
      }

      return response;
    },
    (error) => {
      hideFullScreenLoading();
      if (error?.response?.status === 401) {
        redirectToLegacyLogin("expired");
        return Promise.reject(new Error(error?.response?.data?.msg || "登录已失效"));
      }
      // HTTP 层错误（4xx/5xx/超时）此前静默 reject，用户点按钮后无任何反馈；
      // 与 code!==0 分支对齐给出提示（大量调用点自行 .catch 处理时也不会重复弹窗）
      const message = error?.response?.data?.msg || "网络异常，请检查后端服务是否启动";
      ElMessage.error(message);
      return Promise.reject(new Error(message));
    },
  );

  return instance(config);
}
