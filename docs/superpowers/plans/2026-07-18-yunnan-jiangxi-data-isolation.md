# 云南与江西运行资源隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将云南部署从江西遗留的镜像标签和 `geoview_*` 数据卷中完整隔离，在干净云南数据库中初始化 565 座云南矿山，并让绑定矿山列表可滚动浏览。

**Architecture:** 使用新的 `yunnan-*` 镜像/容器标签和 `yunnan_*` 数据卷启动同一套服务，旧江西资源只保留不挂载。后端启动 Gunicorn 前执行一次幂等云南项目初始化：只有数据库中不存在活动项目时才从 `yunnan.kml` 创建 565 条绑定；已有项目时不覆盖用户后续编辑。Miner 仅增加列表滚动样式，不改变 API 和数据结构。

**Tech Stack:** Docker Compose、Flask/SQLAlchemy、Python `unittest`、Vue 3/Vite、Node Test Runner、MySQL 8

---

## 文件结构

- Create: `backend/applications/project_hub/yunnan_seed.py` — 解析并校验云南 KML，初始化单一云南项目。
- Create: `backend/seed_yunnan_project.py` — 生产启动时调用的命令行入口。
- Create: `backend/test_yunnan_project_seed.py` — 项目初始化、幂等和防混库回归测试。
- Modify: `docker/start-backend.sh` — Gunicorn 启动前执行云南项目初始化。
- Modify: `docker-compose.prod.yml` — 云南镜像默认值、容器名、数据卷名和后端健康检查。
- Modify: `.env` — 当前部署的应用镜像切换到云南专用标签；保留所有密钥原值。
- Modify: `image_bundle.env` — 离线镜像标签与包名改为云南专用名称。
- Modify: `miner/src/components/ProjectWorkspace.vue` — 绑定矿山列表增加限高和纵向滚动。
- Modify: `docs/offline_deployment_guide.md` — 同步云南镜像、卷名与江西旧资源保留说明。

当前目录不是有效 Git 仓库，各任务不执行 commit；改动通过精确文件检查、测试与运行态验证交付。

### Task 1: 云南项目初始化服务

**Files:**
- Create: `backend/test_yunnan_project_seed.py`
- Create: `backend/applications/project_hub/yunnan_seed.py`

- [ ] **Step 1: 编写失败测试**

创建 `backend/test_yunnan_project_seed.py`：

```python
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.project import Project
from applications.project_hub.yunnan_seed import seed_yunnan_project


class TestYunnanProjectSeed(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.temp_dir = Path(tempfile.mkdtemp(prefix="yunnan-seed-"))
        self.kml_path = self.temp_dir / "yunnan.kml"

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_kml(self, province="云南省"):
        self.kml_path.write_text(
            f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>矿山 101</name><ExtendedData><SchemaData schemaUrl="#yunnan">
    <SimpleData name="FID_1">101</SimpleData><SimpleData name="SHENG">{province}</SimpleData>
    <SimpleData name="SHI">大理白族自治州</SimpleData><SimpleData name="TBTYMJ">12.5</SimpleData>
  </SchemaData></ExtendedData></Placemark>
  <Placemark><name>矿山 102</name><ExtendedData><SchemaData schemaUrl="#yunnan">
    <SimpleData name="FID_1">102</SimpleData><SimpleData name="SHENG">{province}</SimpleData>
    <SimpleData name="SHI">大理白族自治州</SimpleData><SimpleData name="HFZLQK">已治理</SimpleData>
  </SchemaData></ExtendedData></Placemark>
</Document></kml>''',
            encoding="utf-8",
        )

    def test_seed_creates_one_yunnan_project_with_all_kml_mines(self):
        self._write_kml()
        first = seed_yunnan_project(self.kml_path, expected_count=2, manager="admin")
        second = seed_yunnan_project(self.kml_path, expected_count=2, manager="admin")

        project = Project.query.one()
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(project.name, "云南矿山生态修复监测项目")
        self.assertEqual(project.region, "云南省")
        self.assertEqual(project.monitor_start_year, 2017)
        self.assertEqual(project.monitor_end_year, 2025)
        self.assertEqual(len(project.mines), 2)
        self.assertEqual(project.mines[0].city_snapshot, "大理白族自治州")
        self.assertEqual(project.mines[0].area_snapshot, 12.5)

    def test_seed_rejects_foreign_project_database(self):
        self._write_kml()
        db.session.add(Project(name="江西项目", region="江西省", status="active"))
        db.session.commit()

        with self.assertRaisesRegex(RuntimeError, "存在非云南项目"):
            seed_yunnan_project(self.kml_path, expected_count=2)

        self.assertEqual(Project.query.count(), 1)

    def test_seed_rejects_non_yunnan_kml_and_wrong_count(self):
        self._write_kml(province="江西省")
        with self.assertRaisesRegex(RuntimeError, "非云南矿山"):
            seed_yunnan_project(self.kml_path, expected_count=2)

        self._write_kml()
        with self.assertRaisesRegex(RuntimeError, "预期 565"):
            seed_yunnan_project(self.kml_path, expected_count=565)
        self.assertEqual(Project.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
python -m unittest test_yunnan_project_seed.py -v
```

