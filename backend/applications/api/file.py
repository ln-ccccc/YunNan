import logging
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request, session

from applications.api.error_responses import business_or_server_failure
from applications.auth.guard import ensure_logged_in
from applications.common.utils import type_utils, upload as upload_curd
from applications.common.utils import upload_sessions
from applications.common.utils.http import fail_api, success_api
from applications.common.utils.tiff_processor import MAX_UPLOAD_TIFF_SIZE_MB, is_tiff_file
from applications.extensions.init_upload import IMAGES_WITH_TIFF

file_api = Blueprint('file_api', __name__, url_prefix='/api/file')
LOGGER = logging.getLogger(__name__)

# 分片续传通道扩展名白名单（2026-11-05 迁移审查 P1-1）：
# 与单发通道的 IMAGES_WITH_TIFF 同源推导，剔除 ENVI 成对格式（dat/bin/hdr 分片必断链，
# 单独给出单发引导文案）。此前 init/complete 只黑名单 ENVI、无正向白名单，
# 任意后缀（.svg/.html）可经分片通道落盘并被 /_uploads 按扩展名推断的
# Content-Type 内联提供（认证内存储型 XSS，江西 5.1+5.6 叠加教训）。
CHUNKED_UPLOAD_EXTENSIONS = frozenset(
    ext.lower() for ext in IMAGES_WITH_TIFF if ext.lower() not in ("dat", "bin", "hdr")
)

# 单发通道普通图片硬上限（2026-11-05 生产化检查，迁移审查 P2-4）：此前 1GB 闸门
# 只查 tif 系栅格，jpg/png/gif 可单发 ≤15GB——Werkzeug 全量内存解析（实测
# 573MB 峰值 1.78GiB），登录用户可一枚"图片"打爆 backend 内存。512MB 与前端
# 自动分流分片续传的阈值同源（>512MB 走分片），真实照片/截图远低于此。
MAX_UPLOAD_IMAGE_SIZE_MB = 512


def _chunked_extension_rejection(filename):
    """分片通道扩展名闸门：返回 (body, status) 拒绝响应或 None 放行。"""
    suffix = Path(filename).suffix.lstrip(".").lower()
    if suffix in ("dat", "bin", "hdr"):
        return fail_api("ENVI 影像暂不支持分片续传通道，请使用单次上传（需 8GB 内）"), 400
    if suffix not in CHUNKED_UPLOAD_EXTENSIONS:
        allowed = "、".join(sorted(CHUNKED_UPLOAD_EXTENSIONS))
        return fail_api(
            f"分片续传仅支持影像/图片格式（{allowed}），"
            f"当前文件后缀 .{suffix or '(无)'} 不受支持"
        ), 400
    return None


@file_api.before_request
def require_file_auth():
    return ensure_logged_in()


