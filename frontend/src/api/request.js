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
    showFullScreenLoading();
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
      return Promise.reject(new Error(error?.response?.data?.msg || "网络异常，请检查后端服务是否启动"));
    },
  );

  return instance(config);
}