Working directory: `backend`

Expected: FAIL，提示 `applications.project_hub.yunnan_seed` 不存在。

- [ ] **Step 3: 实现最小初始化服务**

创建 `backend/applications/project_hub/yunnan_seed.py`：

```python
import json
from pathlib import Path
from xml.etree import ElementTree as ET

from applications.extensions import db
from applications.models.project import Project, ProjectActivityLog, ProjectMineBinding


KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}
PROJECT_NAME = "云南矿山生态修复监测项目"
PROJECT_REGION = "云南省"
PROJECT_REMARK = "system_seed:yunnan_kml"
EXPECTED_MINE_COUNT = 565
MAX_DB_INT = 2147483647


def _to_int(value):
    try:
        result = int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        return None
    return result if 0 < result <= MAX_DB_INT else None


def _to_float(value):
    try:
        return float(str(value or "").strip())
    except (TypeError, ValueError):
        return None


def _load_yunnan_mines(kml_path):
    path = Path(kml_path)
    if not path.is_file():
        raise RuntimeError(f"云南 KML 不存在: {path}")

    mines = {}
    foreign_provinces = set()
    root = ET.parse(path).getroot()
    for placemark in root.findall(".//kml:Placemark", KML_NS):
        fields = {}
        for item in placemark.findall(".//kml:SimpleData", KML_NS):
            fields[str(item.attrib.get("name") or "").strip()] = "".join(item.itertext()).strip()

        province = fields.get("SHENG", "").strip()
        if province and province != PROJECT_REGION:
            foreign_provinces.add(province)
            continue

        fid = _to_int(fields.get("FID_1") or placemark.findtext("kml:name", default="", namespaces=KML_NS))
        if fid is None:
            continue

        name = (
            fields.get("GGKSMC")
            or fields.get("SBKSMC")
            or fields.get("ZLKSMC")
            or placemark.findtext("kml:name", default="", namespaces=KML_NS).strip()
            or f"矿山 {fid}"
        )
        mines[fid] = {
            "mine_fid": fid,
            "mine_name_snapshot": name,
            "city_snapshot": fields.get("SHI") or fields.get("SHI_1") or "",
            "area_snapshot": _to_float(fields.get("TBTYMJ_1") or fields.get("TBTYMJ") or fields.get("SHAPE_Area")),
            "status_snapshot": fields.get("HFZLQK") or fields.get("ZLHFZLQK") or "",
        }

    if foreign_provinces:
        values = "、".join(sorted(foreign_provinces))
        raise RuntimeError(f"云南 KML 中发现非云南矿山: {values}")
    return [mines[fid] for fid in sorted(mines)]


def seed_yunnan_project(kml_path, expected_count=EXPECTED_MINE_COUNT, manager="admin"):
    mines = _load_yunnan_mines(kml_path)
    if len(mines) != expected_count:
        raise RuntimeError(f"云南矿山数量不正确: 预期 {expected_count}，实际 {len(mines)}")

    foreign_count = Project.query.filter(
        Project.deleted_at.is_(None),
        Project.name != PROJECT_NAME,
    ).count()
    if foreign_count:
        raise RuntimeError("云南数据库中存在非云南项目，拒绝混合初始化")

    project = Project.query.filter_by(name=PROJECT_NAME, deleted_at=None).first()
    if project is not None:
        if project.region != PROJECT_REGION:
            raise RuntimeError("云南项目区域不是云南省，拒绝继续启动")
        return {"created": False, "project_id": project.id, "mine_count": len(project.mines)}

    project = Project(
        name=PROJECT_NAME,
        region=PROJECT_REGION,
        manager=manager or "admin",
        remark=PROJECT_REMARK,
        status="active",
        monitor_start_year=2017,
        monitor_end_year=2025,
    )
    db.session.add(project)
    db.session.flush()
    for sort_order, item in enumerate(mines, start=1):
        db.session.add(ProjectMineBinding(project_id=project.id, sort_order=sort_order, **item))

    db.session.add(ProjectActivityLog(
        project_id=project.id,
        event_type="yunnan_project_seeded",
        actor=manager or "admin",
        payload_json=json.dumps({"mine_count": len(mines), "source": "yunnan.kml"}, ensure_ascii=False),
    ))
    db.session.commit()
    return {"created": True, "project_id": project.id, "mine_count": len(mines)}
```

