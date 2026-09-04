import json
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent


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
    def test_worker_app_cold_start_only_loads_inference_database_dependencies(self):
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

            unwanted_modules = [
                "applications.api",
                "applications.common.scripts",
                "applications.extensions.init_dotenv",
                "applications.extensions.init_upload",
                "flask_cors",
                "flask_marshmallow",
                "applications.models.analysis",
                "applications.models.admin_user",
                "applications.models.photo",
                "applications.models.project",
            ]
            print(json.dumps({
                "database_uri": app.config["SQLALCHEMY_DATABASE_URI"],
                "endpoints": sorted(app.view_functions),
                "loaded_unwanted_modules": [
                    name for name in unwanted_modules if name in sys.modules
                ],
                "tables": tables,
            }))
            """,
            env_updates={"FLASK_CONFIG": "testing"},
        )

        result = parse_json_output(process)
        self.assertEqual("sqlite:///:memory:", result["database_uri"])
        self.assertEqual(["static"], result["endpoints"])
        self.assertEqual([], result["loaded_unwanted_modules"])
        self.assertEqual(
            ["inference_jobs", "inference_worker_states"],
            result["tables"],
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
