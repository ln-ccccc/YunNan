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
  proxied.on('error', (error) => {
    response.writeHead(502, { 'content-type': 'application/json; charset=utf-8' });
    response.end(JSON.stringify({ error: `Proxy failed: ${error.message}` }));
  });
  request.pipe(proxied);
}

http.createServer((request, response) => {
  const proxy = proxies.find((item) => request.url.startsWith(item.prefix));
  if (proxy) {
    proxyRequest(request, response, proxy);
    return;
  }

  const requestPath = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
  const safePath = path.normalize(requestPath).replace(/^(\.\.[/\\])+/, '');
  let filePath = path.join(root, safePath);
  if (fs.existsSync(filePath) && fs.statSync(filePath).isDirectory()) {
    filePath = path.join(filePath, 'index.html');
  }
  if (!fs.existsSync(filePath)) {
    filePath = path.join(root, fallback);
  }
  sendFile(response, filePath);
}).listen(port, '0.0.0.0', () => {
  console.log(`[static-server] ${root} -> http://0.0.0.0:${port}`);
});
