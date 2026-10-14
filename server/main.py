import argparse
import os
from pathlib import Path

import platform_registry
from app import create_server


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description="小型公司仓库管理系统")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=None)
    parser.add_argument(
        "--base-path",
        default=None,
        help="Application path prefix, for example /acme",
    )
    parser.add_argument(
        "--platform",
        action="store_true",
        help="多公司平台模式：单进程按 Host 前缀分发公司数据库，console 前缀进控制台",
    )
    parser.add_argument(
        "--platform-data",
        default=None,
        help="平台数据目录（默认 <项目>/data），platform.json 与公司库都在其中",
    )
    parser.add_argument(
        "--domain",
        default=None,
        help="平台根域名，例如 wms.tangzheheshui.cn；也可用环境变量 WMS_PLATFORM_DOMAIN",
    )
    args = parser.parse_args()

    if args.platform:
        data_dir = Path(
            args.platform_data or os.environ.get("WMS_PLATFORM_DATA")
            or PROJECT_ROOT / "data"
        )
        registry = platform_registry.PlatformRegistry(
            data_dir / "platform.json"
        )
        domain = (
            args.domain or os.environ.get("WMS_PLATFORM_DOMAIN") or ""
        )
        server = create_server(
            args.host, args.port, None, base_path="/",
            registry=registry, domain=domain,
        )
        print(
            f"Warehouse platform: http://{args.host}:{args.port} "
            f"(domain={domain or 'any'}, data={data_dir})"
        )
    else:
        server = create_server(
            args.host,
            args.port,
            args.db,
            args.base_path or os.environ.get("WMS_BASE_PATH", "/"),
        )
        print(f"Warehouse server: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
