import os
import os.path as osp
import uuid

from flask import current_app
from sqlalchemy import desc

from applications.common.curd import model_to_dicts
from applications.extensions import db
from applications.extensions.init_upload import photos
from applications.models import Photo
from applications.schemas import PhotoOutSchema


def get_photo(page, limit):
    photo = Photo.query.order_by(desc(Photo.create_time)).paginate(page=page, per_page=limit, error_out=False)
    count = Photo.query.count()
    data = model_to_dicts(schema=PhotoOutSchema, data=photo.items)
    return data, count


def upload_one(photo, mime, type_=0, enable_slicing=False, keep_tiff_raw=False):
    from applications.common.utils.tiff_processor import is_tiff_file, process_uploaded_tiff

    filename = photos.save(photo, name=str(uuid.uuid4()) + ".")
    upload_url = current_app.config.get("UPLOADED_PHOTOS_DEST")
    full_path = os.path.join(upload_url, filename)

    original_filename = getattr(photo, 'filename', filename)
    processed_files = []
    raw_tiff_path = full_path if is_tiff_file(filename) else None

    if is_tiff_file(filename) and keep_tiff_raw:
        # 地物分类（图斑切片推理）快速路径（移植江西 2026-09-19）：只需原始
        # tif 本体，不切片、不生成预览 PNG，也不受 500MB 预处理闸门影响——
        # GB 级县级影像经此直传后由推理链路按图斑窗口裁剪流式处理
        processed_files.append({
            'filename': filename,
            'mime': mime,
            'path': full_path,
            'display_name': original_filename,
        })
    elif is_tiff_file(filename):
        try:
            results = process_uploaded_tiff(
                full_path,
                upload_url,
                original_filename=original_filename,
                enable_slicing=enable_slicing,
            )

            if (not keep_tiff_raw) and os.path.exists(full_path):
                os.remove(full_path)
                raw_tiff_path = None

            for res in results:
                processed_files.append({
                    'filename': res['filename'],
                    'mime': 'image/png',
                    'path': res['path'],
                    'display_name': res['filename'],
                })

        except Exception:
            # 切片为场景分类预览依赖；失败（含 >500MB、损坏文件）不再删除
            # 原始文件并拒绝上传——保留原始文件优雅降级，由下游推理链路
            # 给出更精准的错误（移植江西 2026-09-19 验收反馈：2.4GB 影像被拒）
            processed_files.append({
                'filename': filename,
                'mime': mime,
                'path': full_path,
                'display_name': original_filename,
            })
    else:
        processed_files.append({
            'filename': filename,
            'mime': mime,
            'path': full_path,
            'display_name': original_filename,
        })

    return_data = []

    for p_file in processed_files:
        p_filename = p_file['filename']
        p_mime = p_file['mime']
        p_path = p_file['path']
        p_display = p_file['display_name']

        file_url = '/_uploads/photos/' + p_filename
        size = os.path.getsize(p_path) if os.path.exists(p_path) else 0

        photo_record = Photo(name=p_filename, href=file_url, mime=p_mime, size=size, type=type_)
        db.session.add(photo_record)
        db.session.flush()

        return_data.append((file_url, photo_record.id, p_display, raw_tiff_path))

    db.session.commit()
    return return_data


def delete_photo_by_id(_id):
    photo_name = Photo.query.filter_by(id=_id).first().name
    photo = Photo.query.filter_by(id=_id).delete()
    db.session.commit()
    upload_url = current_app.config.get("UPLOADED_PHOTOS_DEST")
    os.remove(upload_url + '/' + photo_name)
    return photo


def img_url_handle(url):
    return osp.basename(url)
