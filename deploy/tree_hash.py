#!/usr/bin/env python3
"""目录指纹：宿主机生成、核对发布目录时用，容器内核对实际挂载进去的代码时也用，两边是同一份脚本、同一个算法。

用法：
    python tree_hash.py [--exclude 相对路径]... [--stat] <根目录> <相对路径> [<相对路径> ...]
    --exclude   目录树里跳过的路径：命名卷和运行时资产的挂载点（宿主机的发布目录里是空的，容器里被卷盖住
                或是真的权重），两边都跳过，指纹才比得上
    --stat      只数不哈希，给容器内核对运行时资产用（权重有 9.7GB，每次重算太慢）

输出（标准输出，一行 JSON），默认模式：
    {"<相对路径>": {"files": 文件数, "bytes": 字节数, "sha256": "指纹"}, ...}
--stat 模式：
    {"<相对路径>": {"kind": "file"|"dir", "files": 文件数, "bytes": 字节数, "mtime": 文件最后修改的秒时间戳}, ...}

指纹算法：相对路径（正斜杠）按码位排序，逐个文件取内容的 sha256，
         把 "<相对路径>\\0<十六进制摘要>\\n" 依次拼接，再对整段取 sha256。
         文件集合、文件名、文件内容任一不同，指纹就不同。
         文件名里的私有区字符 U+F03A 按冒号算（见 fingerprint_name）。
始终跳过：__pycache__ 目录与 *.pyc 文件（只读挂载下不会生成，别的环境里可能生成）。
路径不存在或是空目录时直接报错退出，不会算出一个看起来正常的"空指纹"。
"""
import argparse
import hashlib
import json
import os
import sys

CHUNK = 1 << 20
ALGORITHM = 2   # 指纹算法的版本：算法变了就加一，宿主机上按旧算法算出来的资产指纹缓存随之作废


def fingerprint_name(relative):
    """指纹里用的相对路径。Windows 上 MSYS / Cygwin 把名字里的冒号记成私有区字符 U+F03A
    （下载标记 "x.pth:Zone.Identifier" 就是真实文件，名字里带的是 U+F03A），Docker Desktop 挂进容器后又是普通的冒号。
    宿主机生成、容器内核对用的是同一份脚本：名字都按容器里看到的算，两边的指纹才对得上。"""
    return relative.replace("\uf03a", ":")


def relative_parts(relative):
    parts = relative.replace("\\", "/").split("/")
    if not relative or relative.startswith(("/", "\\")) or ".." in parts or "." in parts:
        raise ValueError("非法相对路径: %r" % relative)
    return parts


def walk_files(root, relative, exclude):
    """返回 [(相对路径, 绝对路径)]；单个文件就是它自己。"""
    parts = relative_parts(relative)
    target = os.path.join(root, *parts)
    if os.path.islink(target):
        raise ValueError("不能是符号链接: %r" % relative)
    if os.path.isfile(target):
        return [(relative, target)]
    if not os.path.isdir(target):
        raise FileNotFoundError("%r 在 %s 下不存在" % (relative, root))
    files = []
    for directory, subdirectories, names in os.walk(target):
        subdirectories[:] = sorted(name for name in subdirectories if name != "__pycache__")
        for name in names:
            if name.endswith(".pyc"):
                continue
            full = os.path.join(directory, name)
            name_relative = fingerprint_name(os.path.relpath(full, root).replace(os.sep, "/"))
            if any(name_relative == item or name_relative.startswith(item + "/") for item in exclude):
                continue   # 被排除的路径本身，或它目录下的文件
            files.append((name_relative, full))
    if not files:
        raise ValueError("空目录: %r" % relative)
    return sorted(files)


def tree_fingerprint(root, relative, exclude=frozenset()):
    combined = hashlib.sha256()
    total = 0
    files = walk_files(root, relative, exclude)
    for name, full in files:
        digest = hashlib.sha256()
        with open(full, "rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK), b""):
                digest.update(chunk)
        total += os.path.getsize(full)
        combined.update(name.encode("utf-8") + b"\0" + digest.hexdigest().encode("ascii") + b"\n")
    return {"files": len(files), "bytes": total, "sha256": combined.hexdigest()}


def stat_path(root, relative):
    """不读内容：文件给大小和修改时间，目录给文件数和总字节数。"""
    parts = relative_parts(relative)
    target = os.path.join(root, *parts)
    if os.path.isfile(target):
        status = os.stat(target)
        return {"kind": "file", "files": 1, "bytes": status.st_size, "mtime": int(status.st_mtime)}
    files = walk_files(root, relative, frozenset())
    return {"kind": "dir", "files": len(files), "bytes": sum(os.path.getsize(full) for _, full in files)}


def main(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--stat", action="store_true")
    parser.add_argument("root")
    parser.add_argument("paths", nargs="+")
    arguments = parser.parse_args(argv[1:])
    exclude = frozenset(item.replace("\\", "/") for item in arguments.exclude)
    if arguments.stat:
        result = {relative: stat_path(arguments.root, relative) for relative in arguments.paths}
    else:
        result = {relative: tree_fingerprint(arguments.root, relative, exclude) for relative in arguments.paths}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main(sys.argv)
    except (OSError, ValueError) as error:
        print("tree_hash: %s" % error, file=sys.stderr)
        sys.exit(2)
