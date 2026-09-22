import {request} from "@/api/request.js"
import {
    DEFAULT_CHUNK_SIZE_BYTES,
    planChunks,
    sha256HexPlain,
    sumReceivedBytes,
} from "@/utils/uploadChunking.mjs";
export function createSrc(formdata, options = {}) {
    return request({
        method: 'POST',
        url: '/api/file/upload',
        data:formdata,
        // 上传自带进度通知（getUploadImg）：silent 避免全屏锁死且保证取消按钮可点
        silent: true,
        // 透传上传进度回调与取消信号（8GB 大文件 UX：进度可取消，江西 F2 同款）
        signal: options.signal,
        onUploadProgress: options.onUploadProgress,
        transformRequest: [function(data, headers) {
            delete headers.post['Content-Type']
            return data
        }],
        headers:{
            'Accept': 'multipart/form-data',
            'Content-Type': 'multipart/form-data'
        }
    })
}
export function imgUpload(data,funUrl){
    return request({
        method:'POST',
        url:`/api/analysis/${funUrl}`,
        data
    })
}

export function prePhotoHandle(data){
    return request({
        method:'POST',
        url:'/api/analysis/image_pre',
        data
    })
}

export function kmlRoiInfer(data){
    return request({
        method:'POST',
        url:'/api/analysis/kml_roi_inference',
        data
    })
}

// ===== 分片续传（100GB 级影像，2026-09-22）=====
// 单发 multipart 受 gunicorn 单请求超时约束；分片每块秒级完成，失败只重传
// 单块，页面重开/失败重试经幂等 init 自动跳过已收分块（断点续传）。

function abortError() {
    return Object.assign(new Error('请求已取消'), { kind: 'aborted' });
}

async function sha256Hex(input) {
    const bytes = input instanceof Blob ? new Uint8Array(await input.arrayBuffer()) : new TextEncoder().encode(String(input));
    // crypto.subtle 仅安全上下文（HTTPS/localhost）存在；http://内网IP 访问时
    // 回退纯 JS 实现，保住会话标识与逐块校验（2026-09-22 三路审查一致 P1）
    if (globalThis.crypto && globalThis.crypto.subtle) {
        const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
        return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, '0')).join('');
    }
    return sha256HexPlain(bytes);
}

/**
 * 会话标识：文件名+大小+修改时间的摘要（hex）。后端以此为目录名做幂等 init，
 * 同一文件无论重试多少次都落进同一会话，天然断点续传。
 */
export async function computeUploadKey(file) {
    return sha256Hex(`${file.name}:${file.size}:${file.lastModified}`);
}

function chunkedInit(payload) {
    return request({
        method: 'POST',
        url: '/api/file/upload/init',
        data: payload,
        silent: true,
    });
}

function chunkedPutOnce(sessionId, index, blob, { signal, sha } = {}) {
    const headers = { 'Content-Type': 'application/octet-stream' };
    if (sha) headers['X-Chunk-Sha256'] = sha;
    return request({
        method: 'POST',
        url: `/api/file/upload/chunk/${encodeURIComponent(sessionId)}/${index}`,
        data: blob,
        headers,
        silent: true,
        signal,
    });
}

// 单块上传带 2 次退避重试：瞬时网络抖动只重传当前块，不放弃整批
// （此前任一块失败即整批 reject，重试会把已完成文件从头再传并重复入库）
async function chunkedPut(sessionId, index, blob, options = {}) {
    const retryable = (error) => {
        if (options.signal?.aborted) return false;
        if (error?.kind === 'network') return true;
        const status = error?.response?.status ?? error?.cause?.response?.status;
        return status === undefined || status >= 500 || status === 408 || status === 429;
    };
    try {
        return await chunkedPutOnce(sessionId, index, blob, options);
    } catch (error) {
        if (!retryable(error)) throw error;
        await new Promise((resolve) => setTimeout(resolve, 800));
        try {
            return await chunkedPutOnce(sessionId, index, blob, options);
        } catch (second) {
            if (!retryable(second)) throw second;
            await new Promise((resolve) => setTimeout(resolve, 1600));
            return chunkedPutOnce(sessionId, index, blob, options);
        }
    }
}

/**
 * 分片上传编排：逐文件 init → 跳过已收分块 → 顺序传块 → complete。
 * 返回与 createSrc 同构的响应（{data:{data:[{src,filename,photo_id,raw_tiff_path}]}}），
 * 下游（getUploadImg 的上传后处理）无需感知通道差异。
 * options.onProgress(doneBytes, totalBytes) 供聚合进度条。
 */
export async function uploadFilesResumable(fileEntries, meta = {}, options = {}) {
    const entries = Array.isArray(fileEntries) ? fileEntries : [];
    const totalBytes = entries.reduce((sum, entry) => sum + (Number(entry.size) || 0), 0);
    const results = [];
    let doneBytes = 0;

    for (const entry of entries) {
        if (options.signal?.aborted) throw abortError();
        const uploadKey = await computeUploadKey(entry);
        const initResponse = await chunkedInit({
            upload_key: uploadKey,
            filename: entry.name,
            total_size: entry.size,
            mime: entry.mime || 'image/tiff',
            chunk_size: DEFAULT_CHUNK_SIZE_BYTES,
        });
        const state = initResponse?.data?.data || {};
        if (!state.session_id) throw new Error('分片上传初始化未返回会话标识');
        // 后端 done 幂等：complete 成功过（响应丢失/批次中途失败重试）直接复用
        // 上次结果——不重传、不重复入库（2026-09-22 审查 P1）
        if (state.done && Array.isArray(state.result)) {
            results.push(...state.result);
            doneBytes += Number(entry.size) || 0;
            options.onProgress?.(doneBytes, totalBytes);
            continue;
        }

        const received = new Set(state.received || []);
        const plan = planChunks(entry.size, state.chunk_size || DEFAULT_CHUNK_SIZE_BYTES);
        if (plan.length === 0) throw new Error(`文件 ${entry.name} 无法规划分片（大小 ${entry.size}）`);
        let fileDone = sumReceivedBytes(plan, received);
        options.onProgress?.(doneBytes + fileDone, totalBytes);

        for (const chunk of plan) {
            if (options.signal?.aborted) throw abortError();
            if (received.has(chunk.index)) continue;
            const blob = entry.file.slice(chunk.start, chunk.start + chunk.size);
            const sha = await sha256Hex(blob);
            await chunkedPut(state.session_id, chunk.index, blob, { signal: options.signal, sha });
            fileDone += chunk.size;
            options.onProgress?.(doneBytes + fileDone, totalBytes);
        }

        // 合并阶段（GB 级可达分钟）同样受取消控制（审查 P1：此前未传 signal，
        // 取消按钮在合并期间假死；取消后会话仍在，重试即续传）
        const completeResponse = await request({
            method: 'POST',
            url: `/api/file/upload/complete/${encodeURIComponent(state.session_id)}`,
            data: {
                type: meta.type,
                isSlice: Boolean(meta.isSlice),
                keepRawTiff: Boolean(meta.keepRawTiff),
                mime: entry.mime || 'image/tiff',
            },
            silent: true,
            signal: options.signal,
        });
        results.push(...(completeResponse?.data?.data || []));
        doneBytes += Number(entry.size) || 0;
    }

    return { data: { data: results } };
}
