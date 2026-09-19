import os
import sys
import subprocess
import shutil
import re
import time
import threading
from datetime import datetime
from typing import Dict, Any, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE_ROOT = os.path.join(PROJECT_ROOT, "storage")
CAPTURES_ROOT = os.path.join(STORAGE_ROOT, "captures")
BIN_DIR = os.path.join(PROJECT_ROOT, "bin")

os.makedirs(CAPTURES_ROOT, exist_ok=True)
os.makedirs(BIN_DIR, exist_ok=True)

class DeviceManager:
    def __init__(self):
        # 优先使用项目自带免安装便携版 ADB，其次查找系统 PATH
        bundled_adb = os.path.join(BIN_DIR, "adb.exe")
        if os.path.exists(bundled_adb):
            self.adb_path = bundled_adb
        else:
            self.adb_path = shutil.which("adb")
            
        self.default_device_name = "Redmi Note 13 Pro"
        self.default_serial = "A05B2A6C"
        self.adb_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._cached_info = None
        self._cached_time = 0.0
        self.CACHE_TTL = 2.5  # 状态缓存 2.5 秒，避免高频并发轮询竞争 ADB
        self._is_script_running = False

    def set_script_running(self, busy: bool):
        """标记当前是否有自动化任务在独占运行，避免遥测并发争抢 ADB 通道"""
        self._is_script_running = busy

    def get_adb_path(self) -> Optional[str]:
        bundled_adb = os.path.join(BIN_DIR, "adb.exe")
        if os.path.exists(bundled_adb):
            return bundled_adb
        return shutil.which("adb")

    def _run_adb(self, args, timeout=8) -> Optional[subprocess.CompletedProcess]:
        adb_bin = self.get_adb_path()
        if not adb_bin:
            return None
        with self.adb_lock:
            for attempt in range(2):
                try:
                    res = subprocess.run(
                        [adb_bin] + args,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=timeout
                    )
                    if res.returncode == 0:
                        return res
                    if any(kw in res.stderr for kw in ["connection reset", "protocol fault", "failed to read response", "daemon not running"]):
                        if not self._is_script_running:
                            subprocess.run(["taskkill", "/F", "/IM", "adb.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            time.sleep(0.5)
                            subprocess.run([adb_bin, "start-server"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
                        continue
                    return res
                except Exception:
                    if attempt == 1:
                        return None
                    if not self._is_script_running:
                        subprocess.run(["taskkill", "/F", "/IM", "adb.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        time.sleep(0.5)
                        try:
                            subprocess.run([adb_bin, "start-server"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
                        except Exception:
                            pass
            return None

    def get_connected_devices(self, force: bool = False) -> Dict[str, Any]:
        """
        获取当前手机连接的真实物理与系统状态。
        拒绝静态假数据：若 ADB 连通则提取真机真实电池传感器与窗口，否则如实汇报 MTP 连接状态并提示开启调试。
        """
        with self._cache_lock:
            # 当自动化任务在运行时，完全依赖缓存状态，杜绝高频 dumpsys 干扰主自动化任务
            if self._is_script_running and self._cached_info:
                return dict(self._cached_info)
            if not force and self._cached_info and (time.time() - self._cached_time < self.CACHE_TTL):
                return dict(self._cached_info)

        adb_bin = self.get_adb_path()
        
        device_info = {
            "name": self.default_device_name,
            "serial": self.default_serial,
            "connected": False,
            "adb_ready": False,
            "connection_type": "未连接",
            "status_text": "未检测到手机连接",
            "battery_level": None,             # 真实电量百分比 (0-100)
            "battery_status": None,            # 充电中 / 放电中
            "battery_temp": None,              # 电池温度 (°C)
            "battery_health": None,            # 电池健康度
            "screen_state": "未知",            # 亮屏 / 息屏 / 锁屏
            "current_app": "未知",             # 当前前台包名与窗口
            "os_version": "Xiaomi HyperOS",
            "adb_path": adb_bin
        }

        # 1. 尝试通过 ADB 读取真机底层真实状态
        res = self._run_adb(["devices", "-l"])
        if res and res.returncode == 0:
            lines = res.stdout.strip().splitlines()[1:]
            for line in lines:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "device":
                    device_info["connected"] = True
                    device_info["adb_ready"] = True
                    device_info["serial"] = parts[0]
                    device_info["connection_type"] = "USB (ADB调试模式)"
                    device_info["status_text"] = "真机已连接 (ADB调试就绪)"
                    
                    # 采集真实电池数据
                    self._populate_real_battery_info(parts[0], device_info)
                    # 采集真实屏幕与前台状态
                    self._populate_real_screen_and_app(parts[0], device_info)
                    
                    with self._cache_lock:
                        self._cached_info = dict(device_info)
                        self._cached_time = time.time()
                    return device_info

                elif len(parts) >= 2 and parts[1] == "unauthorized":
                    device_info["connected"] = True
                    device_info["adb_ready"] = False
                    device_info["serial"] = parts[0]
                    device_info["connection_type"] = "USB (待手机授权)"
                    device_info["status_text"] = "已连接手机，请在手机屏幕点击「一律允许这台计算机调试」"
                    device_info["battery_status"] = "需在手机点击授权后读取"
                    
                    with self._cache_lock:
                        self._cached_info = dict(device_info)
                        self._cached_time = time.time()
                    return device_info

        # 2. 如果 ADB 未开启，通过 Windows 物理硬件接口 (WPD/USB) 探查真实连接状态
        try:
            cmd = 'Get-PnpDevice -Class "WPD", "USBDevice" -Status OK -ErrorAction SilentlyContinue | Where-Object { $_.FriendlyName -match "Redmi|Xiaomi" -or $_.InstanceId -match "VID_2717" } | Select-Object -First 1 FriendlyName, InstanceId | ConvertTo-Json'
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=4
            )
            out = res.stdout.strip()
            if out and ("Redmi" in out or "Xiaomi" in out or "2717" in out):
                device_info["connected"] = True
                device_info["adb_ready"] = False
                device_info["connection_type"] = "USB (MTP文件传输模式)"
                device_info["status_text"] = "手机物理USB已连接 (需在手机开启USB调试以激活实时电量与自动化)"
                device_info["battery_status"] = "MTP协议受限，开启USB调试后可读"
                device_info["screen_state"] = "开启USB调试后可读"
                device_info["current_app"] = "开启USB调试后可读"
                
                with self._cache_lock:
                    self._cached_info = dict(device_info)
                    self._cached_time = time.time()
                return device_info
        except Exception:
            pass

        with self._cache_lock:
            self._cached_info = dict(device_info)
            self._cached_time = time.time()
        return device_info

    def _populate_real_battery_info(self, serial: str, device_info: Dict[str, Any]):
        """通过 ADB dumpsys battery 获取真机真实电池数据"""
        try:
            res = self._run_adb(["-s", serial, "shell", "dumpsys", "battery"], timeout=3)
            if not res or res.returncode != 0:
                return
            for line in res.stdout.splitlines():
                line = line.strip()
                if line.startswith("level:"):
                    device_info["battery_level"] = int(line.split(":")[1].strip())
                elif line.startswith("temperature:"):
                    temp_val = int(line.split(":")[1].strip()) / 10.0
                    device_info["battery_temp"] = f"{temp_val}°C"
                elif line.startswith("status:"):
                    status_code = line.split(":")[1].strip()
                    status_map = {"2": "充电中", "3": "放电中", "4": "未充电", "5": "已充满"}
                    device_info["battery_status"] = status_map.get(status_code, "使用中")
                elif line.startswith("health:"):
                    health_map = {"2": "良好", "3": "过热", "4": "损坏", "5": "过压"}
                    device_info["battery_health"] = health_map.get(line.split(":")[1].strip(), "正常")
        except Exception:
            pass

    def _populate_real_screen_and_app(self, serial: str, device_info: Dict[str, Any]):
        """通过 ADB 获取真机当前屏幕唤醒状态与前台应用"""
        try:
            pwr = self._run_adb(["-s", serial, "shell", "dumpsys", "power"], timeout=3)
            if pwr and pwr.returncode == 0:
                for line in pwr.stdout.splitlines():
                    if "mWakefulness=" in line:
                        state = line.split("=")[1].strip()
                        device_info["screen_state"] = "亮屏 (Awake)" if "Awake" in state else "息屏 (Asleep)"
                        break

            win = self._run_adb(["-s", serial, "shell", "dumpsys", "window", "displays"], timeout=3)
            if win and win.returncode == 0:
                for line in win.stdout.splitlines():
                    if "mCurrentFocus" in line:
                        match = re.search(r"mCurrentFocus=Window\{[^\}]+\s+([^\}]+)\}", line)
                        if match:
                            device_info["current_app"] = match.group(1).strip()
                        else:
                            device_info["current_app"] = line.strip()
                        break
        except Exception:
            pass

    def capture_screenshot(self, run_id: str, scenario: Optional[str] = None) -> str:
        """
        截取手机屏幕并按照规范化目录结构存储：
        路径规范: storage/captures/YYYY-MM-DD/<run_id>_snapshot.png
        访问路由: /storage/captures/YYYY-MM-DD/<run_id>_snapshot.png
        """
        date_folder = datetime.now().strftime("%Y-%m-%d")
        dest_dir = os.path.join(CAPTURES_ROOT, date_folder)
        os.makedirs(dest_dir, exist_ok=True)

        filename = f"{run_id}_snapshot.png"
        filepath = os.path.join(dest_dir, filename)

        adb_bin = self.get_adb_path()
        # 如果真实真机 ADB 就绪，直接抓取真机物理屏幕
        if adb_bin and os.path.exists(adb_bin):
            try:
                cmd = [adb_bin, "exec-out", "screencap", "-p"]
                with open(filepath, "wb") as f:
                    proc = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, timeout=10)
                    if proc.returncode == 0 and os.path.getsize(filepath) > 2000:
                        return f"/storage/captures/{date_folder}/{filename}"
            except Exception:
                pass

        # 若处于仿真注入模式或未开调试，生成现场可视化快照
        self._generate_mock_screenshot(filepath, scenario)
        return f"/storage/captures/{date_folder}/{filename}"

    def _generate_mock_screenshot(self, filepath: str, scenario: Optional[str]):
        """生成带视觉UI的规范化快照图片"""
        svg_content = self._create_mobile_screen_svg(scenario)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(svg_content)

    def _create_mobile_screen_svg(self, scenario: Optional[str]) -> str:
        title = "系统提示"
        body = "发生未知错误"
        badge = "ERROR"
        badge_color = "#EF4444"
        btn_text = "确定"
        app_title = "实在智能 RPA 业务客户端"

        if scenario == "mock_permission":
            title = "系统权限申请"
            body = "「实在智能RPA客户端」申请获取以下权限：<br/>• 后台常驻悬浮窗<br/>• 显示系统级界面弹窗<br/>• 自动化读取屏幕内容"
            badge = "MIUI 权限阻断"
            badge_color = "#F59E0B"
            btn_text = "仅在使用中允许 | 拒绝"
        elif scenario == "mock_update":
            title = "发现新版本 v2.4.0"
            body = "为了保证业务正常运行，请立即升级到最新版本：<br/>1. 全新 UI 架构改版<br/>2. 修复已知提交订单卡死问题<br/>3. 优化手机端数据加密"
            badge = "App 强制更新弹窗"
            badge_color = "#EC4899"
            btn_text = "立即升级 (强制)"
        elif scenario == "mock_timeout":
            title = "控件查找超时"
            body = "脚本尝试等待元素出现超过 15 秒：<br/><b>Target: #btn_submit_approval</b><br/>当前页面布局已变更，找不到目标审批提交按钮。"
            badge = "UI 改版 / 元素超时"
            badge_color = "#6366F1"
            btn_text = "重试查找"
        elif scenario == "real_device":
            title = "真机连接就绪检测"
            body = "设备已成功连接: Redmi Note 13 Pro<br/>• 当前硬件通道: USB 通讯就绪<br/>• 正在执行自动化任务健康度巡检"
            badge = "真机连通就绪"
            badge_color = "#10B981"
            btn_text = "继续执行"

        return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 860" width="420" height="860">
  <defs>
    <linearGradient id="screenBg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0F172A" />
      <stop offset="100%" stop-color="#1E293B" />
    </linearGradient>
    <filter id="shadow" x="-10%" y="-10%" width="120%" height="120%">
      <feDropShadow dx="0" dy="8" stdDeviation="16" flood-color="#000000" flood-opacity="0.6"/>
    </filter>
  </defs>

  <rect x="5" y="5" width="410" height="850" rx="42" fill="#1E293B" stroke="#334155" stroke-width="4"/>
  <rect x="15" y="15" width="390" height="830" rx="34" fill="url(#screenBg)"/>

  <text x="35" y="44" fill="#94A3B8" font-family="system-ui, sans-serif" font-size="13" font-weight="600">09:41</text>
  <circle cx="210" cy="38" r="7" fill="#0F172A" stroke="#334155" stroke-width="1.5"/>
  <path d="M 350 35 L 365 35 M 353 40 L 365 40 M 356 45 L 365 45" stroke="#94A3B8" stroke-width="2" stroke-linecap="round"/>
  <rect x="372" y="32" width="22" height="11" rx="2.5" fill="none" stroke="#94A3B8" stroke-width="1.5"/>
  <rect x="374" y="34" width="14" height="7" rx="1.5" fill="#10B981"/>

  <rect x="15" y="60" width="390" height="54" fill="#1E293B" />
  <text x="35" y="93" fill="#F8FAFC" font-family="system-ui, sans-serif" font-size="16" font-weight="bold">{app_title}</text>
  <line x1="15" y1="114" x2="405" y2="114" stroke="#334155" stroke-width="1"/>

  <g opacity="0.3">
    <rect x="35" y="135" width="350" height="70" rx="12" fill="#334155"/>
    <rect x="55" y="150" width="140" height="14" rx="4" fill="#64748B"/>
    <rect x="55" y="172" width="220" height="10" rx="4" fill="#475569"/>

    <rect x="35" y="220" width="350" height="70" rx="12" fill="#334155"/>
    <rect x="55" y="235" width="110" height="14" rx="4" fill="#64748B"/>
    <rect x="55" y="257" width="180" height="10" rx="4" fill="#475569"/>
  </g>

  <rect x="15" y="60" width="390" height="785" fill="#000000" fill-opacity="0.65"/>

  <g filter="url(#shadow)">
    <rect x="40" y="270" width="340" height="310" rx="20" fill="#1E293B" stroke="#475569" stroke-width="1.5"/>
    
    <rect x="60" y="295" width="130" height="26" rx="6" fill="{badge_color}" fill-opacity="0.2"/>
    <text x="70" y="313" fill="{badge_color}" font-family="system-ui, sans-serif" font-size="12" font-weight="bold">● {badge}</text>

    <text x="60" y="355" fill="#F8FAFC" font-family="system-ui, sans-serif" font-size="19" font-weight="bold">{title}</text>

    <foreignObject x="60" y="375" width="300" height="120">
      <div xmlns="http://www.w3.org/1999/xhtml" style="color: #CBD5E1; font-size: 13.5px; line-height: 1.6; font-family: system-ui, sans-serif;">
        {body}
      </div>
    </foreignObject>

    <rect x="60" y="505" width="300" height="46" rx="10" fill="#3B82F6"/>
    <text x="210" y="534" fill="#FFFFFF" font-family="system-ui, sans-serif" font-size="14" font-weight="600" text-anchor="middle">{btn_text}</text>
  </g>

  <rect x="100" y="805" width="220" height="24" rx="12" fill="#0F172A" fill-opacity="0.9" stroke="#334155" stroke-width="1"/>
  <text x="210" y="821" fill="#94A3B8" font-family="system-ui, sans-serif" font-size="11" font-weight="500" text-anchor="middle">存储路径: storage/captures/</text>
</svg>'''

device_manager = DeviceManager()