- [ ] **Step 4: 运行新增测试并确认 GREEN**

Run:

```powershell
python -m unittest test_yunnan_project_seed.py -v
```

Expected: 3 tests, all PASS.

- [ ] **Step 5: 运行项目后端相关回归测试**

Run:

```powershell
python -m unittest test_project_api.py test_legacy_project_migration.py test_yunnan_project_seed.py -v
```

Expected: all PASS；若 `test_legacy_project_migration.py` 暴露存量 `_to_float` 错误，只修复该函数缩进，使 `try/except` 回到 `_to_float` 内，不做其他迁移逻辑修改。

### Task 2: 将初始化接入后端生产启动

**Files:**
- Create: `backend/seed_yunnan_project.py`
- Modify: `docker/start-backend.sh`

- [ ] **Step 1: 创建命令行入口**

创建 `backend/seed_yunnan_project.py`：

```python
import argparse
import json
import os
from pathlib import Path

from applications import create_app
from applications.project_hub.yunnan_seed import EXPECTED_MINE_COUNT, seed_yunnan_project


def main():
    default_kml = Path(__file__).resolve().parents[1] / "miner" / "yunnan.kml"
    parser = argparse.ArgumentParser(description="Initialize the isolated Yunnan project database.")
    parser.add_argument("--kml-path", default=str(default_kml))
    parser.add_argument("--expected-count", type=int, default=EXPECTED_MINE_COUNT)
    parser.add_argument("--manager", default=os.getenv("ADMIN_USERNAME", "admin"))
    args = parser.parse_args()

    app = create_app(os.getenv("FLASK_CONFIG", "development"))
    with app.app_context():
        result = seed_yunnan_project(
            Path(args.kml_path),
            expected_count=args.expected_count,
            manager=args.manager,
        )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 在 Gunicorn 前执行初始化**

修改 `docker/start-backend.sh`，在 `cd /app/backend` 和 `exec gunicorn` 之间加入：

```bash
python /app/backend/seed_yunnan_project.py \
  --kml-path /app/miner/yunnan.kml \
  --expected-count 565
