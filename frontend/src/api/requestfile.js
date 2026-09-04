import axios from "axios";

import global from "@/global";
import { ElMessage } from "element-plus";
import { hideFullScreenLoading } from "@/utils/loading";
import { redirectToLegacyLogin } from "@/utils/authRedirect";

export function requestfile(config) {
  const instance = axios.create({
    baseURL: global.BASEURL,
    withCredentials: true,
    transformRequest: [function transformRequest(data, headers) {
      delete headers.post["Content-Type"];
      return data;
    }],
  });

  instance.interceptors.request.use(
    (reqConfig) => {
      reqConfig.headers.Accept = "multipart/form-data";
      reqConfig.headers["Content-Type"] = "multipart/form-data";
      return reqConfig;
    },
    (error) => Promise.reject(error),
  );

  instance.interceptors.response.use(
    (response) => {
      if (response.data.code === 401) {
        redirectToLegacyLogin("expired");
        return Promise.reject(new Error(response.data.msg || "登录已失效"));
      }
      if (response.data.code !== 0) {
        hideFullScreenLoading("#load");
        ElMessage.error(response.data.msg);
        return Promise.reject(new Error(response.data.msg || "请求失败"));
      }
      return response;
    },
    (error) => {
      if (error?.response?.status === 401) {
        redirectToLegacyLogin("expired");
        return Promise.reject(new Error(error?.response?.data?.msg || "登录已失效"));
      }
      return Promise.reject(error);
    },
  );

  return instance(config);
}
