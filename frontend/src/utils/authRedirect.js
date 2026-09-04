export function redirectToLegacyLogin(reason = "") {
  if (typeof window === "undefined") return;

  const currentPath = `${window.location.pathname}${window.location.search}`;
  const params = new URLSearchParams();
  if (currentPath && !currentPath.startsWith("/login")) {
    params.set("redirect", currentPath);
  }
  if (reason) {
    params.set("reason", reason);
  }

  const target = params.toString() ? `/login?${params.toString()}` : "/login";
  if (`${window.location.pathname}${window.location.search}` !== target) {
    window.location.assign(target);
  }
}