```

初始化失败时依靠现有 `set -euo pipefail` 阻止后端启动，避免带着混合数据库继续运行。

- [ ] **Step 3: 校验脚本语法**

Run:

```powershell
python -m py_compile backend/seed_yunnan_project.py backend/applications/project_hub/yunnan_seed.py
bash -n docker/start-backend.sh
```

Expected: exit 0, no output. 如果当前 Windows 环境没有 `bash`，在后续容器内运行 `bash -n /app/docker/start-backend.sh` 并记录本机未执行项。

### Task 3: 让绑定矿山列表可滚动

**Files:**
- Modify: `miner/src/components/ProjectWorkspace.vue:699`

- [ ] **Step 1: 用浏览器记录 RED 状态**

登录 Miner 项目工作台，在浏览器中读取 `.mine-list`：

```javascript
const element = document.querySelector('.mine-list');
({
  overflowY: getComputedStyle(element).overflowY,
  clientHeight: element.clientHeight,
  scrollHeight: element.scrollHeight,
});
```

Expected before fix: `overflowY` 不是 `auto`，列表高度未被限制。

- [ ] **Step 2: 添加最小滚动样式**

在 `ProjectWorkspace.vue` 的现有 `.mine-list` 公共列表规则之后增加：

```css
.mine-list {
  max-height: min(60vh, 640px);
  overflow-y: auto;
  padding-right: 6px;
  scrollbar-gutter: stable;
}
```

- [ ] **Step 3: 构建并验证 GREEN**

Run:

```powershell
npm test
npm run build
```

Working directory: `miner`

Expected: 31+ tests PASS and Vite build exits 0（测试总数可因新增测试增加）。部署后浏览器计算样式应为 `overflowY: "auto"`，且 565 条记录下 `scrollHeight > clientHeight`。

### Task 4: 隔离云南镜像、容器和数据卷

**Files:**
- Modify: `docker-compose.prod.yml`
- Modify: `.env`
- Modify: `image_bundle.env`
- Modify: `docs/offline_deployment_guide.md`

- [ ] **Step 1: 保存旧资源只读清单**

Run:

```powershell
docker image inspect geoview-jiangxi:gpu geoview-runtime:split-clean geoview-inference-worker:current --format '{{json .RepoTags}}|{{.Id}}'
docker volume inspect geoview_mysql_data geoview_backend_static geoview_miner_outputs geoview_miner_uploads --format '{{.Name}}|{{.Mountpoint}}'
```

Expected: all resources exist。将输出用于切换后比对，不执行删除命令。

- [ ] **Step 2: 修改 Compose 资源名称**

在 `docker-compose.prod.yml` 做以下精确替换：

```yaml
x-runtime-base: &runtime-base
  image: ${APP_IMAGE:-yunnan-runtime:current}
```

容器名：

```yaml
container_name: yunnan-backend
container_name: yunnan-frontend
container_name: yunnan-miner-api
container_name: yunnan-inference-worker
container_name: yunnan-miner-web
container_name: yunnan-mysql
```

推理镜像默认值：

```yaml
image: ${INFERENCE_IMAGE:-yunnan-inference-worker:current}
```

后端健康检查：

```yaml
test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:5008/api/auth/session >/dev/null || exit 1"]
```

卷的 `name` 值：

```yaml
name: yunnan_backend_static
name: yunnan_mysql_data
name: yunnan_hf_cache
name: yunnan_miner_outputs
name: yunnan_miner_tiles
name: yunnan_miner_uploads
name: yunnan_inference_runtime
```

- [ ] **Step 3: 修改镜像环境配置**

仅替换 `.env` 第一行，不读取或改写其他密钥行：

```dotenv
APP_IMAGE=yunnan-runtime:current
```

将 `image_bundle.env` 更新为：

```dotenv
APP_IMAGE=yunnan-runtime:current
MYSQL_IMAGE=registry.openanolis.cn/openanolis/mysql:8.0.30-8.6
INFERENCE_IMAGE=yunnan-inference-worker:current
APP_IMAGE_TAR=yunnan_runtime_current.tar
MYSQL_IMAGE_TAR=mysql_8.0.30-8.6.tar
INFERENCE_IMAGE_TAR=yunnan_inference_worker_current.tar
HOTFIX_SRC_DIR=./hotfix_src
MINER_MAP_PROVIDER=gaode
```

- [ ] **Step 4: 同步离线部署文档**

在 `docs/offline_deployment_guide.md` 的镜像和数据卷章节明确记录：

```text
云南应用镜像使用 yunnan-runtime:current，推理镜像使用 yunnan-inference-worker:current。
云南运行数据只使用 yunnan_* 数据卷。旧 geoview_* 数据卷和 geoview-jiangxi:gpu 属于保留的江西环境，不得挂载到云南服务，也不得在云南部署过程中删除。
```

保持原有命令结构，只替换与镜像/卷名直接相关的示例。

- [ ] **Step 5: 验证 Compose 展开结果**

以当前 MySQL 容器环境变量安全注入密码，运行：

```powershell
docker compose --env-file .env --env-file image_bundle.env -f docker-compose.prod.yml -f docker-compose.gpu.yml config
```

Expected:

- 六个容器名均为 `yunnan-*`。
- 应用镜像为 `yunnan-runtime:current`，推理镜像为 `yunnan-inference-worker:current`。
- 命名卷均为 `yunnan_*`。
- 展开结果中不再出现 `cugrs-` 容器名和 `geoview_*` 卷名。

### Task 5: 切换运行环境并初始化云南数据

**Files:**
- Runtime only; no source files.

- [ ] **Step 1: 为已验证镜像增加云南标签**

Run:

```powershell
docker tag geoview-runtime:split-clean yunnan-runtime:current
docker tag geoview-inference-worker:current yunnan-inference-worker:current
```

Expected: `docker image inspect` 显示两个云南标签存在，镜像 ID 分别与来源标签相同。江西标签保持不变。

- [ ] **Step 2: 使用新卷重建云南服务**

从 `cugrs-mysql` 的容器环境读取当前 MySQL 密码到进程变量，禁止打印；然后执行：

```powershell
docker compose --env-file .env --env-file image_bundle.env `
  -f docker-compose.prod.yml -f docker-compose.gpu.yml `
  up -d --force-recreate