@file_api.post('/upload')
def upload_api():
    if 'files' not in request.files:
        return fail_api('请选择文件')

    type_ = request.form['type']
    to_type = type_utils.str_to_type(type_)
    photos = request.files.getlist('files')

    # S1 格式扩展：ENVI 成对校验（.dat/.bin 必须与同名 .hdr 同批上传）
    from applications.common.utils.raster_formats import RasterFormatError, detect_raster_kind

    try:
        raster_entries = detect_raster_kind([photo.filename for photo in photos])
    except RasterFormatError as error:
        return fail_api(str(error)), 400
    raster_filenames = {entry["filename"] for entry in raster_entries}

    for photo in photos:
        if is_tiff_file(photo.filename) and photo.filename in raster_filenames:
            photo.seek(0, 2)
            size_bytes = photo.tell()
            photo.seek(0)
            size_mb = size_bytes / (1024 * 1024)
            # 单发 multipart 上限 1GB（2026-11 验收 R5）：werkzeug 解析全量
            # 内存缓冲，实测 573MB 即峰值 1.78GiB/570s，大文件必走分片。
            # 前端 >512MB 已自动分流分片续传通道：
            # /api/file/upload/init|chunk/<sid>/<idx>|complete/<sid>
            if size_mb > MAX_UPLOAD_TIFF_SIZE_MB:
                return fail_api(
                    f"影像文件 '{photo.filename}' 大小 ({size_mb:.1f}MB) 超过单发上传硬上限 "
                    f"({MAX_UPLOAD_TIFF_SIZE_MB}MB)，请改用分片续传上传"
                )
        elif not is_tiff_file(photo.filename):
            # 普通图片单发上限（P2-4）：非栅格无逐文件大小闸门，见常量注释
            photo.seek(0, 2)
            image_size_mb = photo.tell() / (1024 * 1024)
            photo.seek(0)
            if image_size_mb > MAX_UPLOAD_IMAGE_SIZE_MB:
                return fail_api(
                    f"图片文件 '{photo.filename}' 大小 ({image_size_mb:.1f}MB) 超过单发上传上限 "
                    f"({MAX_UPLOAD_IMAGE_SIZE_MB}MB)，请改用分片续传上传"
                )

    data = []
    is_slice_str = request.form.get('isSlice', 'false')
    is_slice = is_slice_str.lower() == 'true'
    keep_raw_tiff_str = request.form.get('keepRawTiff', 'false')
    keep_raw_tiff = keep_raw_tiff_str.lower() == 'true'

    # ENVI 对（数据+头）共享 UUID 词干；.dat 与 .hdr 分别落 <stem>.dat / <stem>.hdr
    envi_shared_stems = {}
    for entry in raster_entries:
        if entry["kind"] == "envi":
            shared = str(uuid.uuid4())
            envi_shared_stems[entry["filename"]] = shared
            envi_shared_stems[entry["header"]] = shared

    for photo in photos:
        mime = photo.content_type
        try:
            upload_results = upload_curd.upload_one(
                photo=photo,
                mime=mime,
                type_=to_type,
                enable_slicing=is_slice,
                keep_tiff_raw=keep_raw_tiff,
                name_hint=envi_shared_stems.get(photo.filename),
            )
            for file_url, photo_id, display_name, raw_tiff_path in upload_results:
                data.append({
                    'src': file_url,
                    'filename': display_name,
                    'photo_id': photo_id,
                    'raw_tiff_path': raw_tiff_path,
                })
        except ValueError as e:
            return fail_api(str(e))
        except Exception as e:
            return business_or_server_failure(e, "文件上传失败", logger=LOGGER)

    res = {'msg': '上传成功', 'code': 0, 'success': True, 'data': data}
    return jsonify(res)


@file_api.post('/upload/init')
def upload_session_init_api():
    """分片续传：幂等初始化（同 key 同文件复用会话并返回已收分块，支持断点续传）。"""
    payload = request.get_json(silent=True) or {}
    # 扩展名闸门前置到 init（2026-11-05 P1-1）：未传一块就 fail-fast，
    # 避免用户传完几十 GB 才在 complete 被拒
    rejection = _chunked_extension_rejection(str(payload.get('filename') or ''))
    if rejection is not None:
        return rejection[0], rejection[1]
    try:
        state = upload_sessions.init_session(
            upload_key=payload.get('upload_key'),
            filename=payload.get('filename'),
            total_size=payload.get('total_size'),
            mime=payload.get('mime') or 'application/octet-stream',
            chunk_size=payload.get('chunk_size'),
            owner=session.get('admin_username'),
        )
    except upload_sessions.UploadSessionError as error:
        return fail_api(str(error)), 400
    return success_api(data=state)


@file_api.post('/upload/chunk/<session_id>/<int:index>')
def upload_session_chunk_api(session_id, index):
    """分片续传：接收一个分块（octet-stream 原始体），幂等（重传覆盖）。"""
    try:
        written = upload_sessions.write_chunk(
            session_id,
            index,
            request.stream,
            declared_sha256=request.headers.get('X-Chunk-Sha256'),
            owner=session.get('admin_username'),
        )
    except upload_sessions.UploadSessionError as error:
        return fail_api(str(error)), 400
    return success_api(data={'session_id': session_id, 'index': index, 'bytes': written})


