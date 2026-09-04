import { createRouter, createWebHistory } from "vue-router";

import { legacySession } from "@/api/auth";

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
  try {
    const response = await legacySession();
    const authenticated = Boolean(response?.data?.data?.authenticated);

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
