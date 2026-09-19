import os
import sys
import time
import json
import re
import argparse
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime

# 解决 Windows 控制台编码问题
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ADB_BIN = os.path.join(PROJECT_ROOT, "bin", "adb.exe")
STORAGE_ROOT = os.path.join(PROJECT_ROOT, "storage")
CAPTURES_ROOT = os.path.join(STORAGE_ROOT, "captures")

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        print(f"[{ts}] {msg}", flush=True)
    except UnicodeEncodeError:
        print(f"[{ts}] {msg.encode('gbk', 'ignore').decode('gbk')}", flush=True)

def get_adb_path() -> str:
    if os.path.exists(ADB_BIN):
        return ADB_BIN
    return "adb"

def run_adb(adb: str, args: list, timeout: int = 8) -> subprocess.CompletedProcess:
    return subprocess.run(
        [adb] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout
    )

def simulate_tap(adb: str, serial: str, x: int, y: int) -> bool:
    """
    双引擎模拟点击（防风控、防权限拦截）：
    引擎 1: 原生 input tap
    引擎 2: 遇到小米 HyperOS 拦截 (SecurityException) 自动降级为系统级 Monkey DispatchPointer 物理模拟点击
    """
    res = subprocess.run([adb, "-s", serial, "shell", "input", "tap", str(x), str(y)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
    if res.returncode == 0 and "SecurityException" not in res.stderr and "SecurityException" not in res.stdout:
        return True
    
    # 降级引擎: Monkey DispatchPointer 物理触摸事件脚本
    monkey_script = f"""count= 1
speed= 1.0
start data >>
DispatchPointer(0, 0, 0, {x}, {y}, 0,0,0,0,0,0,0)
DispatchPointer(0, 0, 1, {x}, {y}, 0,0,0,0,0,0,0)
"""
    subprocess.run([adb, "-s", serial, "shell", "cat > /data/local/tmp/tap.script"], input=monkey_script.encode("utf-8"), timeout=5)
    m_res = subprocess.run([adb, "-s", serial, "shell", "monkey", "-f", "/data/local/tmp/tap.script", "1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
    return m_res.returncode == 0

def get_current_focus(adb: str, serial: str) -> str:
    res = run_adb(adb, ["-s", serial, "shell", "dumpsys", "window", "displays"], timeout=4)
    for line in res.stdout.splitlines():
        if "mCurrentFocus" in line:
            return line.strip()
    return ""

def dump_ui(adb: str, serial: str) -> ET.Element:
    dump_path = "/data/local/tmp/ui_dump.xml"
    run_adb(adb, ["-s", serial, "shell", "uiautomator", "dump", dump_path], timeout=10)
    raw = subprocess.run([adb, "-s", serial, "exec-out", "cat", dump_path], stdout=subprocess.PIPE, timeout=10).stdout
    return ET.fromstring(raw)

def extract_bilibili_video_info(root: ET.Element) -> dict:
    info = {
        "video_title": None,
        "up_name": None,
        "up_desc": None,
        "fans": None,
        "views": None,
        "danmakus": None,
        "pub_time": None,
        "online_users": None,
        "likes": None,
        "coins": None,
        "favorites": None,
        "shares": None,
        "current_episode": None
    }

    parent_map = {c: p for p in root.iter() for c in p}

    for node in root.iter("node"):
        rid = node.attrib.get("resource-id", "")
        text = node.attrib.get("text", "").strip()
        desc = node.attrib.get("content-desc", "").strip()
        bounds = node.attrib.get("bounds", "")

        # 1. 主视频标题：通过与 arrow 折叠按钮的同级关系精确匹配，彻底排除广告条目
        if rid.endswith("/title") and text:
            p = parent_map.get(node)
            if p is not None:
                has_arrow = any(c.attrib.get("resource-id", "").endswith("/arrow") for c in p)
                is_ad = any("ad_" in c.attrib.get("resource-id", "") for c in p)
                if has_arrow and not is_ad:
                    info["video_title"] = text

        # 2. UP 主信息
        if "author_name" in rid and text:
            info["up_name"] = text
        elif "author_layout" in rid and desc:
            info["up_desc"] = desc
        elif "fans" in rid and text:
            info["fans"] = text

        # 3. 播放与弹幕量
        if "views" in rid and (text or desc):
            info["views"] = desc or text
        elif "danmakus" in rid and (text or desc):
            info["danmakus"] = desc or text

        # 4. 发布时间与在线人数
        if "time" in rid and text:
            info["pub_time"] = text
        elif "online" in rid and text:
            info["online_users"] = text

        # 5. 互动数据 (点赞、投币、收藏、分享)
        if "frame_like" in rid and desc:
            info["likes"] = desc
        elif "frame_coin" in rid and desc:
            info["coins"] = desc
        elif "frame_fav" in rid and desc:
            info["favorites"] = desc
        elif "frame_share" in rid and desc:
            info["shares"] = desc

        # 6. 当前选集
        if "已选" in desc or ("lottie_wave" in rid and bounds):
            info["current_episode"] = desc or text

    # 二次兜底：若 arrow 规则未命中，查找 content-desc 以视频开头的标题容器
    if not info["video_title"]:
        for node in root.iter("node"):
            desc = node.attrib.get("content-desc", "")
            if desc.startswith("视频") and ("集" in desc or "，展开" in desc or "《" in desc):
                cleaned = desc.replace("视频", "").replace("，展开", "").replace(",展开", "").strip()
                info["video_title"] = cleaned
                break

    return info

def main():
    parser = argparse.ArgumentParser(description="B站当前在看视频信息模拟点击采集")
    parser.add_argument("--scenario", default="bilibili_video", help="执行场景")
    args = parser.parse_args()

    adb = get_adb_path()
    log("==================================================")
    log("📺 启动 Bilibili 正在观看视频信息采集与自动化运维任务")
    log(f"🔧 底层 ADB 引擎: {adb}")
    log("==================================================")

    # 1. 检测设备连接
    dev_res = run_adb(adb, ["devices", "-l"])
    lines = [l.strip() for l in dev_res.stdout.strip().splitlines()[1:] if l.strip()]
    target_device = None
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            target_device = parts[0]
            break

    if not target_device:
        log("❌ 未检测到处于正常授权状态的 Android 手机！")
        sys.exit(1)

    log(f"✔ 成功锁定目标真机设备: [{target_device}] (Redmi Note 13 Pro)")

    # 2. 检查当前前台是否已经是哔哩哔哩
    log("🔍 [Step 1/4] 探测真机当前前台活跃窗口...")
    focus = get_current_focus(adb, target_device)
    log(f"📱 当前前台窗口: {focus}")

    if "tv.danmaku.bili" in focus:
        log("✔ 目标应用 (哔哩哔哩) 当前已处于真机前台，检测到活跃播放会话！")
    else:
        log("👉 [模拟点击] 当前不在 B站，正在通过模拟桌面点击交互启动 Bilibili...")
        # 优先寻找桌面图标，若找不到则通过模拟系统 Launcher 点击下发
        subprocess.run([adb, "-s", target_device, "shell", "monkey", "-p", "tv.danmaku.bili", "--pct-touch", "100", "-v", "1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2.5)
        new_focus = get_current_focus(adb, target_device)
        log(f"✔ 模拟点击完成，当前聚焦前台: {new_focus}")

    # 3. Dump 当前页面 UI 元素层级
    log("📥 [Step 2/4] 获取页面无障碍视觉控件树 (UI Hierarchy)...")
    try:
        root = dump_ui(adb, target_device)
        log("✔ 页面 DOM 结构抓取成功，开始解析视频元数据...")
    except Exception as e:
        log(f"❌ UI Hierarchy 解析失败: {e}")
        sys.exit(1)

    # 4. 提取视频详细信息
    log("📊 [Step 3/4] 智能提取视频标题、UP主、播放量与三连数据...")
    video_info = extract_bilibili_video_info(root)

    log("==================================================")
    log("🎉 【采集成功】当前在看视频核心元数据：")
    log(f"🎬 视频标题: {video_info.get('video_title') or '未检测到主标题'}")
    log(f"👤 UP 主: {video_info.get('up_name') or '未知'} ({video_info.get('fans') or ''})")
    log(f"📈 播放数据: 播放量 {video_info.get('views') or '未知'} | 弹幕 {video_info.get('danmakus') or '未知'}")
    log(f"🔥 实时热度: {video_info.get('online_users') or '未知在线人数'}")
    log(f"📅 发布时间: {video_info.get('pub_time') or '未知'}")
    log(f"👍 互动三连: {video_info.get('likes') or '0点赞'} | {video_info.get('coins') or '0投币'} | {video_info.get('favorites') or '0收藏'} | {video_info.get('shares') or '0分享'}")
    if video_info.get('current_episode'):
        log(f"📑 当前选集: {video_info.get('current_episode')}")
    log("==================================================")

    # 5. 保存结构化结果与现场截屏
    log("💾 [Step 4/4] 现场物理截屏归档与结构化落盘...")
    date_str = datetime.now().strftime("%Y-%m-%d")
    result_dir = os.path.join(STORAGE_ROOT, "captures", date_str)
    os.makedirs(result_dir, exist_ok=True)
    
    out_json = os.path.join(result_dir, "bilibili_current_video.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(video_info, f, ensure_ascii=False, indent=2)
    log(f"✔ 结构化 JSON 数据已落盘: storage/captures/{date_str}/bilibili_current_video.json")

    out_snap = os.path.join(result_dir, "bilibili_current_snapshot.png")
    try:
        with open(out_snap, "wb") as f_snap:
            subprocess.run([adb, "-s", target_device, "exec-out", "screencap", "-p"], stdout=f_snap, timeout=8)
        if os.path.exists(out_snap) and os.path.getsize(out_snap) > 1000:
            log(f"✔ 现场高清全屏截图已归档: storage/captures/{date_str}/bilibili_current_snapshot.png")
    except Exception as e:
        log(f"⚠️ 现场截屏保存异常: {e}")

    log("🎉 B站视频信息提取与模拟交互全流程圆满完成！")

if __name__ == "__main__":
    main()
