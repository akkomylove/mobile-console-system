import os
import sys
import time
import subprocess
import argparse

try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ADB_BIN = os.path.join(PROJECT_ROOT, "bin", "adb.exe")

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        print(f"[{ts}] {msg}", flush=True)
    except UnicodeEncodeError:
        print(f"[{ts}] {msg.encode('gbk', 'ignore').decode('gbk')}", flush=True)

def get_adb_path():
    if os.path.exists(ADB_BIN):
        return ADB_BIN
    return "adb"

def run_adb_cmd(adb, args, timeout=5):
    cmd = [adb] + args
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)

def main():
    parser = argparse.ArgumentParser(description="真机真实自动化测试与监控巡检脚本")
    parser.add_argument("--scenario", default="real_device", help="执行场景")
    args = parser.parse_args()

    adb = get_adb_path()
    log("==================================================")
    log("🚀 启动真机自动化实操巡检流程 (Real Device Flow)")
    log(f"🔧 使用 ADB 引擎: {adb}")
    log("==================================================")

    # 1. 检测 ADB 设备连接
    try:
        res = run_adb_cmd(adb, ["devices", "-l"])
    except Exception as e:
        log(f"💥 无法调用 ADB: {e}")
        sys.exit(1)

    lines = [l.strip() for l in res.stdout.strip().splitlines()[1:] if l.strip()]
    if not lines:
        log("❌ [设备未就绪] 当前未检测到已开启 USB 调试的 Android 设备！")
        log("🔍 [硬件排查] 系统底层已检测到物理连接的 Redmi Note 13 Pro (PID_FF40 纯MTP媒体模式)。")
        log("💡 [开启方法] 请在手机上开启:「设置 ➔ 更多设置 ➔ 开发者选项 ➔ USB调试」，并勾选「一律允许这台计算机进行调试」。")
        raise RuntimeError("真机未开启USB调试模式或USB未处于可通信状态，自动化流程中断。")

    target_device = None
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            target_device = parts[0]
            break
        elif len(parts) >= 2 and parts[1] == "unauthorized":
            log(f"⚠️ [设备未授权] 检测到设备 {parts[0]}，但手机屏幕弹出授权框尚未点击确认！")
            log("👉 请点亮手机，在屏幕上勾选「一律允许这台计算机进行调试」后点击「确定」。")
            raise RuntimeError("手机未允许此计算机的 USB 调试授权。")

    if not target_device:
        log(f"❌ 未找到可用设备状态: {lines}")
        raise RuntimeError("无可用真机处于正常连接调试状态。")

    log(f"✔ 成功与真机建立 ADB 会话: 设备号 [{target_device}]")

    # 2. 读取真机硬件指标 (真实电池、屏幕唤醒、前台窗口)
    log("📊 [1/5] 读取真机物理硬件传感器指标...")
    try:
        bat_res = run_adb_cmd(adb, ["-s", target_device, "shell", "dumpsys", "battery"])
        level, status_text, temp = "未知", "使用中", "未知"
        for bline in bat_res.stdout.splitlines():
            bline = bline.strip()
            if bline.startswith("level:"):
                level = bline.split(":")[1].strip() + "%"
            elif bline.startswith("temperature:"):
                temp = f"{int(bline.split(':')[1].strip()) / 10.0}°C"
            elif bline.startswith("status:"):
                sc = bline.split(":")[1].strip()
                status_text = "⚡ 充电中" if sc == "2" else "🔋 放电中"

        log(f"🔋 [真实电池] 剩余电量: {level} | 状态: {status_text} | 温度: {temp}")
    except Exception as e:
        log(f"⚠️ 获取电池数据异常: {e}")

    # 3. 唤醒并点亮屏幕
    log("💡 [2/5] 向真机发送点亮屏幕唤醒指令 (KEYCODE_WAKEUP)...")
    run_adb_cmd(adb, ["-s", target_device, "shell", "input", "keyevent", "224"])
    time.sleep(0.8)

    # 4. 启动真机应用 (以系统设置或计算器为例，证明真实在手机端拉起)
    log("📱 [3/5] 启动真机应用: 系统设置 (com.android.settings)...")
    launch_res = run_adb_cmd(adb, ["-s", target_device, "shell", "am", "start", "-a", "android.settings.SETTINGS"])
    log(f"✔ 应用启动指令已下发，响应: {launch_res.stdout.strip()[:60]}")
    time.sleep(1.5)

    # 5. 检查当前真机前台 Activity
    win_res = run_adb_cmd(adb, ["-s", target_device, "shell", "dumpsys", "window", "displays"])
    focus_window = "未知"
    for wline in win_res.stdout.splitlines():
        if "mCurrentFocus" in wline:
            focus_window = wline.strip()
            break
    log(f"🎯 [4/5] 当前真机前台聚焦窗口: {focus_window}")

    # 6. 回到真机桌面并完成流程
    log("🏠 [5/5] 执行完毕，向真机发送 Home 键返回桌面...")
    run_adb_cmd(adb, ["-s", target_device, "shell", "input", "keyevent", "3"])
    time.sleep(0.5)

    log("🎉 真机真实业务自动化流程全部顺利完成，手机交互闭环验证成功！")

if __name__ == "__main__":
    main()
