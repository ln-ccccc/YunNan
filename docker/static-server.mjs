import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(process.argv[2] || '.');
const port = Number(process.argv[3] || 3000);
const fallback = process.argv[4] || '/index.html';
const proxies = [
  { prefix: '/api/', target: process.env.PROXY_API_TARGET || '' },
  { prefix: '/tiles/', target: process.env.PROXY_TILES_TARGET || process.env.PROXY_API_TARGET || '' },
  { prefix: '/change-matrix-outputs/', target: process.env.PROXY_CHANGE_MATRIX_TARGET || process.env.PROXY_API_TARGET || '' },
].filter((item) => item.target);

const types = new Map([
  ['.css', 'text/css; charset=utf-8'],
  ['.html', 'text/html; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.map', 'application/json; charset=utf-8'],
  ['.png', 'image/png'],
  ['.jpg', 'image/jpeg'],
  ['.jpeg', 'image/jpeg'],
  ['.svg', 'image/svg+xml'],
  ['.ico', 'image/x-icon'],
]);

// GeoView 和 Miner 的路由页面是按需加载的代码块（GeoView：/js/<编号>.<哈希>.js，Miner：/assets/<名字>-<哈希>.js），
// 文件名带内容哈希，发一次版本就换一批。上线或回滚之后，还开着旧页面的浏览器切到没打开过的页面时，会来要旧名字的代码块。
// 此前它拿到的是回落出来的 index.html——一段 HTML 被当成脚本执行，报语法错误，页面卡住或白屏。
// 现在回一小段脚本：问用户要不要刷新，刷新之后拿到的就是正在运行的那个版本。
const staleScript = `(() => {
  const now = Date.now();
  let last;
  try {
    last = Number(sessionStorage.getItem('yunnan-stale-script-at') || 0);
    sessionStorage.setItem('yunnan-stale-script-at', String(now));
  } catch (error) {
    return; // 用不了会话存储就防不了反复刷新，什么都不做
  }
  // 30 秒内只问一次：刷新之后新页面要的还是不存在的代码块（构建本身有问题），不能一遍一遍地问
  if (now - last < 30000) return;
  if (window.confirm('系统已更新到新版本，需要刷新页面后才能继续使用。\\n点“确定”立即刷新（尚未保存的内容会丢失），刷新后请重新打开要去的页面；\\n点“取消”留在本页，请先保存，再手动刷新。')) {
    window.location.reload();
  }
})();`;

// 只认两个站点放构建产物的目录；其它缺失文件照旧回落
function isStaleScript(request, requestPath) {
  return (request.method === 'GET' || request.method === 'HEAD') && /^\/(js|assets)\/[^/]+\.js$/.test(requestPath);
}

function sendStaleScript(request, response, requestPath) {
  console.warn(`[static-server] 页面请求了已不存在的代码块，已回复刷新提示：${JSON.stringify(requestPath)}`);
  response.writeHead(200, {
    'content-type': 'text/javascript; charset=utf-8',
    'cache-control': 'no-store',
  });
  response.end(request.method === 'HEAD' ? undefined : staleScript);
}

function sendFile(response, filePath) {
  fs.readFile(filePath, (error, data) => {
    if (error) {
      response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
      response.end('Not found');
      return;
    }
    response.writeHead(200, {
      'content-type': types.get(path.extname(filePath).toLowerCase()) || 'application/octet-stream',
    });
    response.end(data);
  });
}

function proxyRequest(request, response, proxy) {
  const target = new URL(request.url, proxy.target);
  const proxied = http.request(target, {
    method: request.method,
    headers: { ...request.headers, host: target.host },
  }, (proxyResponse) => {
    response.writeHead(proxyResponse.statusCode || 502, proxyResponse.headers);
    proxyResponse.pipe(response);
  });
  proxied.on('error', () => {
    // 2026-11-05 迁移审查：不回显 error.message（含上游主机名/端口/DNS 细节，
    // 泄露内网拓扑）；固定文案足够排障（容器日志里 proxied 抛错仍可见）
    response.writeHead(502, { 'content-type': 'application/json; charset=utf-8' });
    response.end(JSON.stringify({ error: 'Proxy failed' }));
  });
  request.pipe(proxied);
}

http.createServer((request, response) => {
  try {
    // 显式 origin-form 断言（2026-11-05 迁移审查 P2 纵深，江西 5.3 同款）：
    // 绝对形请求（GET http://内网IP/... HTTP/1.1）会让 Node 的 req.url 变为
    // 绝对 URL——此前靠 startsWith('/api/') 前缀巧合挡住 base 覆盖（SSRF），
    // 这里把巧合升级为显式拒绝，防止未来改前缀匹配时打开旁路。
    if (!request.url.startsWith('/')) {
      response.writeHead(400, { 'content-type': 'text/plain; charset=utf-8' });
      response.end('Bad request form');
      return;
    }
    const proxy = proxies.find((item) => request.url.startsWith(item.prefix));
    if (proxy) {
      proxyRequest(request, response, proxy);
      return;
    }

    // 截断的百分号编码（如 /%C3）会让 decodeURIComponent 抛 URIError；
    // 回调内同步异常会让整个静态服务进程退出（2026-09-20 审查 P1），按 400 拒绝即可
    let requestPath;
    try {
      requestPath = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    } catch {
      response.writeHead(400, { 'content-type': 'text/plain; charset=utf-8' });
      response.end('Bad request');
      return;
    }
    const safePath = path.normalize(requestPath).replace(/^(\.\.[/\\])+/, '');
    let filePath = path.join(root, safePath);
    if (fs.existsSync(filePath) && fs.statSync(filePath).isDirectory()) {
      filePath = path.join(filePath, 'index.html');
    }
    if (!fs.existsSync(filePath)) {
      if (isStaleScript(request, requestPath)) {
        sendStaleScript(request, response, requestPath);
        return;
      }
      filePath = path.join(root, fallback);
    }
    sendFile(response, filePath);
  } catch (error) {
    console.error('[static-server] request handler error:', error);
    if (!response.headersSent) {
      response.writeHead(500, { 'content-type': 'text/plain; charset=utf-8' });
    }
    response.end('Internal error');
  }
}).listen(port, '0.0.0.0', () => {
  console.log(`[static-server] ${root} -> http://0.0.0.0:${port}`);
});
