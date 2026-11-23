// BFF 访问 Flask 后端的唯一出口：所有出站请求都从这里发（守护见 test/backendFetch.test.js）。
//
// 不复用连接。gunicorn（docker/start-backend.sh 未设 --keep-alive）在连接空闲 2 秒后关闭它，
// 且不发 Keep-Alive 超时提示；Node 18 自带 undici 的连接池却默认把空闲连接留 4 秒。客户端于是
// 会把一条服务端正在关闭的连接拿去发下一个请求，得到 UND_ERR_SOCKET(other side closed)，
// 在浏览器里就是一个莫名的 502。线上 Miner 日志保留范围内 38 次（会话校验 9、接口转发 29）；
// 间隔约 2 秒的 240 次只读请求实测失败 27 次（2026-11-24）。
//
// 为什么是 Connection: close，而不是：
// - 只加重试——治不了本。接口转发那一侧没有重试，补的话还要逐个判断哪些 POST 可以安全重放。
// - 让后端发超时提示 / 调大 gunicorn keep-alive——跨模块的部署变更，影响所有客户端。
// - 自建 undici Agent 调短空闲超时——Node 18 没有内置 undici 模块，要新增依赖。
// 代价是每个请求多一次容器网络内的 TCP 握手，换确定性。顺带的好处：gunicorn gthread 模式下
// 一条 keep-alive 连接会占住一个工作线程直到空闲超时，不复用后线程在响应完立即释放。
export async function fetchBackend(url, options = {}) {
  return fetch(url, {
    ...options,
    // 放在最后：调用方传入的请求头不能把这条策略覆盖掉
    headers: { ...(options.headers || {}), connection: 'close' },
  });
}
