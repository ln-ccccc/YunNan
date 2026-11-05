import logging
import os
import re
import uuid

from flask import Flask, g, request

from applications.auth.guard import ensure_logged_in

_REQUEST_LOGGER = logging.getLogger("yunnan.request")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


def _build_allowed_origins(app):
    configured = app.config.get('CORS_ALLOWED_ORIGINS')
    if configured:
        return [item.strip() for item in str(configured).split(',') if item.strip()]

    hosts = ('localhost', '127.0.0.1')
    ports = {
        int(app.config.get('FRONTEND_PORT', 3000) or 3000),
        int(app.config.get('MINER_FRONTEND_PORT', 4000) or 4000),
    }
    return [f'http://{host}:{port}' for host in hosts for port in sorted(ports)]


def create_app(config_name=None):
    from flask_cors import CORS

    from applications import models
    from applications.api import system_api
    from applications.common.scripts import init_script
    from applications.configs import config
    from applications.extensions import db, init_plugs

    app = Flask(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

    if not config_name:
        config_name = os.getenv('FLASK_CONFIG', 'development')

    app.config.from_object(config[config_name])
    # 版本端点报告实际生效的配置名（显式传参优先于 env；/api/system/version）
    app.config["FLASK_CONFIG_NAME"] = config_name
    # 非 testing 一律拒绝 dev key 回退（2026-11-05 生产化检查，迁移审查 P2-13）：
    # 此前仅 production 拦截，development 静默用 'dev key' 签会话——配置错误
    # （如容器 FLASK_CONFIG 误设 development）不会有任何提示
    if config_name != 'testing' and not os.getenv('SECRET_KEY'):
        raise RuntimeError(
            'SECRET_KEY is required (set SECRET_KEY env; testing config is exempt)'
        )

    models.load_all_models()
    init_plugs(app)

    with app.app_context():
        db.create_all()
        from applications.runtime_schema import ensure_runtime_schema
        ensure_runtime_schema()

    if config_name != 'testing':
        init_script(app)

    # 请求关联 ID（2026-11-05 生产化检查）：前端注入或服务端生成，响应头回显、
    # 5xx 日志携带——一次失败请求可在四层日志里靠同一 ID 串联，不再肉眼对时间戳
    @app.before_request
    def assign_request_id():
        incoming = request.headers.get("X-Request-Id", "")
        if _REQUEST_ID_PATTERN.match(incoming):
            g.request_id = incoming
        else:
            g.request_id = uuid.uuid4().hex[:12]

    @app.after_request
    def echo_request_id(response):
        response.headers["X-Request-Id"] = g.get("request_id", "")
        if response.status_code >= 500:
            _REQUEST_LOGGER.error(
                "%s %s -> %s request_id=%s",
                request.method,
                request.path,
                response.status_code,
                g.get("request_id", ""),
            )
        return response

    # 缓存策略（2026-11-05 生产化检查，迁移审查 P2-1）：此前全域无 Cache-Control，
    # 浏览器对 API 响应自行启发式缓存（410 停用/401 响应也可能被中间层缓存），
    # 而重切后的瓦片又被旧缓存钉住——按资源形态显式声明
    @app.after_request
    def apply_cache_policy(response):
        path = request.path
        if response.status_code in (401, 403, 410, 429):
            # 鉴权/限流/停用响应禁止任何层缓存（优先于路径分支：
            # 401 的 /_uploads 响应绝不能被钉一年）
            response.headers["Cache-Control"] = "no-store"
        elif path.startswith("/_uploads/"):
            # 上传产物文件名为 UUID，内容不可变
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        elif "/tiles/" in path and response.status_code == 200:
            # 真实瓦片可缓存一天；重切属罕见操作且资源 id 会变（404 不缓存）
            response.headers.setdefault("Cache-Control", "public, max-age=86400")
        elif path.startswith("/api/"):
            # 业务数据默认不缓存（正确性优先；有明确语义的端点可在视图内自行覆盖）
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    system_api(app)

    @app.before_request
    def require_static_auth():
        # 上传与生成目录位于 Flask static 目录下（UPLOADED_PHOTOS_DEST），
        # 与 /_uploads/photos 一样必须登录后访问，避免默认 /static/<path>
        # 路由成为免登录下载上传影像与结果图的旁路。
        # 分片暂存（static/upload/.chunks/）不属任何公开产物：已登录用户也不得
        # 经 /static 或 /_uploads 读他人在途分块/会话（2026-09-22 对抗审查）。
        if '/.chunks/' in request.path or request.path.rstrip('/').endswith('/.chunks'):
            from flask import abort

            abort(404)
        if request.path.startswith('/static/'):
            unauthorized = ensure_logged_in()
            if unauthorized is not None:
                return unauthorized

    app.config['SESSION_TYPE'] = 'filesystem'
    # 会话寿命显式化：login 设 permanent=True，但此前无 LIFETIME 配置，
    # Flask 默认 31 天与实测 20 分钟过期不符——显式 7 天并随响应刷新，
    # 消除"双端各自 20 分钟掉线"的体验问题（2026-09-27 会话联动项）
    from datetime import timedelta

    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    # 上传上限与单发大影像闸门（tiff_processor.MAX_UPLOAD_TIFF_SIZE_MB，
    # 2026-11 验收 R5 起 1GB）保持同源：Werkzeug 在表单解析阶段按该值抛
    # 413，若小于视图内闸门会把大文件功能在 HTTP 层拦死（2026-09-20 审查
    # P1）。视图内仍保留逐文件硬上限校验与 500MB 预处理闸门；GB 级大文件
    # 走分片续传通道（UPLOAD_SESSION_MAX_TOTAL_MB=100GB）。
    # 2026-10-31：底图网页上传（spatial_service.MAX_BASEMAP_UPLOAD_BYTES=
    # 15GB，整省/大图集场景）需 MAX_CONTENT_LENGTH 覆盖——取两者较大值加
    # 64MB 冗余；全局上限只挡未登录面滥用，业务口径仍在各视图内校验。
    from applications.common.utils.tiff_processor import MAX_UPLOAD_TIFF_SIZE_MB
    from applications.project_hub.spatial_service import MAX_BASEMAP_UPLOAD_BYTES

    app.config['MAX_CONTENT_LENGTH'] = max(
        MAX_UPLOAD_TIFF_SIZE_MB * 1024 * 1024,
        MAX_BASEMAP_UPLOAD_BYTES + 64 * 1024 * 1024,
    )

    # Werkzeug 在表单解析阶段即抛 413（默认英文 HTML），视图内"请改用分片
    # 续传"引导完全不可达（含绕过前端直连 API 的消费者）；统一转 JSON 业务
    # 响应，保留可行动的中文引导
    from applications.common.utils.http import fail_api

    @app.errorhandler(413)
    def _payload_too_large(_error):
        limit_mb = app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)
        return fail_api(
            f"请求体超过服务端上限 {limit_mb}MB（单发影像 1GB / 底图 15GB，GB 级影像请走分片续传或投递 incoming/）"
        ), 413

    app.config['JSON_AS_ASCII'] = False
    allowed_origins = _build_allowed_origins(app)
    CORS(
        app,
        resources={
            r"/api/*": {"origins": allowed_origins},
            # /_uploads 影像/结果图要求登录，GeoView(:3000) 前端跨源下载需带 Cookie，
            # 不配 credentials CORS 会被浏览器拦截（2026-09-20 审查 P1 下载回归）
            r"/_uploads/*": {"origins": allowed_origins},
        },
        supports_credentials=True,
    )

    return app
