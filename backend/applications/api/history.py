import json

from flask import Blueprint, request
from sqlalchemy import desc

from applications.auth.guard import ensure_logged_in
from applications.common.curd import model_to_dicts
from applications.common.utils import type_utils
from applications.common.utils.http import fail_api, success_api, table_api
from applications.common.utils.type_utils import items_handle
from applications.extensions import db
from applications.models.analysis import Analysis
from applications.schemas import AnalysisSchema

history_api = Blueprint('history_api', __name__, url_prefix='/api/history')


@history_api.before_request
def require_history_auth():
    return ensure_logged_in()
"""
    查询我的历史记录
"""


@history_api.get('/list')
def history_list():
    # orm查询
    # 使用分页获取data需要.items
    _type = request.args.get('type', type=str)
    if _type is None or _type == '""' or _type == "":
        log = Analysis.query.order_by(desc(
            Analysis.create_time)).layui_paginate()
        count = log.total
        items = log.items
        analysis_handle(items)
        dicts = model_to_dicts(schema=AnalysisSchema, data=items)
        items_handle(dicts)
        return table_api(data=dicts, count=count)
    else:
        to_type = type_utils.str_to_type(_type)
        log = Analysis.query.filter_by(
            type=to_type).order_by(desc(Analysis.create_time)).layui_paginate()
        count = log.total
        items = log.items
        analysis_handle(items)
        dicts = model_to_dicts(schema=AnalysisSchema, data=items)
        items_handle(dicts)
        return table_api(data=dicts, count=count)


def analysis_handle(items):
    for t in items:
        if t.data == "" or t.data is None:
            continue
        t.data = json.loads(t.data)
    pass


"""
批量删除
"""


@history_api.delete('/batchRemove')
def history_delete():
    # 无 body / JSON null 时 request.json 为 None，缺省容错避免 TypeError 进全局处理器
    req_json = request.json or {}
    ids = req_json.get('ids')
    # ids 必须是整型列表：字符串会被逐字符迭代误删（"12" 删掉 id=1 和 id=2），
    # 非可迭代对象直接 500
    if not isinstance(ids, list) or not ids or not all(
        isinstance(item, int) and not isinstance(item, bool) for item in ids
    ):
        return fail_api(msg="参数异常：ids 必须是记录 ID 列表")
    Analysis.query.filter(Analysis.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    return success_api(msg="批量删除成功")


@history_api.delete('/removeOne')
def history_remove_one():
    req_json = request.json or {}
    rid = req_json.get('id')
    if rid is None:
        return fail_api(msg="参数异常")
    Analysis.query.filter_by(id=rid).delete()
    db.session.commit()
    return success_api(msg="删除成功")


@history_api.delete('/clearByType')
def history_clear_by_type():
    req_json = request.json or {}
    type_name = req_json.get('type')
    to_type = type_utils.str_to_type(type_name)
    if to_type is None:
        return fail_api(msg="类型不正确")
    removed = Analysis.query.filter_by(type=to_type).delete()
    db.session.commit()
    return success_api(msg="清理成功", data={"removed": int(removed or 0)})
