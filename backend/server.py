import os
import time
import json
import re
from urllib.parse import quote
from fastapi import FastAPI, Query, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

from backend.database import (
    get_dashboard_stats, get_runs, get_runs_with_total, get_run,
    get_all_configs, set_config, init_db, clear_all_runs,
    get_audit_tasks, get_audit_items, get_single_audit_item,
    update_audit_action, get_quality_stats,
    get_clean_report_items, save_audit_batch,
    update_audit_ai_result, adopt_ai_verdict, apply_corrected_price,
    get_crawler_params, save_crawler_params
)
from backend.quality import audit_batch, export_clean_excel, compile_quality_rule
from backend.ai_auditor import diagnose_audit_item, batch_diagnose_script_anomalies
from backend.item_replay_runner import replay_single_item
from backend.device import device_manager
from backend.runner import runner, watchdog
from backend.notifier import notifier
from backend.probe_queue import probe_queue_manager

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")
STORAGE_ROOT = os.path.join(PROJECT_ROOT, "storage")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
SCREENSHOTS_DIR = os.path.join(DATA_DIR, "screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
os.makedirs(STORAGE_ROOT, exist_ok=True)
os.makedirs(FRONTEND_DIR, exist_ok=True)

app = FastAPI(title="手机端流程控制台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件目录 (支持老路径与标准化 storage/ 路径)
app.mount("/screenshots", StaticFiles(directory=SCREENSHOTS_DIR), name="screenshots")
app.mount("/storage", StaticFiles(directory=STORAGE_ROOT), name="storage")

class ExecuteRequest(BaseModel):
    script_name: str = "sample_mobile_task.py"
    scenario: str = "normal"
    resume: bool = True
    params: Optional[Dict[str, Any]] = None

class ConfigUpdateRequest(BaseModel):
    feishu_webhook: Optional[str] = None
    dingtalk_webhook: Optional[str] = None
    wecom_webhook: Optional[str] = None
    custom_webhook: Optional[str] = None
    alert_enabled: Optional[str] = None
    ai_engine: Optional[str] = None
    ai_api_key: Optional[str] = None
    ai_api_base: Optional[str] = None
    ai_model: Optional[str] = None

class WatchdogConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    interval: Optional[int] = None
    scenario: Optional[str] = None

class TestAlertRequest(BaseModel):
    channel: str = "feishu"
    webhook_url: str

class AuditActionRequest(BaseModel):
    action: str  # APPROVED, REJECTED, MODIFIED
    modified_price: Optional[float] = None

class ProbeQueueActionRequest(BaseModel):
    task_id: Optional[str] = None
    direction: Optional[str] = "up"
    task_ids: Optional[List[str]] = None

@app.get("/api/dashboard/stats")
def get_stats():
    stats = get_dashboard_stats()
    device = device_manager.get_connected_devices()
    stats["device"] = device
    stats["is_runner_busy"] = runner.is_busy()
    stats["watchdog"] = watchdog.get_status()
    return stats

@app.get("/api/watchdog/status")
def get_watchdog_status():
    return watchdog.get_status()

@app.post("/api/watchdog/config")
def config_watchdog(req: WatchdogConfigRequest):
    watchdog.configure(enabled=req.enabled, interval=req.interval, scenario=req.scenario)
    return {"message": "巡检守护配置已更新", "status": watchdog.get_status()}

@app.post("/api/runs/clear")
def clear_runs_endpoint():
    clear_all_runs()
    return {"message": "已清空所有历史运行记录"}

@app.get("/api/device/status")
@app.post("/api/device/probe-adb")
def get_device_status():
    return device_manager.get_connected_devices()

@app.get("/api/runs")
def list_runs(
    status: Optional[str] = Query("ALL"),
    limit: int = Query(15, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    return get_runs_with_total(status=status, limit=limit, offset=offset)

@app.get("/api/runs/{run_id}")
def get_run_detail(run_id: str):
    data = get_run(run_id)
    if not data:
        raise HTTPException(status_code=404, detail="Run not found")
    return data

@app.get("/api/runs/{run_id}/logs")
def get_run_logs(run_id: str):
    logs = runner.get_live_logs(run_id)
    run = get_run(run_id)
    status = run.get("status") if run else "UNKNOWN"
    return {"run_id": run_id, "logs": logs, "status": status}

@app.post("/api/runner/execute")
def trigger_execution(req: ExecuteRequest):
    if runner.is_busy():
        return JSONResponse(status_code=400, content={"error": "当前已有任务正在执行，请稍候"})
    
    script_name = req.script_name
    extra_args = []
    if req.scenario == "real_device":
        script_name = "real_phone_automation.py"
    elif req.scenario == "bilibili_video":
        script_name = "bilibili_video_task.py"
    elif req.scenario == "pdd_beer_top15":
        script_name = "pdd_beer_top15_crawler.py"
        extra_args.extend(["--count", "15"])
        if req.resume:
            extra_args.append("--resume")
        else:
            extra_args.append("--fresh")
    elif req.scenario == "pdd_gpu":
        script_name = "pdd_gpu_crawler.py"
        params = req.params or get_crawler_params()
        count = int(params.get("target_count") or params.get("count") or 100)
        keyword = str(params.get("keyword") or "RTX 5070 Ti").strip()
        sort_by = str(params.get("sort_by") or "default").strip()

        extra_args.extend(["--count", str(count), "--keyword", keyword, "--sort", sort_by])
        if req.resume:
            extra_args.append("--resume")
        else:
            extra_args.append("--fresh")

        try:
            save_crawler_params({"target_count": count, "keyword": keyword, "sort_by": sort_by})
        except Exception:
            pass

    run_id = runner.execute_async(script_name=script_name, scenario=req.scenario, extra_args=extra_args)
    return {"run_id": run_id, "status": "STARTED"}

@app.get("/api/crawler/params")
def get_crawler_params_endpoint():
    """获取采数任务当前配置参数 (品类、数量、排序方式)"""
    return get_crawler_params()

@app.post("/api/crawler/params")
def save_crawler_params_endpoint(params: Dict[str, Any] = Body(...)):
    """持久化保存采数任务配置参数"""
    saved = save_crawler_params(params)
    return {"message": "参数配置已更新并保存", "params": saved}

@app.post("/api/runner/cancel")
@app.post("/api/runner/stop")
def stop_execution(req: Optional[Dict[str, Any]] = None):
    run_id = req.get("run_id") if req else None
    stopped = runner.stop_run(run_id)
    return {"success": True, "stopped": stopped}

@app.post("/api/runner/pause")
def pause_execution(req: Optional[Dict[str, Any]] = None):
    run_id = req.get("run_id") if req else None
    paused = runner.pause_run(run_id)
    return {"success": True, "paused": paused, "message": "任务已安全暂停，断点已持久化保存"}

def _resolve_checkpoint_meta(scenario: str):
    date_str = time.strftime("%Y-%m-%d")
    reports_dir = os.path.join(STORAGE_ROOT, "reports", date_str)
    os.makedirs(reports_dir, exist_ok=True)
    if scenario == "pdd_beer_top15":
        ckpt_file = os.path.join(reports_dir, "pdd_beer_checkpoint.json")
        data_file = os.path.join(reports_dir, "pdd_beer_top15.json")
        default_target = 15
        task_name = "pdd_beer_top15"
    elif scenario == "pdd_gpu":
        ckpt_file = os.path.join(reports_dir, "pdd_crawler_checkpoint.json")
        data_file = os.path.join(reports_dir, "pdd_gtx5070ti_16g.json")
        try:
            cfg = get_crawler_params()
            default_target = int(cfg.get("target_count") or 100)
        except Exception:
            default_target = 100
        task_name = "pdd_gtx5070ti_16g"
    else:
        safe_sc = re.sub(r'[^a-zA-Z0-9_-]', '_', scenario)
        ckpt_file = os.path.join(reports_dir, f"{safe_sc}_checkpoint.json")
        data_file = os.path.join(reports_dir, f"{safe_sc}.json")
        default_target = 100
        task_name = safe_sc
    return reports_dir, ckpt_file, data_file, default_target, task_name

@app.get("/api/crawler/checkpoint")
def get_crawler_checkpoint(scenario: str = Query("pdd_gpu")):
    """获取采集任务当前断点状态 (通用支持各类电商与垂直采数场景)"""
    reports_dir, ckpt_file, json_file, default_target, task_name = _resolve_checkpoint_meta(scenario)
    
    # 优先读取明确的断点文件
    target_file = ckpt_file if os.path.exists(ckpt_file) else (json_file if os.path.exists(json_file) else None)
    if not target_file:
        return {
            "scenario": scenario,
            "has_checkpoint": False,
            "completed_count": 0,
            "target_count": default_target,
            "status": "RESET"
        }
    
    try:
        with open(target_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                status = data.get("status", "PAUSED")
                items = data.get("items", [])
                completed = data.get("current_count", len(items))
                target = data.get("target_count", default_target)
                last_updated = data.get("last_updated", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(target_file))))
                latest_title = items[-1]["title"] if items else ""

                # 显式 RESET 或已采集为 0 项时直接返回全新状态，禁止恢复
                if status == "RESET" or completed == 0:
                    return {
                        "scenario": scenario,
                        "has_checkpoint": False,
                        "completed_count": 0,
                        "target_count": target,
                        "status": "RESET",
                        "last_updated": last_updated,
                        "latest_title": ""
                    }
            elif isinstance(data, list):
                items = data
                completed = len(items)
                target = default_target
                status = "PAUSED"
                last_updated = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(target_file)))
                latest_title = items[-1]["title"] if items else ""
            else:
                return {
                    "scenario": scenario,
                    "has_checkpoint": False,
                    "completed_count": 0,
                    "target_count": default_target,
                    "status": "RESET"
                }

            return {
                "scenario": scenario,
                "has_checkpoint": completed > 0,
                "completed_count": completed,
                "target_count": target,
                "status": status,
                "last_updated": last_updated,
                "latest_title": latest_title
            }
    except Exception as e:
        return {
            "scenario": scenario,
            "has_checkpoint": False,
            "completed_count": 0,
            "target_count": default_target,
            "status": "RESET",
            "error": str(e)
        }

@app.post("/api/crawler/checkpoint/reset")
def reset_crawler_checkpoint(scenario: str = Query("pdd_gpu")):
    """清空指定场景的断点存档以支持全新从头采集"""
    import shutil
    reports_dir, ckpt_file, json_file, default_target, task_name = _resolve_checkpoint_meta(scenario)
    date_str = time.strftime("%Y-%m-%d")

    # 1. 归档当日已抓取的 json 数据，防止误作为有效断点回流，同时保障历史数据安全
    if os.path.exists(json_file):
        try:
            ts = time.strftime("%H%M%S")
            base_name = os.path.splitext(os.path.basename(json_file))[0]
            archive_file = os.path.join(reports_dir, f"{base_name}_archived_{ts}.json")
            shutil.move(json_file, archive_file)
        except Exception:
            pass

    # 2. 写入显式 RESET 状态断点记录
    reset_payload = {
        "task_name": task_name,
        "date": date_str,
        "target_count": default_target,
        "current_count": 0,
        "status": "RESET",
        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "items": []
    }
    try:
        with open(ckpt_file, "w", encoding="utf-8") as f:
            json.dump(reset_payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return {
        "message": f"任务 [{scenario}] 断点记录已成功清空重置，下次派发将从第 1 个商品全新开始",
        "scenario": scenario,
        "has_checkpoint": False,
        "completed_count": 0,
        "target_count": default_target,
        "status": "RESET"
    }


@app.get("/api/reports/pdd-excel")
def download_pdd_excel(keyword: Optional[str] = None, mode: str = "clean", batch_id: Optional[str] = None):
    """
    导出拼多多采数 Excel 报表：
    1. 默认 mode='clean'：直通质检清洗与人工终审层，实时剔除 REJECTED 垃圾项，应用纠偏与实测价，生成纯净版报表；
    2. 若 mode='raw' 或未进行质检：回退下载爬虫原始落盘 Excel 文件。
    """
    if not keyword:
        try:
            cfg = get_crawler_params()
            keyword = cfg.get("keyword", "RTX 5070 Ti")
        except Exception:
            keyword = "RTX 5070 Ti"
            
    safe_kw = re.sub(r'[\s/\\:\*\?"<>\|]+', '_', keyword).strip('_') or "goods"
    date_str = time.strftime("%Y-%m-%d")
    out_dir = os.path.join(STORAGE_ROOT, "reports", date_str)
    os.makedirs(out_dir, exist_ok=True)
    
    # 模式一：纯净版（已同步盲审剔除与价格纠偏）
    if mode == "clean":
        clean_items = get_clean_report_items(script_name="pdd_gpu_crawler", batch_id=batch_id, keyword=keyword)
        if not clean_items and "5070" in keyword:
            sync_quality_from_latest()
            clean_items = get_clean_report_items(script_name="pdd_gpu_crawler", batch_id=batch_id, keyword=keyword)
            
        if clean_items:
            clean_excel_path = os.path.join(out_dir, f"pdd_{safe_kw}_clean.xlsx")
            export_clean_excel(clean_items, clean_excel_path)
            fn = f"拼多多_{keyword}_质检纯净版报表.xlsx"
            encoded_fn = quote(fn)
            return FileResponse(
                path=clean_excel_path,
                filename=fn,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_fn}"}
            )

    # 模式二：爬虫原始落地版
    report_file = os.path.join(STORAGE_ROOT, "reports", date_str, f"pdd_{safe_kw}.xlsx")
    latest_file = os.path.join(STORAGE_ROOT, "reports", f"pdd_{safe_kw}_latest.xlsx")
    fallback_5070 = os.path.join(STORAGE_ROOT, "reports", date_str, "pdd_gtx5070ti_16g.xlsx")
    fallback_5070_latest = os.path.join(STORAGE_ROOT, "reports", "pdd_gtx5070ti_16g_latest.xlsx")
    
    target = None
    for cand in [report_file, latest_file, fallback_5070, fallback_5070_latest]:
        if os.path.exists(cand):
            target = cand
            break
            
    if target and os.path.exists(target):
        fn = f"拼多多_{keyword}_商品监控报表.xlsx"
        encoded_fn = quote(fn)
        return FileResponse(
            path=target,
            filename=fn,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_fn}"}
        )
        
    raise HTTPException(status_code=404, detail=f"未找到品类 [{keyword}] 的 Excel 报表，请先执行采数或检查生成记录")

