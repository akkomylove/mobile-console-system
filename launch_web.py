# -*- coding: utf-8 -*-
"""
手机端流程控制台
一键启动网页与后端服务管控工具 (launch_web.py)
"""

import os
import sys
import time
import socket
import subprocess
import webbrowser
import signal
import threading
import urllib.request
import urllib.error
import argparse

if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
SERVER_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"

def print_banner():
    print("\n" + "=" * 62)
    print("               手机端流程控制台 —— 流程调度与监控中枢")
    print("                 【一键启动与网页秒开工具】")
    print("=" * 62 + "\n")

def check_dependencies():
    """检查核心 Python 依赖项"""
    required = ["fastapi", "uvicorn", "requests", "pydantic"]
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    
    if missing:
        print(f"[提示] 检测到缺失依赖包: {', '.join(missing)}，正在自动安装...")
        req_file = os.path.join(PROJECT_ROOT, "requirements.txt")
        pip_cmd = [
            sys.executable, "-m", "pip", "install",
            "-r", req_file,
            "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"
        ]
        try:
            subprocess.run(pip_cmd, check=True)
            print("[成功] 依赖安装完成！\n")
        except Exception as e:
            print(f"[警告] 自动安装依赖遇到问题: {e}")
            print("请尝试手动运行: pip install -r requirements.txt")

