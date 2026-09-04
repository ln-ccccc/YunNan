from flask import Blueprint, jsonify, request

from applications.auth.guard import ensure_logged_in
from applications.common.utils import type_utils, upload as upload_curd
from applications.common.utils.http import fail_api
from applications.common.utils.tiff_processor import MAX_TIFF_SIZE_MB, is_tiff_file

file_api = Blueprint('file_api', __name__, url_prefix='/api/file')


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

    for photo in photos:
        if is_tiff_file(photo.filename):
            photo.seek(0, 2)
            size_bytes = photo.tell()
            photo.seek(0)
            size_mb = size_bytes / (1024 * 1024)
            if size_mb > MAX_TIFF_SIZE_MB:
                return fail_api(f"TIFF 文件 '{photo.filename}' 大小 ({size_mb:.1f}MB) 超过限制 ({MAX_TIFF_SIZE_MB}MB)")

    data = []
    is_slice_str = request.form.get('isSlice', 'false')
    is_slice = is_slice_str.lower() == 'true'
    keep_raw_tiff_str = request.form.get('keepRawTiff', 'false')
    keep_raw_tiff = keep_raw_tiff_str.lower() == 'true'

    for photo in photos:
        mime = photo.content_type
        try:
            upload_results = upload_curd.upload_one(
                photo=photo,
                mime=mime,
                type_=to_type,
                enable_slicing=is_slice,
                keep_tiff_raw=keep_raw_tiff,
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
            return fail_api(f'文件上传失败: {str(e)}')

    res = {'msg': '上传成功', 'code': 0, 'success': True, 'data': data}
    return jsonify(res)

