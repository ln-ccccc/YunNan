import logging
import os
from urllib.parse import quote_plus


def _bool_env(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class BaseConfig:
    SYSTEM_NAME = os.getenv('SYSTEM_NAME', 'Admin')
    # 主题面板的链接列表配置
    SYSTEM_PANEL_LINKS = []

    UPLOADED_PHOTOS_DEST = 'static/upload'
    UPLOADED_FILES_ALLOW = ['gif', 'jpg', 'png']

    # JSON配置
    JSON_AS_ASCII = False

    SECRET_KEY = os.getenv('SECRET_KEY') or 'dev key'
    ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '')
    FRONTEND_PORT = int(os.getenv('FRONTEND_PORT') or 3000)
    MINER_FRONTEND_PORT = int(os.getenv('MINER_FRONTEND_PORT') or 4000)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = _bool_env('SESSION_COOKIE_SECURE', False)
    CORS_ALLOWED_ORIGINS = os.getenv('CORS_ALLOWED_ORIGINS', '')

    # 推理 Worker：默认能用 NVIDIA CUDA 则使用，否则显式回退 CPU。
    INFERENCE_ACCELERATOR = os.getenv('INFERENCE_ACCELERATOR', 'auto')
    INFERENCE_CPU_FALLBACK = _bool_env('INFERENCE_CPU_FALLBACK', True)
    INFERENCE_GPU_DEVICE = int(os.getenv('INFERENCE_GPU_DEVICE') or 0)
    INFERENCE_MAX_CONCURRENCY = int(os.getenv('INFERENCE_MAX_CONCURRENCY') or 1)
    INFERENCE_JOB_TIMEOUT_SECONDS = int(os.getenv('INFERENCE_JOB_TIMEOUT_SECONDS') or 3600)
    # 单次前向的瓦片批量（GPU 上 >1 可摊薄固定开销；显存随批线性增加）
    INFERENCE_BATCH_SIZE = os.environ.get("INFERENCE_BATCH_SIZE", "1")
    INFERENCE_KEEP_FAILED_WORKDIR = _bool_env('INFERENCE_KEEP_FAILED_WORKDIR', True)
    INFERENCE_INPUT_ROOTS = os.getenv('INFERENCE_INPUT_ROOTS', '')
    INFERENCE_OUTPUT_ROOTS = os.getenv('INFERENCE_OUTPUT_ROOTS', '')
    INFERENCE_RUNTIME_ROOT = os.getenv('INFERENCE_RUNTIME_ROOT', '')

    # redis配置
    REDIS_HOST = os.getenv('REDIS_HOST') or "127.0.0.1"
    REDIS_PORT = int(os.getenv('REDIS_PORT') or 6379)

    # mysql 配置
    MYSQL_USERNAME = os.getenv('MYSQL_USERNAME') or "root"
    MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', '')
    MYSQL_HOST = os.getenv('MYSQL_HOST') or "127.0.0.1"
    MYSQL_PORT = int(os.getenv('MYSQL_PORT') or 3306)
    MYSQL_DATABASE = os.getenv('MYSQL_DATABASE') or "AdminFlask"

    # mysql 数据库的配置信息
    SQLALCHEMY_DATABASE_URI = f"mysql+pymysql://{MYSQL_USERNAME}:{quote_plus(MYSQL_PASSWORD)}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
    # 默认日志等级
    LOG_LEVEL = logging.WARN
    #
    MAIL_SERVER = os.getenv('MAIL_SERVER') or 'smtp.qq.com'
    MAIL_USE_TLS = False
    MAIL_USE_SSL = True
    MAIL_PORT = 465
    MAIL_USERNAME = os.getenv('MAIL_USERNAME') or '123@qq.com'
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD') or 'XXXXX'  # 生成的授权码
    # 默认发件人的邮箱,这里填写和MAIL_USERNAME一致即可
    MAIL_DEFAULT_SENDER = ('admin', os.getenv('MAIL_USERNAME') or '123@qq.com')


class TestingConfig(BaseConfig):
    """ 测试配置 """
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'  # 内存数据库
    TESTING = True


class DevelopmentConfig(BaseConfig):
    """ 开发配置 """
    SQLALCHEMY_TRACK_MODIFICATIONS = True
    SQLALCHEMY_ECHO = False


class ProductionConfig(BaseConfig):
    """生成环境配置"""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False
    # 8 秒回收会造成连接池持续重建 churn（每次低频访问都重做 TCP+认证）；
    # 常规做法是 3600（须小于 MySQL wait_timeout）
    SQLALCHEMY_POOL_RECYCLE = 3600

    LOG_LEVEL = logging.ERROR


config = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig
}
