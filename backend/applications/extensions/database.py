from flask import request
from flask_sqlalchemy import BaseQuery, SQLAlchemy

# 分页 per_page 上限：layui 分页直通 SQL limit，无上界会让单请求整表加载
PER_PAGE_MAX = 100


def _bounded_per_page(limit):
    if limit is None:
        return None
    return max(1, min(PER_PAGE_MAX, limit))


class Query(BaseQuery):
    def all_json(self, schema):
        return schema(many=True).dump(self.all())

    def layui_paginate(self):
        limit = _bounded_per_page(request.args.get('limit', type=int))
        page = request.args.get('page', type=int)
        return self.paginate(page=page, per_page=limit, error_out=False)

    def layui_paginate_json(self, schema):
        """
        返回dict
        """
        _res = self.paginate(
            page=request.args.get('page', type=int),
            per_page=_bounded_per_page(request.args.get('limit', type=int)),
            error_out=False)
        return schema(
            many=True).dump(_res.items), _res.total, _res.page, _res.per_page

    def layui_paginate_db_json(self):
        """
        db.query(A.name).layui_paginate_db_json()
        """
        _res = self.paginate(
            page=request.args.get('page', type=int),
            per_page=_bounded_per_page(request.args.get('limit', type=int)),
            error_out=False)
        return [dict(i) for i in _res.items], _res.total


db = SQLAlchemy(query_class=Query)