```

Expected: 旧 `cugrs-*` 服务容器被同一 Compose 项目的 `yunnan-*` 容器替代；不使用 `down -v`，不删除任何旧卷。

- [ ] **Step 3: 等待并检查健康状态**

轮询最多 90 秒：

```powershell
docker inspect yunnan-mysql yunnan-backend yunnan-frontend yunnan-miner-api yunnan-miner-web yunnan-inference-worker `
  --format '{{.Name}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}'
```

Expected: all `running`；有健康检查的容器为 `healthy`。推理 worker 无健康检查时通过日志中的模型加载成功信息和进程运行状态验证。

- [ ] **Step 4: 验证云南项目数据**

读取 `.env` 管理员账号/密码到变量但不打印，经 `http://localhost:4000/api/auth/login` 登录并复用会话访问：

```text
GET http://localhost:4000/api/projects
GET http://localhost:4000/api/projects/<returned-id>
GET http://localhost:4000/api/geojson
```

Expected:

- 项目列表只有 `云南矿山生态修复监测项目`。
- `region` 为 `云南省`，`remark` 为 `system_seed:yunnan_kml`。
- `monitor_start_year` 为 2017，`monitor_end_year` 为 2025。
- 项目 `mine_count` 与详情 `mines.length` 均为 565。
- GeoJSON `features.length` 为 565。
- 项目详情和绑定快照中不含“江西”。

- [ ] **Step 5: 验证旧江西资源未变化**

再次运行 Task 4 Step 1 的 `docker image inspect` 和 `docker volume inspect`。

Expected: `geoview-jiangxi:gpu` 与旧 `geoview_*` 卷仍存在，ID/挂载路径与切换前一致；云南容器的 `.Mounts` 中只出现 `yunnan_*` 命名卷和明确的云南工作区 bind mount。

### Task 6: 端到端界面与最终回归

**Files:**
- Verification only.

- [ ] **Step 1: 浏览器验证项目工作台**

访问 `http://localhost:4000/#/projects` 并登录。验证页面可见文本：

- `云南矿山生态修复监测项目`
- `云南省`
- `矿山 565 座`
- `2017 - 2025`

验证页面不存在 `江西矿山生态修复监测项目`、`江西省` 和 `system_seed:jiangxi_mine_csv`。

- [ ] **Step 2: 浏览器验证滚动列表**

读取 `.mine-list` 计算样式和尺寸：

```javascript
const list = document.querySelector('.mine-list');
({
  overflowY: getComputedStyle(list).overflowY,
  clientHeight: list.clientHeight,
  scrollHeight: list.scrollHeight,
  optionCount: list.querySelectorAll('.mine-item').length,
});
```

Expected: `overflowY === "auto"`、`scrollHeight > clientHeight`、`optionCount === 565`。滚动到末尾后最后一条矿山仍可见，勾选状态和“定位地图”按钮可正常操作。

- [ ] **Step 3: 运行最终测试与构建**

Run:

```powershell
python -m unittest test_project_api.py test_legacy_project_migration.py test_yunnan_project_seed.py -v
```

Working directory: `backend`

Run:

```powershell
npm test
npm run build
```

Working directory: `miner`

Run:

```powershell
npm run build
```

Working directory: `frontend`

Expected: all commands exit 0。记录存量包体积和 Browserslist 警告，但不在本任务中升级依赖或重构打包。

- [ ] **Step 4: 最终范围检查**

确认只修改计划列出的代码、Compose、环境镜像标签和文档；确认没有删除 `geoview_*` 卷、江西镜像、用户文件或历史成果。由于无 Git 元数据，用精确文件清单、Docker inspect 和测试输出代替 diff/commit 证据。