@file_api.post('/upload/complete/<session_id>')
def upload_session_complete_api(session_id):
    """分片续传：校验合并并走与单发通道同源的 upload_one_from_path 处理。

    响应形状与 /api/file/upload 完全一致（{src, filename, photo_id, raw_tiff_path}）。
    """
    payload = request.get_json(silent=True) or {}
    type_ = type_utils.str_to_type(payload.get('type') or '')
    is_slice = bool(payload.get('isSlice', False))
    keep_raw_tiff = bool(payload.get('keepRawTiff', False))
    # 与 photos.save 同一个上传目标（跟随 UPLOADED_PHOTOS_DEST 配置）
    from flask import current_app

    upload_root = Path(current_app.config.get('UPLOADED_PHOTOS_DEST') or 'static/upload')

    # 属主校验（2026-11-05 迁移审查 P2-12）：init/chunk 均有，complete 此前缺失——
    # 登录态内他人拿到会话 id 可抢先完成收割；与底图孪生端点对齐（spatial_service.py）。
    # 注意用 flask_session 别名：下方 session_state 的返回值以局部名 session 遮蔽
    # 了模块级导入的 Flask session（整函数作用域），直接用会在赋值前触发 UnboundLocalError。
    from flask import session as flask_session

    try:
        upload_sessions.ensure_session_owner(session_id, flask_session.get('admin_username'))
    except upload_sessions.UploadSessionError as error:
        return fail_api(str(error)), 400

    try:
        state = upload_sessions.validate_session_id(session_id)
        session = upload_sessions.session_state(session_id)
    except upload_sessions.UploadSessionError as error:
        return fail_api(str(error)), 400

    # 组装直落上传目标（同卷就地合并，避免三倍磁盘峰值），命名规则与 photos.save 一致
    suffix = Path(session['filename']).suffix
    # 扩展名闸门（2026-11-05 P1-1）：ENVI 沿用单发引导文案；其余非白名单后缀拒绝，
    # 防 init 之后逻辑被绕过或历史残留会话把 .svg/.html 落进上传根
    rejection = _chunked_extension_rejection(session['filename'])
    if rejection is not None:
        return rejection[0], rejection[1]
    dest_path = upload_root / f"{uuid.uuid4()}{suffix}"

    # done 幂等：complete 成功过（响应丢失/批次重试/双标签并发）直接复用上次结果，
    # 不重新合并、不重复入库（2026-09-22 数据流审查 P1）
    existing_result = upload_sessions.session_is_done(session_id)
    if existing_result is not None:
        return jsonify({'msg': '上传成功', 'code': 0, 'success': True, 'data': existing_result})

    try:
        upload_sessions.assemble_session(session_id, dest_path)
        upload_results = upload_curd.upload_one_from_path(
            str(dest_path),
            session['filename'],
            payload.get('mime') or 'image/tiff',
            type_=type_,
            enable_slicing=is_slice,
            keep_tiff_raw=keep_raw_tiff,
        )
    except upload_sessions.UploadSessionError as error:
        dest_path.unlink(missing_ok=True)
        return fail_api(str(error)), 400
    except ValueError as error:
        dest_path.unlink(missing_ok=True)
        return fail_api(str(error))
    except Exception as error:
        dest_path.unlink(missing_ok=True)
        return business_or_server_failure(error, "文件上传失败", logger=LOGGER)

    data = []
    for file_url, photo_id, display_name, raw_tiff_path in upload_results:
        data.append({
            'src': file_url,
            'filename': display_name,
            'photo_id': photo_id,
            'raw_tiff_path': raw_tiff_path,
        })
    # 成功后写完成态而非删目录（幂等；过期由 sweep 清理）
    upload_sessions.mark_session_done(session_id, data)
    return jsonify({'msg': '上传成功', 'code': 0, 'success': True, 'data': data})

