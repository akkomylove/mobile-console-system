# -*- coding: utf-8 -*-
"""
实在智能RPA - 手机端流程控制台
单品重跑探针任务管理与串行队列调度引擎 (backend/probe_queue.py)

核心设计目标:
1. 物理真机单体互斥独占: 同一时刻只允许 1 个探针任务驱动真机, 坚决杜绝多探针并发冲突;
2. 任务保护不被挤掉: 新任务安全进入等待队列 (QUEUED), 正在运行的任务不受任何干扰;
3. 队列精细化调度: 支持提前任务(置顶)、上移下移、自定义排序、任务取消与删除;
4. 状态实时透视: 提供队列完整快照与最近完成历史, 支持前端秒级动态感知。
"""

import time
import uuid
import threading
from typing import Dict, Any, List, Optional, Tuple

class ProbeQueueManager:
    """单品重跑探针任务队列调度器 (单例)"""
    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super(ProbeQueueManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self.lock = threading.Lock()
        self.wake_event = threading.Event()
        self.current_task: Optional[Dict[str, Any]] = None
        self.queue: List[Dict[str, Any]] = []
        self.history: List[Dict[str, Any]] = []  # 保留最近 30 条已完成/失败记录
        self.running = True

        # 启动后台守护工作线程
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="ProbeQueueWorker")
        self.worker_thread.start()
        self._initialized = True

    def add_task(self, audit_id: int) -> Dict[str, Any]:
        """
        将指定审计条目推入探针任务队列：
        - 若已在运行中，返回 RUNNING 状态与当前信息；
        - 若已在队列中，返回 QUEUED 状态与当前排队位次，避免重复添加；
        - 若未排队，构建任务对象推入队尾，唤醒工作线程。
        """
        from backend.database import get_single_audit_item
        item = get_single_audit_item(audit_id)
        if not item:
            return {"success": False, "message": f"未找到 ID 为 {audit_id} 的数据项"}

        with self.lock:
            # 1. 检查是否正在执行
            if self.current_task and self.current_task.get("audit_id") == audit_id:
                return {
                    "success": True,
                    "message": "该商品探针当前正在真机执行中，无需重复触发",
                    "task_id": self.current_task["task_id"],
                    "status": "RUNNING",
                    "position": 0,
                    "task": self.current_task
                }

            # 2. 检查是否已在等待队列中
            for idx, task in enumerate(self.queue):
                if task.get("audit_id") == audit_id:
                    return {
                        "success": True,
                        "message": f"该商品已在等待队列中 (当前排在第 {idx + 1} 位)",
                        "task_id": task["task_id"],
                        "status": "QUEUED",
                        "position": idx + 1,
                        "task": task
                    }

            # 3. 构造新任务入队
            now_ts = time.time()
            task_id = f"probe_{int(now_ts)}_{uuid.uuid4().hex[:6]}"
            new_task = {
                "task_id": task_id,
                "audit_id": audit_id,
                "item_seq": item.get("item_seq", audit_id),
                "title": item.get("title", "未命名商品"),
                "shop": item.get("shop", "未知店铺"),
                "price_str": item.get("price_str", "¥0"),
                "script_name": item.get("script_name", "pdd_gpu_crawler"),
                "task_title": item.get("task_title", "移动端采数"),
                "status": "QUEUED",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts)),
                "created_ts": now_ts,
                "start_time": None,
                "start_ts": None,
                "duration": 0.0,
                "error": None,
                "result": None
            }

            self.queue.append(new_task)
            position = len(self.queue)
            # 唤醒工作线程
            self.wake_event.set()

        return {
            "success": True,
            "message": f"已成功加入探针任务队列 (排在第 {position} 位)",
            "task_id": task_id,
            "status": "QUEUED",
            "position": position,
            "task": new_task
        }

    def get_status(self) -> Dict[str, Any]:
        """获取当前队列全局快照"""
        with self.lock:
            # 计算当前运行任务的即时耗时
            curr = None
            if self.current_task:
                curr = dict(self.current_task)
                if curr.get("start_ts"):
                    curr["elapsed"] = round(time.time() - curr["start_ts"], 1)
                else:
                    curr["elapsed"] = 0.0

            q_list = [dict(t) for t in self.queue]
            for idx, item in enumerate(q_list):
                item["position"] = idx + 1

            h_list = [dict(t) for t in self.history[-15:]]
            h_list.reverse()

            return {
                "is_busy": self.current_task is not None,
                "current": curr,
                "queue": q_list,
                "history": h_list,
                "pending_count": len(q_list),
                "active_audit_ids": self._get_active_audit_ids_internal()
            }

    def _get_active_audit_ids_internal(self) -> Dict[int, Dict[str, Any]]:
        """获取所有正处于活跃状态 (运行中或排队中) 的 audit_id 映射"""
        mapping = {}
        if self.current_task:
            aid = self.current_task.get("audit_id")
            if aid:
                mapping[aid] = {"status": "RUNNING", "position": 0, "task_id": self.current_task["task_id"]}
        for idx, t in enumerate(self.queue):
            aid = t.get("audit_id")
            if aid:
                mapping[aid] = {"status": "QUEUED", "position": idx + 1, "task_id": t["task_id"]}
        return mapping

    def prioritize_task(self, task_id: str) -> Dict[str, Any]:
        """
        提前任务 (置顶 / 立即插队):
        将任务调整至等待队列的第 1 位 (即 queue[0])，当前运行任务结束后立即执行它
        """
        with self.lock:
            idx = self._find_task_index(task_id)
            if idx is None:
                return {"success": False, "message": "未在等待队列中找到该任务 (可能已开始或已取消)"}
            if idx == 0:
                return {"success": True, "message": "该任务已经在等待队列第 1 位，无需调整", "position": 1}

            task = self.queue.pop(idx)
            self.queue.insert(0, task)
            return {"success": True, "message": f"已将任务【{task['title'][:15]}...】提前至下一执行位", "position": 1}

    def move_task(self, task_id: str, direction: str = "up") -> Dict[str, Any]:
        """
        上移 / 下移调整任务次序:
        direction: 'up' (前移一位) 或 'down' (后移一位)
        """
        with self.lock:
            idx = self._find_task_index(task_id)
            if idx is None:
                return {"success": False, "message": "未在等待队列中找到该任务"}

            if direction == "up":
                if idx == 0:
                    return {"success": False, "message": "该任务已在等待队列首位，无法继续上移"}
                self.queue[idx], self.queue[idx - 1] = self.queue[idx - 1], self.queue[idx]
                new_pos = idx
            elif direction == "down":
                if idx >= len(self.queue) - 1:
                    return {"success": False, "message": "该任务已在等待队列末尾，无法继续下移"}
                self.queue[idx], self.queue[idx + 1] = self.queue[idx + 1], self.queue[idx]
                new_pos = idx + 2
            else:
                return {"success": False, "message": f"不支持的移动方向: {direction}"}

            return {"success": True, "message": f"调整成功，当前排在第 {new_pos} 位", "position": new_pos}

    def reorder_queue(self, task_ids: List[str]) -> Dict[str, Any]:
        """自定义全量顺序重排"""
        with self.lock:
            id_map = {t["task_id"]: t for t in self.queue}
            new_queue = []
            for tid in task_ids:
                if tid in id_map:
                    new_queue.append(id_map.pop(tid))
            new_queue.extend(list(id_map.values()))
            self.queue = new_queue
            return {"success": True, "message": f"已重新排序 {len(self.queue)} 项排队任务"}

    def remove_task(self, task_id: str) -> Dict[str, Any]:
        """从等待队列中删除/取消任务"""
        with self.lock:
            idx = self._find_task_index(task_id)
            if idx is None:
                return {"success": False, "message": "未在等待队列中找到该任务"}

            task = self.queue.pop(idx)
            task["status"] = "CANCELLED"
            task["duration"] = 0.0
            task["error"] = "用户从任务队列管理中主动移除"
            self.history.append(task)
            return {"success": True, "message": f"已从队列中移除任务【{task['title'][:15]}...】"}

    def clear_queue(self) -> Dict[str, Any]:
        """清空所有等待中的任务 (不影响当前正在执行中的任务)"""
        with self.lock:
            count = len(self.queue)
            for t in self.queue:
                t["status"] = "CANCELLED"
                t["duration"] = 0.0
                t["error"] = "批量清空队列"
                self.history.append(t)
            self.queue.clear()
            return {"success": True, "message": f"已清空 {count} 项等待中的探针任务", "cleared_count": count}

    def stop_current(self) -> Dict[str, Any]:
        """强行中止当前正在执行的探针任务"""
        with self.lock:
            if not self.current_task:
                return {"success": False, "message": "当前没有正在执行中的探针任务"}
            task = self.current_task
            task["status"] = "CANCELLED"
            task["error"] = "用户主动从队列控制面板强行中止"
            if task.get("start_ts"):
                task["duration"] = round(time.time() - task["start_ts"], 1)
            self.history.append(task)
            self.current_task = None
            self.wake_event.set()
            return {"success": True, "message": f"已强行中止任务【{task['title'][:15]}...】，系统将自动流转下一个任务"}

    def _find_task_index(self, task_id: str) -> Optional[int]:
        for idx, t in enumerate(self.queue):
            if t.get("task_id") == task_id or str(t.get("audit_id")) == str(task_id):
                return idx
        return None

    def _worker_loop(self):
        """后台单工作线程：安全、串行驱动真机执行，任务一个个完成，绝不并发打架"""
        while self.running:
            task = None
            with self.lock:
                if self.queue:
                    task = self.queue.pop(0)
                    self.current_task = task
                    now_ts = time.time()
                    task["status"] = "RUNNING"
                    task["start_ts"] = now_ts
                    task["start_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts))
                else:
                    self.current_task = None
                    self.wake_event.clear()

            if not task:
                self.wake_event.wait(timeout=1.0)
                continue

            audit_id = task["audit_id"]
            print(f"\n[ProbeQueue] ▶️ 开始执行探针任务: TaskId={task['task_id']}, AuditId={audit_id}, 商品={task['title'][:25]}...")

            try:
                from backend.ai_auditor import diagnose_audit_item
                result = diagnose_audit_item(audit_id, auto_replay=True)
                task["status"] = "COMPLETED"
                task["result"] = result
                print(f"[ProbeQueue] ✅ 探针任务圆满完成: TaskId={task['task_id']}, 裁决={result.get('verdict')}, 纠偏价={result.get('corrected_price')}")
            except Exception as e:
                task["status"] = "FAILED"
                task["error"] = str(e)
                print(f"[ProbeQueue] ❌ 探针任务执行异常: TaskId={task['task_id']}, 错误: {e}")
            finally:
                if task.get("start_ts"):
                    task["duration"] = round(time.time() - task["start_ts"], 1)
                with self.lock:
                    self.history.append(dict(task))
                    if len(self.history) > 50:
                        self.history = self.history[-50:]
                    self.current_task = None

                time.sleep(1.2)

probe_queue_manager = ProbeQueueManager()
