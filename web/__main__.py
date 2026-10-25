"""Web 服务入口：python -m web [--host 127.0.0.1 --port 8000 --model ...]"""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m web", description="DeepPulse Web UI 服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机，无鉴权勿暴露公网）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument("--model", default=None, help="覆盖 setting.json 的 llm.model（仅本次进程生效）")
    args = parser.parse_args()

    setting = None
    if args.model:
        from web.service.settings_store import load_setting_file

        setting = load_setting_file()
        setting.setdefault("llm", {})["model"] = args.model

    from web.app import create_app

    app = create_app(setting=setting)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
