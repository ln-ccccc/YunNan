import fs from 'fs';
import path from 'path';

// 与前端上传入口的 50MB 上限对齐；服务端必须自设上限（2026-09-20 审查 P2），
// 否则登录用户可循环提交超大文本填满共享磁盘
const MAX_KML_CONTENT_BYTES = 50 * 1024 * 1024;

export function saveKmlUpload({ uploadRoot, filename, content }) {
  const originalName = String(filename || '').trim();
  const safeName = path.basename(originalName);
  if (!safeName.toLowerCase().endsWith('.kml')) {
    throw new Error('只支持 .kml 文件');
  }

  const text = String(content || '');
  if (!text.trim()) {
    throw new Error('KML 内容不能为空');
  }
  if (Buffer.byteLength(text, 'utf-8') > MAX_KML_CONTENT_BYTES) {
    throw new Error('KML 文件超过 50MB 上限');
  }

  const resolvedRoot = path.resolve(uploadRoot);
  fs.mkdirSync(resolvedRoot, { recursive: true });
  const target = path.resolve(resolvedRoot, safeName);
  if (!target.startsWith(resolvedRoot + path.sep) && target !== resolvedRoot) {
    throw new Error('KML 文件名非法');
  }

  fs.writeFileSync(target, text, 'utf-8');
  // 返回裸文件名：浏览器不得感知服务器物理路径（AGENTS §6）；
  // 绝对路径仅供服务端日志/排查，不进入前端可见的提交链路
  return { kml_path: safeName, kml_path_absolute: target };
}
