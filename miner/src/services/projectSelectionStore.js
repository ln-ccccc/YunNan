// 跨模块共享的「当前项目」上下文（gui-audit #5）。
// 项目工作台选中项目后写入；影像/解译/编辑/数据四个模块页载入项目清单后读取并自动回选，
// 消除"工作台选了项目、其他模块全忘"的断裂。纯 JS 单例、无 Vue 依赖，便于 node --test。
const STORAGE_KEY = 'miner.selected-project-id';

function normalize(value) {
  const num = Number(value);
  return Number.isSafeInteger(num) && num > 0 ? num : null;
}

function readInitial() {
  try {
    return normalize(globalThis.window?.localStorage?.getItem(STORAGE_KEY));
  } catch {
    return null; // localStorage 不可用（隐私模式等）时退化为仅内存态
  }
}

let currentId = readInitial();
const listeners = new Set();

export function getSelectedProjectId() {
  return currentId;
}

export function setSelectedProjectId(value) {
  try {
    installStorageBridge();
  } catch {
    // 忽略
  }
  const next = normalize(value);
  if (next === currentId) return;
  currentId = next;
  try {
    const storage = globalThis.window?.localStorage;
    if (next === null) storage?.removeItem(STORAGE_KEY);
    else storage?.setItem(STORAGE_KEY, String(next));
  } catch {
    // 持久化失败不影响内存态与通知
  }
  for (const listener of listeners) {
    try {
      listener(next);
    } catch {
      // 单个订阅者异常不阻断其余通知
    }
  }
}

export function subscribeProjectSelection(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// 多标签页：监听其他页签的写入，桥接为本页内存态 + 订阅通知。
// 幂等延迟安装：import 时无 window（SSR/测试）则首次写入时再装。
let storageBridgeInstalled = false;
function installStorageBridge() {
  if (storageBridgeInstalled) return;
  const w = globalThis.window;
  if (!w || typeof w.addEventListener !== "function") return;
  w.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY) return;
    const next = normalize(event.newValue);
    if (next === currentId) return;
    currentId = next;
    for (const listener of listeners) {
      try {
        listener(next);
      } catch {
        // 单个订阅者异常不阻断其余通知
      }
    }
  });
  storageBridgeInstalled = true;
}

try {
  installStorageBridge();
} catch {
  // 监听不可用时跳过（单页签语义不受影响）
}

// 仅供测试使用：重置模块级状态，避免用例间串扰
export function _resetForTest() {
  currentId = null;
  listeners.clear();
  storageBridgeInstalled = false;
  try {
    globalThis.window?.localStorage?.removeItem(STORAGE_KEY);
  } catch {
    // 忽略
  }
}
