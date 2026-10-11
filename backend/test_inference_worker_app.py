import json
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent

WORKER_AUDIT_IMPORTS = """
import importlib.abc
import json
import os
import sys
import tempfile
from pathlib import Path

# 完整 Web 测试环境也必须遵守轻量 Worker 的依赖边界。
class DenyWebSchemas(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = ("applications.schemas", "marshmallow", "flask_marshmallow")
        if any(fullname == name or fullname.startswith(name + ".") for name in forbidden):
            raise ModuleNotFoundError("Worker 不应加载 Web schema: " + fullname)

sys.meta_path.insert(0, DenyWebSchemas())
from applications.inference.app import create_worker_app
from applications.extensions import db
from applications.models.project import Project, ProjectActivityLog, ProjectMineBinding
"""


def run_isolated_python(source, env_updates=None, remove_env=()):
    env = os.environ.copy()
    env.update(env_updates or {})
    for name in remove_env:
        env.pop(name, None)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def parse_json_output(process):
    if process.returncode != 0:
        raise AssertionError(
            f"isolated Python failed with exit code {process.returncode}\n"
            f"stdout:\n{process.stdout}\nstderr:\n{process.stderr}"
        )
    return json.loads(process.stdout.strip().splitlines()[-1])


class TestInferenceWorkerApp(unittest.TestCase):
    def test_project_publication_records_activity_without_web_schema_dependencies(self):
        process = run_isolated_python(WORKER_AUDIT_IMPORTS + textwrap.dedent("""
            import cv2
            import numpy as np
            from applications.project_hub.inference_results import publish_project_inference_result

            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                os.environ["PROJECT_STORAGE_ROOT"] = str(root / "storage")
                app = create_worker_app("testing")
                with app.app_context():
                    project = Project(name="Worker 发布活动测试", status="active")
                    db.session.add(project)
                    db.session.flush()
                    db.session.add(ProjectMineBinding(project_id=project.id, mine_fid=17))
                    db.session.commit()
                    staged = root / "staged" / "17"
                    staged.mkdir(parents=True)
                    image = np.full((4, 4, 3), 120, dtype=np.uint8)
                    assert cv2.imwrite(str(staged / "17+2022.png"), image)
                    assert cv2.imwrite(str(staged / "17+2022_src.png"), image)
                    assert cv2.imwrite(str(staged / "17+2022_mask.png"), np.zeros((4, 4), dtype=np.uint8))
                    published = publish_project_inference_result(project.id, {
                        "year": "2022", "mine_fids": [17],
                        "inference_job_id": "worker-success",
                        "output_root": str(root / "storage" / "projects" / str(project.id) / "outputs" / "inference"),
                    }, {"written_fid_list": [17], "output_root": str(staged.parent)})
                    db.session.expire_all()
                    rows = ProjectActivityLog.query.filter_by(project_id=project.id, event_type="inference_result_published").all()
                    print(json.dumps({
                        "synced_fids": published["synced_fids"],
                        "published_file_exists": (root / "storage" / "projects" / str(project.id) / "outputs" / "inference" / "17" / "17+2022.png").is_file(),
                        "events": [{"actor": row.actor, "payload": json.loads(row.payload_json)} for row in rows],
                        "web_schema_loaded": any(name.startswith("applications.schemas") for name in sys.modules),
                    }))
        """))
        result = parse_json_output(process)
        self.assertEqual([17], result["synced_fids"])
        self.assertTrue(result["published_file_exists"])
        self.assertFalse(result["web_schema_loaded"])
        self.assertEqual(1, len(result["events"]))
        event = result["events"][0]
        self.assertEqual("system", event["actor"])
        self.assertEqual("worker-success", event["payload"]["inference_job_id"])
        self.assertEqual([17], event["payload"]["mine_fids"])
        self.assertEqual("success", event["payload"]["result"])

    def test_worker_failure_records_activity_without_web_schema_dependencies(self):
        process = run_isolated_python(WORKER_AUDIT_IMPORTS + textwrap.dedent("""
            from types import SimpleNamespace
            from applications.inference.worker import InferenceWorker

            app = create_worker_app("testing")
            with app.app_context():
                project = Project(name="Worker 失败活动测试", status="active")
                db.session.add(project)
                db.session.commit()
                worker = InferenceWorker()
                worker._record_project_failure_audit({
                    "project_id": project.id, "mine_fids": [17], "year": "2022",
                }, SimpleNamespace(id="worker-failed"), "INFERENCE_FAILED", "可控测试错误")
                db.session.expire_all()
                rows = ProjectActivityLog.query.filter_by(project_id=project.id, event_type="inference_failed").all()
                print(json.dumps({
                    "events": [{"actor": row.actor, "payload": json.loads(row.payload_json)} for row in rows],
                    "web_schema_loaded": any(name.startswith("applications.schemas") for name in sys.modules),
                }))
        """))
        result = parse_json_output(process)
        self.assertFalse(result["web_schema_loaded"])
        self.assertEqual(1, len(result["events"]))
        event = result["events"][0]
        self.assertEqual("system", event["actor"])
        self.assertEqual("worker-failed", event["payload"]["inference_job_id"])
        self.assertEqual("INFERENCE_FAILED", event["payload"]["error_code"])
        self.assertEqual("可控测试错误", event["payload"]["error_message"])
        self.assertEqual("failure", event["payload"]["result"])

    def test_worker_app_cold_start_creates_project_result_database_dependencies(self):
        process = run_isolated_python(
            """
            import json
            import sys

            from sqlalchemy import inspect

            from applications.inference.app import create_worker_app
            from applications.extensions import db

            app = create_worker_app()
            with app.app_context():
                tables = sorted(inspect(db.engine).get_table_names())

            print(json.dumps({
                "database_uri": app.config["SQLALCHEMY_DATABASE_URI"],
                "endpoints": sorted(app.view_functions),
                "loaded_result_model": "applications.models.classification_result" in sys.modules,
                "tables": tables,
            }))
            """,
            env_updates={"FLASK_CONFIG": "testing"},
        )

        result = parse_json_output(process)
        self.assertEqual("sqlite:///:memory:", result["database_uri"])
        self.assertEqual(["static"], result["endpoints"])
        self.assertTrue(result["loaded_result_model"])
        self.assertTrue(
            {
                "classification_results",
                "classification_revisions",
                "classification_edit_audits",
                "inference_jobs",
                "inference_worker_states",
            }.issubset(result["tables"]),
        )

    def test_extension_function_exports_are_callable_before_submodule_imports(self):
        process = run_isolated_python(
            """
            import json

            from applications.extensions import init_dotenv, init_upload
            from applications.extensions.init_dotenv import init_dotenv as dot_env_function
            from applications.extensions.init_upload import init_upload as upload_function

            print(json.dumps({
                "dot_env_callable": callable(init_dotenv),
                "dot_env_identical": init_dotenv is dot_env_function,
                "upload_callable": callable(init_upload),
                "upload_identical": init_upload is upload_function,
            }))
            """
        )

        result = parse_json_output(process)
        self.assertEqual(
            {
                "dot_env_callable": True,
                "dot_env_identical": True,
                "upload_callable": True,
                "upload_identical": True,
            },
            result,
        )

    def test_extension_function_exports_survive_submodule_first_import_order(self):
        process = run_isolated_python(
            """
            import importlib
            import json

            dot_env_module = importlib.import_module("applications.extensions.init_dotenv")
            upload_module = importlib.import_module("applications.extensions.init_upload")
            from applications.extensions import init_dotenv, init_upload

            print(json.dumps({
                "dot_env_callable": callable(init_dotenv),
                "dot_env_identical": init_dotenv is dot_env_module.init_dotenv,
                "upload_callable": callable(init_upload),
                "upload_identical": init_upload is upload_module.init_upload,
            }))
            """
        )

        result = parse_json_output(process)
        self.assertEqual(
            {
                "dot_env_callable": True,
                "dot_env_identical": True,
                "upload_callable": True,
                "upload_identical": True,
            },
            result,
        )

    def test_web_app_still_loads_all_models_and_registers_all_api_blueprints(self):
        process = run_isolated_python(
            """
            import json
            import sys

            from sqlalchemy import inspect

            from applications import create_app
            from applications.extensions import db

            app = create_app("testing")
            with app.app_context():
                tables = sorted(inspect(db.engine).get_table_names())

            model_modules = [
                "applications.models.analysis",
                "applications.models.admin_user",
                "applications.models.photo",
                "applications.models.project",
                "applications.models.inference_job",
            ]
            print(json.dumps({
                "blueprints": sorted(app.blueprints),
                "loaded_model_modules": [
                    name for name in model_modules if name in sys.modules
                ],
                "tables": tables,
            }))
            """,
            env_updates={"FLASK_CONFIG": "testing"},
        )

        result = parse_json_output(process)
        self.assertEqual(
            {
                "analysis_api",
                "auth_api",
                "file_api",
                "history_api",
                "inference_api",
                "model_api",
                "project_api",
                "stats_api",
                "_uploads",
            },
            set(result["blueprints"]),
        )
        self.assertEqual(
            {
                "applications.models.analysis",
                "applications.models.admin_user",
                "applications.models.photo",
                "applications.models.project",
                "applications.models.inference_job",
            },
            set(result["loaded_model_modules"]),
        )
        self.assertTrue(
            {
                "admin_user",
                "analysis",
                "photo",
                "project",
                "inference_jobs",
                "inference_worker_states",
            }.issubset(result["tables"])
        )

    def test_worker_app_requires_secret_key_in_production(self):
        process = run_isolated_python(
            """
            from applications.inference.app import create_worker_app

            try:
                create_worker_app("production")
            except RuntimeError as exc:
                if str(exc) != "SECRET_KEY is required in production":
                    raise
            else:
                raise AssertionError("production worker app accepted a missing SECRET_KEY")
            """,
            remove_env=("SECRET_KEY",),
        )
        self.assertEqual(0, process.returncode, process.stderr)


if __name__ == "__main__":
    unittest.main()
