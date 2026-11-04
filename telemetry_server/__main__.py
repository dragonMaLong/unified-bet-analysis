import argparse
import json
import sys

from .app import create_app, purge_database


def main():
    parser = argparse.ArgumentParser(description="BET 使用统计服务维护命令")
    parser.add_argument("command", choices=("purge",), help="维护 SQLite WAL；长期保留事件和 IP，不按时间删除")
    parser.parse_args()
    app = create_app()
    result = purge_database(app.config["DATABASE"])
    print(json.dumps(result, ensure_ascii=False))
    if result["checkpoint_busy"]:
        # A busy WAL checkpoint must be visible to the maintenance job monitor.
        sys.exit(1)


if __name__ == "__main__":
    main()
