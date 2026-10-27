import { createRouter, createWebHistory } from "vue-router";

import { isSessionFresh, legacySession, markSessionFresh } from "@/api/auth";

const Home = () => import("@/views/Home.vue");
const Login = () => import("@/views/Login.vue");
const Segmentation = () => import("@/views/mainfun/Segmentation.vue");
const SpectralIndices = () => import("@/views/mainfun/SpectralIndices.vue");
const ClassificationResultEditor = () => import("@/views/mainfun/ClassificationResultEditor.vue");
const NotFound = () => import("@/views/NotFound.vue");

const routes = [
  {
    path: "/",
    redirect: "/segmentation",
  },
  {
    path: "/login",
    name: "Login",
    component: Login,
  },
  {
    path: "/home",
    name: "Home",
    component: Home,
    children: [
      {
        path: "/segmentation",
        name: "Segmentation",
        component: Segmentation,
      },
      {
        path: "/spectralindices",
        name: "SpectralIndices",
        component: SpectralIndices,
      },
      {
        path: "/classification-results/editor",
        name: "ClassificationResultEditor",
        component: ClassificationResultEditor,
      },
    ],
  },
  {
    path: "/:pathMatch(.*)*",
    name: "notfound",
    component: NotFound,
  },
];

const router = createRouter({
  history: createWebHistory(process.env.BASE_URL),
  routes,
});

router.beforeEach(async (to) => {
  // TTL 内零网络导航：后端忙时 session 探测排队会卡死全部路由跳转
  //（2026-10-26 实测：多次地物分类上传/推理后切光谱指数无反应）；
  // 会话中途失效由 request.js 的 401 拦截器兜底重定向
  if (to.path !== "/login" && isSessionFresh()) {
    return true;
  }
  try {
    const response = await legacySession();
    const authenticated = Boolean(response?.data?.data?.authenticated);
    if (authenticated) {
      markSessionFresh();
    }

    if (to.path === "/login") {
      if (authenticated) {
        return to.query.redirect || "/segmentation";
      }
      return true;
    }

    if (!authenticated) {
      return {
        path: "/login",
        query: { redirect: to.fullPath },
      };
    }

    return true;
  } catch (_) {
    if (to.path === "/login") {
      return true;
    }
    return {
      path: "/login",
      query: { redirect: to.fullPath, reason: "session" },
    };
  }
});

export default router;