@app.get("/api/quality/tasks")
def get_quality_tasks_endpoint():
    """获取所有脚本任务维度的质检与盲审概览数据（支持文件夹式任务切换）"""
    return get_audit_tasks()

@app.get("/api/quality/stats")
def get_quality_stats_endpoint(script_name: str = Query("ALL"), batch_id: Optional[str] = None):
    return get_quality_stats(script_name=script_name, batch_id=batch_id)

@app.get("/api/quality/audits")
def get_quality_audits_endpoint(
    script_name: str = Query("ALL"),
    status: str = Query("ALL"),
    batch_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    items = get_audit_items(script_name=script_name, status=status, batch_id=batch_id, limit=limit, offset=offset)
    stats = get_quality_stats(script_name=script_name, batch_id=batch_id)
    return {"items": items, "stats": stats, "total": len(items)}

@app.post("/api/quality/audits/{audit_id}/action")
def update_audit_action_endpoint(audit_id: int, req: AuditActionRequest):
    ok = update_audit_action(audit_id, req.action, req.modified_price)
    if not ok:
        raise HTTPException(status_code=404, detail="Audit item not found")
    item = get_single_audit_item(audit_id)
    s_name = item.get("script_name") if item else "ALL"
    return {"message": "人工核验状态已更新", "stats": get_quality_stats(script_name=s_name)}

@app.post("/api/quality/audits/{audit_id}/ai-diagnose")
def ai_diagnose_single_audit(audit_id: int):
    """针对指定存疑审计项，触发 DeepSeek AI 智能会诊诊断"""
    try:
        diag = diagnose_audit_item(audit_id)
        item = get_single_audit_item(audit_id)
        return {
            "message": "DeepSeek AI 诊断已完成",
            "diagnosis": diag,
            "item": item,
            "stats": get_quality_stats(script_name=item.get("script_name") if item else "ALL")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 诊断失败: {str(e)}")

@app.post("/api/quality/audits/batch-ai-diagnose")
def batch_ai_diagnose_endpoint(script_name: str = Query("pdd_gpu_crawler"), max_items: int = Query(15, ge=1, le=50)):
    """对指定任务分类下的全部待审/异常项发起一键 DeepSeek 批量诊断"""
    try:
        res = batch_diagnose_script_anomalies(script_name=script_name, max_items=max_items)
        return {
            "message": f"DeepSeek 批量诊断已完成，共分析 {res['diagnosed_count']} 条异常数据",
            "data": res,
            "stats": get_quality_stats(script_name=script_name)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量诊断失败: {str(e)}")

@app.post("/api/quality/audits/{audit_id}/adopt-ai")
def adopt_ai_verdict_endpoint(audit_id: int):
    """一键采纳 DeepSeek AI 诊断裁决，直接生效到审核结果"""
    item = adopt_ai_verdict(audit_id)
    if not item:
        raise HTTPException(status_code=404, detail="条目不存在或未包含有效 AI 裁决")
    return {
        "message": f"已成功采纳 AI 诊断裁决: {item.get('human_action')}",
        "item": item,
        "stats": get_quality_stats(script_name=item.get("script_name"))
    }

@app.post("/api/quality/audits/{audit_id}/replay-probe")
def replay_probe_endpoint(audit_id: int):
    """
    单品独立步骤回放与 AI 深度确诊沙箱探针 (串行安全队列模式):
    1. 推入 ProbeQueueManager 串行任务队列，防止多探针争抢物理真机
    2. 物理真机独占执行，任务排队等待，不被轻易挤掉
    3. 支持提前插队、上下移动排序、取消删除
    """
    res = probe_queue_manager.add_task(audit_id)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message", "加入探针队列失败"))
    return {
        "message": res.get("message"),
        "task_id": res.get("task_id"),
        "status": res.get("status"),
        "position": res.get("position"),
        "task": res.get("task"),
        "queue_status": probe_queue_manager.get_status()
    }

# ==================== 探针任务队列管理调度 API ====================

@app.get("/api/probe/queue")
def get_probe_queue_endpoint():
    """获取探针任务队列全局快照 (正在执行、排队列表、活跃映射、历史记录)"""
    return probe_queue_manager.get_status()

@app.post("/api/probe/queue/prioritize")
def prioritize_probe_task_endpoint(req: ProbeQueueActionRequest):
    """提前任务至等待队列首位 (置顶插队，下一个立即执行)"""
    if not req.task_id:
        raise HTTPException(status_code=400, detail="task_id 不能为空")
    res = probe_queue_manager.prioritize_task(req.task_id)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message"))
    return {"message": res.get("message"), "position": res.get("position"), "queue": probe_queue_manager.get_status()}

@app.post("/api/probe/queue/move")
def move_probe_task_endpoint(req: ProbeQueueActionRequest):
    """上移或下移排队任务次序"""
    if not req.task_id:
        raise HTTPException(status_code=400, detail="task_id 不能为空")
    res = probe_queue_manager.move_task(req.task_id, direction=req.direction or "up")
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message"))
    return {"message": res.get("message"), "position": res.get("position"), "queue": probe_queue_manager.get_status()}

@app.post("/api/probe/queue/reorder")
def reorder_probe_queue_endpoint(req: ProbeQueueActionRequest):
    """全量自定义重排队列"""
    if not req.task_ids:
        raise HTTPException(status_code=400, detail="task_ids 列表不能为空")
    res = probe_queue_manager.reorder_queue(req.task_ids)
    return {"message": res.get("message"), "queue": probe_queue_manager.get_status()}

@app.post("/api/probe/queue/remove")
def remove_probe_task_endpoint(req: ProbeQueueActionRequest):
    """从等待队列中删除/移除指定任务"""
    if not req.task_id:
        raise HTTPException(status_code=400, detail="task_id 不能为空")
    res = probe_queue_manager.remove_task(req.task_id)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message"))
    return {"message": res.get("message"), "queue": probe_queue_manager.get_status()}

@app.post("/api/probe/queue/clear")
def clear_probe_queue_endpoint():
    """清空所有排队中的探针任务 (不影响当前正在真机执行的任务)"""
    res = probe_queue_manager.clear_queue()
    return {"message": res.get("message"), "cleared_count": res.get("cleared_count", 0), "queue": probe_queue_manager.get_status()}

@app.post("/api/probe/queue/stop-current")
def stop_current_probe_task_endpoint():
    """强行中止当前正在真机执行的探针任务"""
    res = probe_queue_manager.stop_current()
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message"))
    return {"message": res.get("message"), "queue": probe_queue_manager.get_status()}

@app.get("/api/quality/audits/{audit_id}/replay-trace")
def get_replay_trace_endpoint(audit_id: int):
    """获取单件商品自动化步骤重跑捕获的证据链详情与截图路径"""
    item = get_single_audit_item(audit_id)
    if not item:
        raise HTTPException(status_code=404, detail="条目不存在")
    return {
        "audit_id": audit_id,
        "title": item.get("title"),
        "replay_status": item.get("replay_status", "NOT_RUN"),
        "replay_trace": item.get("replay_trace", []),
        "corrected_price": item.get("corrected_price"),
        "replay_summary": item.get("replay_summary", "")
    }

@app.post("/api/quality/audits/{audit_id}/apply-correction")
def apply_correction_endpoint(audit_id: int):
    """一键应用单品回放探针纠偏出的真实价格，并自动核准为合格数据"""
    item = apply_corrected_price(audit_id)
    if not item:
        raise HTTPException(status_code=400, detail="该条目未包含有效的纠偏价格")
    return {
        "message": f"已将真实纠偏价格 ¥{item.get('modified_price')} 应用生效并核准",
        "item": item,
        "stats": get_quality_stats(script_name=item.get("script_name"))
    }

@app.post("/api/quality/compile-rule")
def compile_quality_rule_endpoint(payload: Dict[str, Any] = Body(...)):
    """业务质检意图编译 Agent 接口：将自然语言诉求单次编译为低成本结构化 Profile"""
    intent_prompt = payload.get("intent_prompt", "")
    keyword = payload.get("keyword", "")
    profile = compile_quality_rule(intent_prompt=intent_prompt, keyword=keyword)
    auto_apply = payload.get("auto_apply", False)
    if auto_apply:
        save_crawler_params({
            "intent_prompt": intent_prompt,
            "keyword": profile.get("suggested_keyword") or keyword,
            "target_count": profile.get("suggested_count", 100),
            "sort_by": profile.get("suggested_sort_by", "default"),
            "rule_profile": profile
        })
    return {
        "status": "SUCCESS",
        "profile": profile,
        "message": "质检规则编译成功"
    }

@app.post("/api/quality/sync-from-latest")
def sync_quality_from_latest(script_name: str = Query("ALL")):
    """扫描并质检最新采集产物，按任务类别驱动同步入库 (支持指定任务或全量扫描)"""
    import glob
    total_synced = 0
    last_batch_id = time.strftime("%Y-%m-%d")
    last_summary = {}

    # 1. 啤酒畅销榜单数据同步
    if script_name in ("ALL", "pdd_beer_top15", "pdd_beer"):
        beer_jsons = glob.glob(os.path.join(STORAGE_ROOT, "reports", "*", "pdd_beer_top15.json"))
        if beer_jsons:
            latest_beer = sorted(beer_jsons)[-1]
            try:
                with open(latest_beer, "r", encoding="utf-8") as f:
                    b_data = json.load(f)
                    if isinstance(b_data, dict):
                        b_data = b_data.get("items", [])
                beer_profile = {
                    "task_category": "BEER_RANK",
                    "brand_must_contain": [],
                    "category_guard": ["啤酒", "纯生", "黑啤", "白啤", "原麦", "雪花", "青岛", "珠江", "燕京", "喜力", "乌苏", "百威"],
                    "exclude_keywords": ["苏打水", "果汁", "饮料", "杯", "开瓶器"],
                    "check_memory_conflict": False,
                    "check_chip_conflict": False,
                    "iqr_multiplier": 1.5
                }
                b_results, b_summary = audit_batch(b_data, profile=beer_profile)
                b_batch_id = os.path.basename(os.path.dirname(latest_beer))
                b_count = save_audit_batch(b_batch_id, b_results, script_name="pdd_beer_top15", task_title="拼多多-品牌啤酒畅销榜Top15", category="电商榜单")
                total_synced += b_count
                last_batch_id = b_batch_id
                last_summary = b_summary
            except Exception as e:
                print(f"[Sync] 啤酒数据质检同步异常: {e}")

    # 2. 拼多多显卡/数码采数数据同步
    if script_name in ("ALL", "pdd_gpu_crawler"):
        gpu_jsons = glob.glob(os.path.join(STORAGE_ROOT, "reports", "*", "pdd_gtx5070ti_16g.json"))
        if gpu_jsons:
            latest_gpu = sorted(gpu_jsons)[-1]
            try:
                with open(latest_gpu, "r", encoding="utf-8") as f:
                    g_data = json.load(f)
                    if isinstance(g_data, dict):
                        g_data = g_data.get("items", [])
                cfg = get_crawler_params()
                kw = cfg.get("keyword", "RTX 5070 Ti")
                profile = cfg.get("rule_profile")
                g_results, g_summary = audit_batch(g_data, profile=profile)
                g_batch_id = os.path.basename(os.path.dirname(latest_gpu))
                g_count = save_audit_batch(g_batch_id, g_results, script_name="pdd_gpu_crawler", task_title=f"拼多多-{kw}采数", category="电商采数")
                total_synced += g_count
                last_batch_id = g_batch_id
                last_summary = g_summary
            except Exception as e:
                print(f"[Sync] 显卡数据质检同步异常: {e}")

    target_script = script_name if script_name != "ALL" else ("pdd_beer_top15" if total_synced > 0 else "ALL")
    return {
        "message": f"成功同步并质检 {total_synced} 条数据",
        "batch_id": last_batch_id,
        "summary": last_summary,
        "stats": get_quality_stats(script_name=target_script, batch_id=last_batch_id)
    }

@app.get("/api/reports/{script_name}/excel-clean")
def download_script_clean_excel(script_name: str, batch_id: Optional[str] = None, keyword: Optional[str] = None):
    """一键导出指定分类脚本通过质检与人工核验的高纯净度 Excel 报表"""
    date_str = time.strftime("%Y-%m-%d")
    out_dir = os.path.join(STORAGE_ROOT, "reports", date_str)
    os.makedirs(out_dir, exist_ok=True)

    if script_name == "pdd_gpu_crawler" and not keyword:
        try:
            cfg = get_crawler_params()
            keyword = cfg.get("keyword", "RTX 5070 Ti")
        except Exception:
            keyword = "RTX 5070 Ti"

    safe_kw = re.sub(r'[\s/\\:\*\?"<>\|]+', '_', keyword).strip('_') if keyword else script_name
    clean_excel_path = os.path.join(out_dir, f"{script_name}_{safe_kw}_clean.xlsx")

    clean_items = get_clean_report_items(script_name=script_name, batch_id=batch_id, keyword=keyword)
    if not clean_items and script_name == "pdd_gpu_crawler":
        sync_quality_from_latest()
        clean_items = get_clean_report_items(script_name=script_name, batch_id=batch_id, keyword=keyword)

    if not clean_items:
        raise HTTPException(status_code=404, detail=f"任务 [{script_name}] 暂无可用的纯净数据项")

    export_clean_excel(clean_items, clean_excel_path)

    if script_name == "pdd_gpu_crawler":
        filename = f"拼多多_{keyword}_质检纯净版报表.xlsx"
    elif script_name == "bilibili_video_task":
        filename = "B站UP主视频数据监测_质检纯净版报表.xlsx"
    elif script_name == "sample_mobile_task":
        filename = "移动端巡检自愈_质检纯净版报表.xlsx"
    else:
        filename = f"{script_name}_质检纯净版报表.xlsx"

    encoded_fn = quote(filename)
    return FileResponse(
        path=clean_excel_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_fn}"}
    )

@app.get("/api/reports/pdd-excel-clean")
def download_clean_pdd_excel(batch_id: Optional[str] = None):
    """向下兼容老路径"""
    return download_script_clean_excel(script_name="pdd_gpu_crawler", batch_id=batch_id)

@app.get("/api/reports/pdd_beer_top15/excel")
def download_beer_top15_excel():
    """下载最新的拼多多品牌啤酒畅销榜Top15带图Excel报表"""
    date_str = time.strftime("%Y-%m-%d")
    excel_path = os.path.join(STORAGE_ROOT, "reports", date_str, "pdd_beer_top15.xlsx")
    if not os.path.exists(excel_path):
        import glob
        all_excels = glob.glob(os.path.join(STORAGE_ROOT, "reports", "*", "pdd_beer_top15.xlsx"))
        if not all_excels:
            raise HTTPException(status_code=404, detail="暂未生成啤酒畅销榜报表，请先在控制台执行一次任务")
        excel_path = sorted(all_excels)[-1]

    filename = f"拼多多_品牌啤酒畅销榜Top15_{date_str}.xlsx"
    encoded_fn = quote(filename)
    return FileResponse(
        path=excel_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_fn}"}
    )


@app.get("/api/config")
def get_configs():
    return get_all_configs()

@app.post("/api/config")
def update_configs(req: ConfigUpdateRequest):
    updates = req.dict(exclude_unset=True)
    for k, v in updates.items():
        if v is not None:
            set_config(k, str(v))
    return {"message": "配置更新成功", "configs": get_all_configs()}

@app.post("/api/config/test-alert")
def test_alert(req: TestAlertRequest):
    res = notifier.send_test_message(req.channel, req.webhook_url)
    return res

@app.post("/api/seed-demo")
def seed_demo():
    """预置典型案例数据，便于开箱全流程体验"""
    # 模拟执行几个经典场景
    scenarios = ["normal", "mock_permission", "mock_update", "mock_timeout"]
    created_ids = []
    for s in scenarios:
        rid = runner.execute_async(scenario=s)
        created_ids.append(rid)
    return {"message": "已成功派发4组典型场景运行示例", "run_ids": created_ids}

# 静态首页挂载
@app.get("/")
def serve_index():
    index_file = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "Frontend index.html not found"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
