import argparse
import os

from app import create_server


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
    args = parser.parse_args()
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
