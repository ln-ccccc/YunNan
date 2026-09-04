import os
from pathlib import Path

import yaml


CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/app/config.yaml"))


def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def client_host(host):
    return "127.0.0.1" if host == "0.0.0.0" else host


def write_text(path, content):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def main():
    cfg = load_config()
    host_cfg = cfg.get("host", {})
    port_cfg = cfg.get("port", {})
    miner_cfg = cfg.get("miner", {})

    backend_host = host_cfg.get("backend", "0.0.0.0")
    backend_port = int(port_cfg.get("backend", 5008))
    frontend_port = int(port_cfg.get("frontend", 3000))
    miner_enabled = "true" if miner_cfg.get("enabled", False) else "false"
    miner_frontend_port = int(miner_cfg.get("frontend_port", 4000))
    miner_backend_port = int(miner_cfg.get("backend_port", 8000))

    write_text(
        "/app/frontend/.env",
        "\n".join(
            [
                f"VUE_APP_BACKEND_PORT = {backend_port}",
                f"VUE_APP_BACKEND_IP = {client_host(backend_host)}",
                f"VUE_APP_MINER_ENABLED = {miner_enabled}",
                f"VUE_APP_MINER_URL = http://localhost:{miner_frontend_port}",
                "",
            ]
        ),
    )

    miner_api_base_url = os.environ.get("VITE_MINER_API_BASE_URL", "")
    geoview_url = os.environ.get(
        "VITE_GEOVIEW_URL",
        f"http://localhost:{frontend_port}/#/segmentation",
    )
    local_max_native_zoom = (
        os.environ.get("VITE_MINER_LOCAL_MAX_NATIVE_ZOOM")
        or os.environ.get("MINER_LOCAL_MAX_NATIVE_ZOOM")
        or os.environ.get("MINER_TILE_MAX_ZOOM")
        or "13"
    )
    miner_env = [
        f'VITE_GEOVIEW_URL="{geoview_url}"',
        f"VITE_MINER_API_BASE_URL={miner_api_base_url}",
        f"VITE_MINER_MAP_PROVIDER={os.environ.get('MINER_MAP_PROVIDER', 'local')}",
        f"VITE_TDT_KEY={os.environ.get('MINER_TDT_KEY', '')}",
        f"VITE_MINER_LOCAL_TILE_URL={os.environ.get('MINER_LOCAL_TILE_URL', f'http://localhost:{miner_backend_port}/tiles/{{z}}/{{x}}/{{y}}.png')}",
        f"VITE_MINER_LOCAL_TMS={os.environ.get('MINER_LOCAL_TMS', '0')}",
        f"VITE_MINER_LOCAL_MAX_NATIVE_ZOOM={local_max_native_zoom}",
        "",
    ]
    write_text("/app/miner/.env", "\n".join(miner_env))


if __name__ == "__main__":
    main()
