import os

from flask import Flask


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
    if config_name == 'production' and not os.getenv('SECRET_KEY'):
        raise RuntimeError('SECRET_KEY is required in production')

    models.load_all_models()
    init_plugs(app)

    with app.app_context():
        db.create_all()
        from applications.runtime_schema import ensure_runtime_schema
        ensure_runtime_schema()

    if config_name != 'testing':
        init_script(app)

    system_api(app)

    app.config['SESSION_TYPE'] = 'filesystem'
    app.config['MAX_CONTENT_LENGTH'] = 60 * 1024 * 1024
    app.config['JSON_AS_ASCII'] = False
    CORS(
        app,
        resources={r"/api/*": {"origins": _build_allowed_origins(app)}},
        supports_credentials=True,
    )

    return app
