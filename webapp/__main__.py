"""DeepPulse Web 平台一键启动 — python -m webapp"""

import argparse
import atexit
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).parent.parent

# 颜色
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"


def print_banner():
    print(f"""
{CYAN}{BOLD}╔══════════════════════════════════════════════════╗
║           DeepPulse 智能分析平台                  ║
║       A 股短线分析 AI Agent — Web 模式            ║
╚══════════════════════════════════════════════════╝{RESET}
""")


def _stream_output(proc: subprocess.Popen, prefix: str):
    """后台线程：实时打印子进程输出"""
    import threading

    def reader():
        for line in proc.stdout:
            text = line.decode("utf-8", errors="replace").rstrip()
            print(f"{prefix}{text}{RESET}")

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    return thread


def start_backend(host: str, port: int) -> subprocess.Popen:
    """启动 FastAPI 后端"""
    print(f"{GREEN}🚀 启动 DeepPulse 服务...{RESET}")

    # 确保项目根目录在 PYTHONPATH
    env = os.environ.copy()
    python_path = str(ROOT)
    if "PYTHONPATH" in env:
        python_path = python_path + os.pathsep + env["PYTHONPATH"]
    env["PYTHONPATH"] = python_path
    env["DEEPPULSE_WEB_MODE"] = "1"  # 标记 Web 模式，跳过本地文件生成

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "web.app.main:app",
            "--host",
            host,
            "--port",
            str(port),
            "--log-level",
            "info",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    _stream_output(proc, f"{CYAN}[服务] ")
    return proc


def wait_for_port(port: int, timeout: int = 30) -> bool:
    """等待端口可用"""
    import socket

    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def main():
    parser = argparse.ArgumentParser(description="DeepPulse 智能分析平台")
    parser.add_argument("--port", type=int, default=8000, help="服务端口 (默认: 8000)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="监听地址 (默认: 127.0.0.1)")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    print_banner()

    # 管理子进程
    processes: list[subprocess.Popen] = []

    def cleanup():
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        print(f"\n{YELLOW}👋 DeepPulse 已停止{RESET}")

    atexit.register(cleanup)

    def signal_handler(sig, frame):
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 启动后端
    backend_proc = start_backend(args.host, args.port)
    processes.append(backend_proc)

    print(f"{CYAN}   等待服务启动...{RESET}")
    if wait_for_port(args.port):
        print(f"{GREEN}✅ DeepPulse 已启动{RESET}")
        print()
        print(f"{GREEN}   🌐 访问地址: http://localhost:{args.port}{RESET}")
        print(f"{GREEN}   📖 API 文档: http://localhost:{args.port}/docs{RESET}")

        # 自动打开浏览器
        if not args.no_open:
            time.sleep(1)
            webbrowser.open(f"http://localhost:{args.port}")
    else:
        print(f"{RED}❌ 服务启动超时{RESET}")
        cleanup()
        sys.exit(1)

    print(f"""
{CYAN}{BOLD}══════════════════════════════════════════════════
  DeepPulse 运行中，按 Ctrl+C 停止
══════════════════════════════════════════════════{RESET}
""")

    # 等待子进程
    try:
        while True:
            for proc in processes[:]:
                if proc.poll() is not None:
                    print(f"{RED}⚠️  进程退出 (code={proc.returncode}){RESET}")
                    processes.remove(proc)
            if not processes:
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
