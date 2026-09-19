import os

import pymysql
import sqlparse
from dotenv import dotenv_values

_dotenv = dotenv_values('.flaskenv')


def _config(key: str, default: str) -> str:
    """Read configuration value from environment first, then .flaskenv, fallback to default."""
    return os.getenv(key) or _dotenv.get(key) or default


# MySql配置信息
HOST = _config('MYSQL_HOST', '127.0.0.1')
PORT = int(_config('MYSQL_PORT', 3306))
DATABASE = _config('MYSQL_DATABASE', 'AdminFlask')
USERNAME = _config('MYSQL_USERNAME', 'root')
# 不再提供 '123456' 弱口令默认值：缺省时跳过初始化连接并告警，
# 避免脱离 compose 直跑时静默用 root/123456 连库（应用侧 SQLAlchemy 用
# 空口令会在首次访问时报错，错误路径一致且明确）
PASSWORD = _config('MYSQL_PASSWORD', '')


def is_exist_database():
    db = pymysql.connect(
        host=HOST,
        port=PORT,
        user=USERNAME,
        password=PASSWORD,
        charset='utf8mb4')
    cursor = db.cursor()
    sql = "SELECT COUNT(DISTINCT `TABLE_NAME`) AS anyAliasName FROM `INFORMATION_SCHEMA`.`COLUMNS` WHERE `table_schema` = '%s';" % DATABASE
    res = cursor.execute(sql)
    results = cursor.fetchall()
    db.close()
    return results


def init_database():
    db = pymysql.connect(
        host=HOST,
        port=PORT,
        user=USERNAME,
        password=PASSWORD,
        charset='utf8mb4')
    cursor = db.cursor()
    sql = "CREATE DATABASE IF NOT EXISTS %s CHARSET=utf8 COLLATE=utf8_general_ci;" % DATABASE
    res = cursor.execute(sql)
    db.close()
    return res


def execute_fromfile(filename):
    db = pymysql.connect(
        host=HOST,
        port=PORT,
        user=USERNAME,
        password=PASSWORD,
        database=DATABASE,
        charset='utf8')
    fd = open(filename, 'r', encoding='utf-8')
    cursor = db.cursor()
    sqlfile = fd.read()
    sqlfile = sqlparse.format(sqlfile, strip_comments=True).strip()

    sqlcommamds = sqlfile.split(';')

    failures = []
    for command in sqlcommamds:
        statement = command.strip()
        if not statement:
            continue
        try:
            cursor.execute(statement)
            db.commit()

        except Exception as msg:
            db.rollback()
            failures.append((statement, msg))
    db.close()

    if failures:
        # 初始化 SQL 失败不允许被静默吞掉：逐条打印 SQL 片段与异常，
        # 并汇总抛错阻止“表创建成功”这类误导性的完成提示。
        for statement, msg in failures:
            snippet = ' '.join(statement.split())[:120]
            print('SQL 执行失败: %s\n异常: %s' % (snippet, msg))
        raise RuntimeError(
            'init_db 有 %d 条 SQL 执行失败，数据库初始化未完成' % len(failures))


def init_db():
    if not PASSWORD:
        print('[init_db] MYSQL_PASSWORD 未配置，跳过数据库初始化连接（不再回退默认口令）')
        return
    if is_exist_database()[0][0] > 0:
        print('数据库%s不为空，不进行初始化操作' % str(DATABASE))
        return
    if init_database():
        print('数据库%s创建成功' % str(DATABASE))
    execute_fromfile('init_db.sql')
    print('表创建成功')
