import os
import sys
import time
import uuid
import json
import threading
import subprocess
from datetime import datetime
from typing import Dict, Any, Optional

from backend.database import create_run, update_run, get_run, get_all_configs
from backend.device import device_manager
from backend.analyzer import analyzer
from backend.notifier import notifier

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE_ROOT = os.path.join(PROJECT_ROOT, "storage")
LOGS_ROOT = os.path.join(STORAGE_ROOT, "logs")
os.makedirs(LOGS_ROOT, exist_ok=True)

class ScriptRunner:
    def __init__(self):
        self.active_runs: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()
        # 服务启动时清理数据库遗留的孤儿运行中状态
        try:
            from backend.database import cleanup_stale_running_tasks
            cleanup_stale_running_tasks()
        except Exception:
            pass

    def is_busy(self) -> bool:
        with self.lock:
            for rid, info in list(self.active_runs.items()):
                if info.get("status") == "RUNNING":
                    proc = info.get("process")
                    # 如果进程对象存在但已退出，自动清理释放锁
                    if proc and proc.poll() is not None:
                        info["status"] = "FINISHED"
                        continue
                    return True
            return False

    def stop_run(self, run_id: Optional[str] = None) -> bool:
        """主动终止正在运行的任务进程"""
        with self.lock:
            targets = []
            if run_id and run_id in self.active_runs:
                targets.append((run_id, self.active_runs[run_id]))
            else:
                for rid, info in self.active_runs.items():
                    if info.get("status") == "RUNNING":
                        targets.append((rid, info))

            stopped = False
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for rid, info in targets:
                proc = info.get("process")
                if proc and proc.poll() is None:
                    try:
                        proc.terminate()
                        stopped = True
                    except Exception:
                        try:
                            proc.kill()
                            stopped = True
                        except Exception:
                            pass
                info["status"] = "CANCELLED"
                info["logs_buffer"].append(f"\n[{now_str}] 🛑 用户已从网页控制台主动中止该自动化任务。\n")
                full_logs = "".join(info.get("logs_buffer", []))
                try:
                    update_run(rid, {
                        "status": "FAILED",
                        "end_time": now_str,
                        "error_type": "MANUAL_ABORT",
                        "error_message": "用户从网页控制台手动中止任务",
                        "logs": full_logs
                    })
                except Exception as db_err:
                    print(f"[Stop Run DB Error]: {db_err}")
            return stopped

    def pause_run(self, run_id: Optional[str] = None) -> bool:
        """用户主动暂停正在运行的任务（安全封存断点，方便拔插设备与稍后继跑）"""
        with self.lock:
            targets = []
            if run_id and run_id in self.active_runs:
                targets.append((run_id, self.active_runs[run_id]))
            else:
                for rid, info in self.active_runs.items():
                    if info.get("status") == "RUNNING":
                        targets.append((rid, info))

            paused = False
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 1. 写入暂停控制信号文件，通知脚本自行安全完成当前单步并退出
            pause_signal = os.path.join(STORAGE_ROOT, "temp", "crawler_pause.signal")
            os.makedirs(os.path.join(STORAGE_ROOT, "temp"), exist_ok=True)
            try:
                with open(pause_signal, "w", encoding="utf-8") as f:
                    f.write(now_str)
            except Exception:
                pass

            for rid, info in targets:
                proc = info.get("process")
                if proc and proc.poll() is None:
                    # 等待最多 2.5 秒让脚本优雅落盘
                    try:
                        proc.wait(timeout=2.5)
                    except Exception:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                    paused = True

                info["status"] = "PAUSED"
                info["logs_buffer"].append(f"\n[{now_str}] ⏸️ 任务已由用户主动暂停！断点数据已安全封存，手机可随时断开连接。稍后重新连接后点击「断点续跑」即可继续。\n")
                full_logs = "".join(info.get("logs_buffer", []))
                try:
                    update_run(rid, {
                        "status": "PAUSED",
                        "end_time": now_str,
                        "error_type": "USER_PAUSED",
                        "error_message": "用户主动暂停任务（断点已保留）",
                        "logs": full_logs
                    })
                except Exception as db_err:
                    print(f"[Pause Run DB Error]: {db_err}")

            # 释放设备独占锁
            device_manager.set_script_running(False)

            # 同步更新断点存档状态为 PAUSED (通用适配所有采集类脚本)
            try:
                import glob
                date_str = time.strftime("%Y-%m-%d")
                ckpt_files = glob.glob(os.path.join(STORAGE_ROOT, "reports", date_str, "*_checkpoint.json"))
                for ckpt_path in ckpt_files:
                    try:
                        with open(ckpt_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if isinstance(data, dict) and data.get("status") != "RESET":
                            data["status"] = "PAUSED"
                            data["last_updated"] = now_str
                            with open(ckpt_path, "w", encoding="utf-8") as f:
                                json.dump(data, f, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
            except Exception as ckpt_err:
                print(f"[Pause ckpt update error]: {ckpt_err}")

            return paused

    def get_live_logs(self, run_id: str) -> str:
        with self.lock:
            if run_id in self.active_runs:
                return "".join(self.active_runs[run_id]["logs_buffer"])
        # 如果未在内存，则从数据库读取
        run = get_run(run_id)
        return run.get("logs", "") if run else ""

    def execute_async(self, script_name: str = "sample_mobile_task.py", scenario: str = "normal", extra_args: Optional[list] = None) -> str:
        """异步启动执行脚本并返回 run_id"""
        now = datetime.now()
        run_id = f"run_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
        start_time_str = now.strftime("%Y-%m-%d %H:%M:%S")

        script_path = os.path.join(PROJECT_ROOT, script_name)
        device_info = device_manager.get_connected_devices()

        # 启动新任务前清理任何可能遗留的暂停信号
        pause_signal = os.path.join(STORAGE_ROOT, "temp", "crawler_pause.signal")
        if os.path.exists(pause_signal):
            try:
                os.remove(pause_signal)
            except Exception:
                pass

        # 1. 数据库记录初始化
        run_data = {
            "id": run_id,
            "script_name": script_name,
            "device_name": device_info.get("name", "Redmi Note 13 Pro"),
            "device_id": device_info.get("serial", "A05B2A6C"),
            "status": "RUNNING",
            "scenario": scenario,
            "start_time": start_time_str,
            "end_time": None,
            "duration": 0.0,
            "error_type": None,
            "error_message": None,
            "ai_analysis": None,
            "suggested_action": None,
            "screenshot_path": None,
            "logs": ""
        }
        create_run(run_data)

        # 2. 内存活跃状态维护
        with self.lock:
            self.active_runs[run_id] = {
                "status": "RUNNING",
                "logs_buffer": [f"[{start_time_str}] 任务启动: {script_name} (场景: {scenario})\n"],
                "start_time": time.time(),
                "process": None,
                "last_update": time.time()
            }

        # 3. 开启后台守护线程执行子进程
        thread = threading.Thread(
            target=self._run_worker,
            args=(run_id, script_path, scenario, run_data, extra_args or []),
            daemon=True
        )
        thread.start()

        return run_id

    def _run_worker(self, run_id: str, script_path: str, scenario: str, run_data: Dict[str, Any], extra_args: list):
        start_ts = time.time()
        error_lines = []
        return_code = 1
        proc = None

        # 告知设备管理器任务已独占启动，暂停高频 dumpsys 遥测，杜绝争抢 ADB
        device_manager.set_script_running(True)

        # 准备隔离子进程环境变量 (强制 Python 实时无缓冲输出与 UTF-8 编码)
        child_env = os.environ.copy()
        child_env["PYTHONUNBUFFERED"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"
        bin_dir = os.path.join(PROJECT_ROOT, "bin")
        if bin_dir not in child_env.get("PATH", ""):
            child_env["PATH"] = bin_dir + os.pathsep + child_env.get("PATH", "")

        try:
            # 加入 -u 确保 Python 子进程标准输出不产生 4KB 缓冲死锁
            cmd = [sys.executable, "-u", script_path, "--scenario", scenario] + extra_args
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=PROJECT_ROOT,
                env=child_env,
                text=True,
                bufsize=1,
                universal_newlines=True,
                encoding="utf-8",
                errors="replace"
            )

            with self.lock:
                if run_id in self.active_runs:
                    self.active_runs[run_id]["process"] = proc

            # 实时读取输出流
            for line in proc.stdout:
                with self.lock:
                    if run_id in self.active_runs:
                        self.active_runs[run_id]["logs_buffer"].append(line)
                        self.active_runs[run_id]["last_update"] = time.time()
                if any(kw in line for kw in ["ERROR", "Exception", "Traceback", "💥", "🚨", "RuntimeError", "Timeout"]):
                    error_lines.append(line.strip())

            proc.wait()
            return_code = proc.returncode

        except Exception as e:
            return_code = 1
            err_msg = f"子进程启动异常: {str(e)}\n"
            with self.lock:
                if run_id in self.active_runs:
                    self.active_runs[run_id]["logs_buffer"].append(err_msg)
            error_lines.append(err_msg)
        finally:
            device_manager.set_script_running(False)
            if proc and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass

        duration = round(time.time() - start_ts, 2)
        end_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self.lock:
            full_logs = "".join(self.active_runs.get(run_id, {}).get("logs_buffer", []))

        # 保存物理日志文件至 storage/logs/YYYY-MM-DD/<run_id>.log
        try:
            date_str = datetime.now().strftime("%Y-%m-%d")
            log_dir = os.path.join(LOGS_ROOT, date_str)
            os.makedirs(log_dir, exist_ok=True)
            log_file_path = os.path.join(log_dir, f"{run_id}.log")
            with open(log_file_path, "w", encoding="utf-8") as f:
                f.write(full_logs)
        except Exception as log_err:
            print(f"[Runner Log Save Error]: {log_err}")

        # 4. 判定执行结果与异常处理闭环
        with self.lock:
            mem_status = self.active_runs.get(run_id, {}).get("status")
        if mem_status in ["PAUSED", "CANCELLED"]:
            # 用户已主动暂停或中止任务，不覆盖状态，不触发失败告警
            return

        if return_code == 0:
            status = "SUCCESS"
            screenshot_url = None
            if scenario in ["real_device", "bilibili_video"]:
                screenshot_url = device_manager.capture_screenshot(run_id, scenario=scenario)
            update_data = {
                "status": status,
                "end_time": end_time_str,
                "duration": duration,
                "screenshot_path": screenshot_url,
                "logs": full_logs
            }
            update_run(run_id, update_data)
        else:
            status = "FAILED"
            error_message = "\n".join(error_lines[-3:]) if error_lines else "自动化脚本异常非零退出"
            
            # (1) 自动截取现场屏幕
            screenshot_url = device_manager.capture_screenshot(run_id, scenario=scenario)

            # (2) 触发 AI / 本地专家规则智能根因诊断
            configs = get_all_configs()
            diagnosis = analyzer.diagnose(
                logs=full_logs,
                error_message=error_message,
                scenario=scenario,
                ai_config=configs
            )

            update_data = {
                "status": status,
                "end_time": end_time_str,
                "duration": duration,
                "error_type": diagnosis.get("error_type"),
                "error_message": error_message,
                "ai_analysis": json.dumps(diagnosis, ensure_ascii=False),
                "suggested_action": diagnosis.get("actionable_suggestion"),
                "screenshot_path": screenshot_url,
                "logs": full_logs
            }
            update_run(run_id, update_data)

            # (3) 触发消息推送告警 (飞书/钉钉/企微)
            run_data.update(update_data)
            try:
                notifier.send_alert(run_data, diagnosis, configs)
            except Exception as notify_err:
                print(f"[Notifier Error] 告警推送失败: {notify_err}")

        with self.lock:
            if run_id in self.active_runs:
                self.active_runs[run_id]["status"] = status

runner = ScriptRunner()

class WatchdogManager:
    def __init__(self, script_runner: ScriptRunner):
        self.runner = script_runner
        self.enabled = False
        self.interval = 60
        self.countdown = 60
        self.scenario = "normal"
        self.last_run_time = None
        self.last_run_status = "IDLE"
        self.total_watch_cycles = 0
        self.lock = threading.Lock()
        
        self.thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self.thread.start()

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "enabled": self.enabled,
                "interval": self.interval,
                "countdown": self.countdown,
                "scenario": self.scenario,
                "last_run_time": self.last_run_time,
                "last_run_status": self.last_run_status,
                "total_watch_cycles": self.total_watch_cycles,
                "is_runner_busy": self.runner.is_busy()
            }

    def configure(self, enabled: Optional[bool] = None, interval: Optional[int] = None, scenario: Optional[str] = None):
        with self.lock:
            if enabled is not None:
                self.enabled = enabled
                if enabled and self.countdown <= 0:
                    self.countdown = self.interval
            if interval is not None and interval >= 10:
                self.interval = interval
                self.countdown = interval
            if scenario is not None:
                self.scenario = scenario

    def _watchdog_loop(self):
        while True:
            time.sleep(1)
            with self.lock:
                if not self.enabled:
                    continue
                self.countdown -= 1
                if self.countdown <= 0:
                    if not self.runner.is_busy():
                        self.total_watch_cycles += 1
                        self.last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.countdown = self.interval
                        target_scenario = self.scenario
                        threading.Thread(target=self._trigger_cycle, args=(target_scenario,), daemon=True).start()
                    else:
                        self.countdown = 5

    def _trigger_cycle(self, scenario: str):
        try:
            self.runner.execute_async(scenario=scenario)
        except Exception as e:
            print(f"[Watchdog Error] Trigger failed: {e}")

watchdog = WatchdogManager(runner)