def is_service_healthy(url: str, timeout: float = 1.0) -> bool:
    """检测本系统服务是否已经在正常响应 HTTP 请求"""
    try:
        req = urllib.request.Request(f"{url}/api/dashboard/stats", headers={"User-Agent": "HealthChecker"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        # 也尝试访问根路径
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "HealthChecker"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status in (200, 304)
        except Exception:
            return False

def is_port_in_use(host: str, port: int) -> bool:
    """检测指定端口是否已被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0

def create_desktop_shortcut(name: str = "一键启动手机端流程控制台.lnk") -> bool:
    """在 Windows 桌面自动创建一键启动快捷方式"""
    if sys.platform != "win32":
        return False
    
    desktop = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Desktop")
    if not os.path.exists(desktop):
        return False

    shortcut_path = os.path.join(desktop, name)
    target_bat = os.path.join(PROJECT_ROOT, "一键启动网页.bat")
    
    vbs_file = os.path.join(PROJECT_ROOT, "_temp_shortcut.vbs")
    vbs_content = (
        'Set ws = CreateObject("WScript.Shell")\r\n'
        f'Set s = ws.CreateShortcut("{shortcut_path}")\r\n'
        f's.TargetPath = "{target_bat}"\r\n'
        f's.WorkingDirectory = "{PROJECT_ROOT}"\r\n'
        's.IconLocation = "shell32.dll,14"\r\n'
        's.Description = "手机端流程控制台 自动化运维与流程管控一键启动"\r\n'
        's.WindowStyle = 1\r\n'
        's.Save\r\n'
    )
    
    try:
        with open(vbs_file, "w", encoding="ansi") as f:
            f.write(vbs_content)
        subprocess.run(["cscript", "//nologo", vbs_file], capture_output=True, timeout=5)
        return os.path.exists(shortcut_path)
    except Exception as e:
        return False
    finally:
        if os.path.exists(vbs_file):
            try:
                os.remove(vbs_file)
            except Exception:
                pass

def wait_and_open_browser(url: str, timeout: float = 15.0, open_browser: bool = True):
    """等待服务健康检查就绪，然后秒开浏览器"""
    start_time = time.time()
    print("[2/3] 正在等待 Web 监控服务就绪", end="", flush=True)
    ready = False
    while time.time() - start_time < timeout:
        if is_service_healthy(url, timeout=0.8):
            ready = True
            break
        print(".", end="", flush=True)
        time.sleep(0.3)
    
    print("")
    if ready:
        print("[3/3] [就绪] 服务健康检查通过，系统已完全就绪！")
        if open_browser:
            print(f"[打开] 正在自动为您唤起浏览器打开看板: {url}")
            webbrowser.open(url)
        else:
            print(f"[就绪] 服务访问地址: {url}")
    else:
        print(f"[警告] 服务等待超时({timeout}s)，但进程仍在运行，请手动访问: {url}")
        if open_browser:
            webbrowser.open(url)

def interactive_loop(server_proc, url: str):
    """交互监听循环，支持终端快捷命令与平滑停止"""
    print("\n" + "-" * 62)
    print(" [服务运行中] 快捷指令:")
    print("   * 输入 [o] 并回车 : 重新在浏览器中打开监控看板网页")
    print("   * 输入 [c] 并回车 : 重新在桌面创建一键启动快捷方式")
    print("   * 输入 [q] 并回车 或 按 Ctrl+C : 安全停止服务并退出")
    print("-" * 62 + "\n")

    if not sys.stdin or not hasattr(sys.stdin, "isatty") or not sys.stdin.isatty():
        # 非交互式终端（如后台服务模式），挂起等待子进程生命周期
        try:
            server_proc.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        return

    while True:
        try:
            cmd = sys.stdin.readline()
            if not cmd:
                server_proc.wait()
                break
            cmd = cmd.strip().lower()
            if cmd == "o":
                print(f"[打开] 正在打开浏览器: {url}")
                webbrowser.open(url)
            elif cmd == "c":
                if create_desktop_shortcut():
                    print("[成功] 桌面快捷方式已更新！")
                else:
                    print("[错误] 创建桌面快捷方式失败")
            elif cmd == "q":
                print("[正在退出] 正在停止后台监控服务...")
                break
        except (KeyboardInterrupt, EOFError):
            print("\n[正在退出] 收到退出信号，正在停止后台服务...")
            break

def main():
    parser = argparse.ArgumentParser(description="手机端流程控制台 一键启动器")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--create-shortcut", action="store_true", help="仅在桌面创建快捷方式后退出")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"监听主机 (默认: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口 (默认: {DEFAULT_PORT})")
    args = parser.parse_args()

    print_banner()

    # 快捷方式创建指令
    if args.create_shortcut:
        ok = create_desktop_shortcut()
        if ok:
            print("[成功] 桌面一键启动快捷方式创建成功！")
        else:
            print("[错误] 桌面快捷方式创建失败，请检查权限。")
        return

    url = f"http://{args.host}:{args.port}"

    # 检查桌面快捷方式，若不存在则顺便创建
    try:
        desktop = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Desktop")
        shortcut = os.path.join(desktop, "一键启动手机端流程控制台.lnk")
        if not os.path.exists(shortcut):
            if create_desktop_shortcut():
                print("[提示] 已为您在桌面自动生成「一键启动手机端流程控制台」快捷方式！\n")
    except Exception:
        pass

    # [场景1] 服务已经在线
    if is_service_healthy(url):
        print(f"[提示] 检测到监控服务已经在后台稳定运行 ({url})！")
        print("[打开] 无需重复启动，正在为您直接唤醒浏览器打开看板网页...")
        if not args.no_browser:
            webbrowser.open(url)
        print("\n看板网页已开启。您可以直接在浏览器中操作。")
        time.sleep(1.5)
        return

    # [场景2] 端口被其他非本系统程序占用
    if is_port_in_use(args.host, args.port):
        print(f"[警告] 端口 {args.port} 已被占用，但未响应监控服务健康检查。")
        print(f"请检查是否有残留进程，或在浏览器中尝试访问: {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return

    # [场景3] 正常启动服务
    print("[1/3] 检查 Python 环境及依赖完整性...")
    check_dependencies()

    print(f"[*] 启动后台 FastAPI 服务 (http://{args.host}:{args.port})...")
    
    server_cmd = [
        sys.executable, "-m", "uvicorn",
        "backend.server:app",
        "--host", args.host,
        "--port", str(args.port),
        "--log-level", "info",
        "--reload"
    ]
    
    # 在当前控制台启动子进程
    server_proc = subprocess.Popen(server_cmd, cwd=PROJECT_ROOT)

    def cleanup(signum=None, frame=None):
        if server_proc and server_proc.poll() is None:
            print("\n正在优雅停止 Uvicorn 进程...")
            server_proc.terminate()
            try:
                server_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server_proc.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        # 等待服务就绪并打开浏览器
        wait_and_open_browser(url, timeout=15.0, open_browser=not args.no_browser)
        
        # 终端交互循环
        interactive_loop(server_proc, url)
    finally:
        cleanup()

if __name__ == "__main__":
    main()
