import os
import sqlite3
import json
from datetime import datetime
from typing import Dict, Any, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE_DIR = os.path.join(PROJECT_ROOT, "storage")
DB_DIR = os.path.join(STORAGE_DIR, "database")
DB_PATH = os.path.join(DB_DIR, "monitor.db")

def get_connection() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = get_connection()
    cursor = conn.cursor()
    
    # 运行历史与异常详情表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        script_name TEXT NOT NULL,
        device_name TEXT,
        device_id TEXT,
        status TEXT NOT NULL,           -- RUNNING, SUCCESS, FAILED
        scenario TEXT DEFAULT 'normal', -- normal, mock_permission, mock_update, mock_timeout, real_device
        start_time TEXT NOT NULL,
        end_time TEXT,
        duration REAL DEFAULT 0.0,
        error_type TEXT,                -- PERMISSION_INTERRUPTION, APP_UPDATE_INTERRUPTION, etc.
        error_message TEXT,
        ai_analysis TEXT,               -- JSON 格式存储诊断报告
        suggested_action TEXT,
        screenshot_path TEXT,
        logs TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # 监控与告警配置表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS configs (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 数据质量与人工盲审表 (Data Quality & HITL Audit)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS data_audits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        script_name TEXT NOT NULL DEFAULT 'pdd_gpu_crawler',
        task_title TEXT NOT NULL DEFAULT '拼多多-RTX5070Ti 16G显卡采数',
        category TEXT NOT NULL DEFAULT '电商采数',
        batch_id TEXT,
        item_seq INTEGER,
        title TEXT NOT NULL,
        price_str TEXT,
        price_num REAL,
        deviation_pct REAL DEFAULT 0.0,
        risk_tags TEXT,                   -- JSON 格式存储标签列表
        confidence_score INTEGER DEFAULT 100,
        audit_status TEXT NOT NULL,       -- PASS / NEED_AUDIT / REJECT
        human_action TEXT DEFAULT 'PENDING', -- PENDING / APPROVED / REJECTED / MODIFIED
        modified_price REAL,
        shop TEXT,
        tags TEXT,
        link TEXT,
        raw_shot TEXT,
        thumb_path TEXT,
        ai_verdict TEXT,                  -- PASS / REJECT / WARNING
        ai_confidence INTEGER DEFAULT 0,  -- 0 - 100
        ai_root_cause TEXT,               -- 根因定性代码与简述
        ai_analysis TEXT,                 -- DeepSeek 智能分析详细剖析
        ai_suggestion TEXT,               -- 脚本修复优化建议
        ai_action TEXT,                   -- 推荐采纳动作 (REJECTED / APPROVED)
        ai_diagnosed_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audits_status ON data_audits(audit_status, human_action)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audits_batch ON data_audits(batch_id)")

    # 动态平滑迁移老版本数据表字段
    try:
        existing_cols = [row[1] for row in cursor.execute("PRAGMA table_info(data_audits)").fetchall()]
        new_cols = {
            "script_name": "TEXT DEFAULT 'pdd_gpu_crawler'",
            "task_title": "TEXT DEFAULT '拼多多-RTX5070Ti 16G显卡采数'",
            "category": "TEXT DEFAULT '电商采数'",
            "ai_verdict": "TEXT",
            "ai_confidence": "INTEGER DEFAULT 0",
            "ai_root_cause": "TEXT",
            "ai_analysis": "TEXT",
            "ai_suggestion": "TEXT",
            "ai_action": "TEXT",
            "ai_diagnosed_at": "TIMESTAMP",
            "replay_status": "TEXT DEFAULT 'NOT_RUN'",
            "replay_trace": "TEXT",
            "corrected_price": "REAL",
            "replay_summary": "TEXT"
        }
        for col_name, col_def in new_cols.items():
            if col_name not in existing_cols:
                cursor.execute(f"ALTER TABLE data_audits ADD COLUMN {col_name} {col_def}")
                
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audits_script ON data_audits(script_name)")
    except Exception as e:
        print(f"Audit table migration notice: {e}")
    
    # 初始默认配置 (预置 DeepSeek 智能诊断引擎)
    default_configs = {
        "feishu_webhook": "",
        "dingtalk_webhook": "",
        "wecom_webhook": "",
        "custom_webhook": "",
        "alert_enabled": "true",
        "alert_on_failure_only": "true",
        "ai_engine": "openai_compatible",        # heuristic / openai_compatible
        "ai_api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "ai_api_base": os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
        "ai_model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    }
    
    for k, v in default_configs.items():
        cursor.execute("INSERT OR IGNORE INTO configs (key, value) VALUES (?, ?)", (k, v))

    # 若环境变量中配置了 API Key，且数据库中为空，则自动同步环境变量
    env_api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if env_api_key:
        cursor.execute("UPDATE configs SET value = ? WHERE key = 'ai_api_key' AND (value = '' OR value IS NULL)", (env_api_key,))
    cursor.execute("UPDATE configs SET value = 'https://api.deepseek.com' WHERE key = 'ai_api_base' AND (value = '' OR value = 'https://api.openai.com/v1')")
    cursor.execute("UPDATE configs SET value = 'deepseek-chat' WHERE key = 'ai_model' AND (value = '' OR value = 'gpt-4o-mini')")
        
    conn.commit()
    conn.close()

    # 启动时自动清理上一轮遗留的未闭环 RUNNING 记录
    cleanup_stale_running_tasks()

def cleanup_stale_running_tasks():
    """服务启动或重连时，自动清理上一轮遗留的未闭环 RUNNING 孤儿记录"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            UPDATE runs 
            SET status = 'FAILED', 
                end_time = ?,
                error_type = 'STALE_PROCESS_CLEANUP', 
                error_message = '控制台服务重载，前序未正常闭环任务已自动安全置为中断状态。' 
            WHERE status = 'RUNNING'
        """, (now_str,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB Cleanup Error]: {e}")

def create_run(run_data: Dict[str, Any]) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    INSERT INTO runs (
        id, script_name, device_name, device_id, status, scenario,
        start_time, end_time, duration, error_type, error_message,
        ai_analysis, suggested_action, screenshot_path, logs
    ) VALUES (
        :id, :script_name, :device_name, :device_id, :status, :scenario,
        :start_time, :end_time, :duration, :error_type, :error_message,
        :ai_analysis, :suggested_action, :screenshot_path, :logs
    )
    """, run_data)
    
    conn.commit()
    conn.close()
    return run_data["id"]

def update_run(run_id: str, update_data: Dict[str, Any]):
    conn = get_connection()
    cursor = conn.cursor()
    
    fields = []
    values = []
    for k, v in update_data.items():
        fields.append(f"{k} = ?")
        values.append(v)
    values.append(run_id)
    
    query = f"UPDATE runs SET {', '.join(fields)} WHERE id = ?"
    cursor.execute(query, values)
    conn.commit()
    conn.close()

def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        d = dict(row)
        if d.get("ai_analysis"):
            try:
                d["ai_analysis_data"] = json.loads(d["ai_analysis"])
            except Exception:
                d["ai_analysis_data"] = None
        return d
    return None

def get_runs_with_total(status: Optional[str] = None, limit: int = 15, offset: int = 0) -> Dict[str, Any]:
    """分页获取运行历史列表及精确总数"""
    conn = get_connection()
    cursor = conn.cursor()
    
    where_clause = ""
    params = []
    if status and status.upper() != 'ALL':
        where_clause = "WHERE status = ?"
        params.append(status.upper())
        
    count_query = f"SELECT COUNT(*) FROM runs {where_clause}"
    cursor.execute(count_query, params)
    total = cursor.fetchone()[0]
    
    data_query = f"SELECT * FROM runs {where_clause} ORDER BY start_time DESC LIMIT ? OFFSET ?"
    cursor.execute(data_query, params + [limit, offset])
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        d = dict(row)
        if d.get("ai_analysis"):
            try:
                d["ai_analysis_data"] = json.loads(d["ai_analysis"])
            except Exception:
                d["ai_analysis_data"] = None
        results.append(d)
        
    return {"runs": results, "total": total, "limit": limit, "offset": offset}

def get_runs(status: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    return get_runs_with_total(status=status, limit=limit, offset=offset)["runs"]

def get_dashboard_stats() -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM runs")
    total_runs = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM runs WHERE status = 'SUCCESS'")
    success_runs = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM runs WHERE status = 'FAILED'")
    failed_runs = cursor.fetchone()[0]
    
    cursor.execute("SELECT AVG(duration) FROM runs WHERE duration > 0")
    avg_duration_row = cursor.fetchone()[0]
    avg_duration = round(avg_duration_row, 1) if avg_duration_row else 0.0
    
    success_rate = round((success_runs / total_runs * 100), 1) if total_runs > 0 else 100.0
    
    # 异常分类统计
    cursor.execute("""
        SELECT error_type, COUNT(*) as count 
        FROM runs 
        WHERE status = 'FAILED' AND error_type IS NOT NULL AND error_type != '' 
        GROUP BY error_type
    """)
    error_distribution = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    return {
        "total_runs": total_runs,
        "success_runs": success_runs,
        "failed_runs": failed_runs,
        "success_rate": success_rate,
        "avg_duration": avg_duration,
        "error_distribution": error_distribution
    }

def get_config(key: str, default: Any = None) -> Any:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM configs WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row["value"] if row else default

def set_config(key: str, value: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO configs (key, value, updated_at) 
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
    """, (key, str(value)))
    conn.commit()
    conn.close()

def get_all_configs() -> Dict[str, str]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM configs")
    rows = cursor.fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}

def get_crawler_params() -> Dict[str, Any]:
    """获取拼多多自动化采数核心运行参数 (质检意图、品类规则、数量、排序)"""
    from backend.quality import compile_quality_rule
    raw = get_config("pdd_crawler_params", None)
    default_params = {
        "keyword": "RTX 5070 Ti",
        "target_count": 100,
        "sort_by": "default",  # default: 综合推荐, sales: 销量优先, price_asc: 价格优先
        "intent_prompt": "采集 RTX 5070 Ti 显卡 16G 规格，排除 12G 起步价引流与非整卡配件",
        "rule_profile": None
    }
    if not raw:
        default_params["rule_profile"] = compile_quality_rule(default_params["intent_prompt"], default_params["keyword"])
        return default_params
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            default_params.update(data)
        if not default_params.get("rule_profile"):
            default_params["rule_profile"] = compile_quality_rule(default_params.get("intent_prompt", ""), default_params.get("keyword", ""))
        return default_params
    except Exception:
        default_params["rule_profile"] = compile_quality_rule(default_params["intent_prompt"], default_params["keyword"])
        return default_params

def save_crawler_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """持久化保存拼多多自动化采数运行参数与规则Profile"""
    from backend.quality import compile_quality_rule
    current = get_crawler_params()
    current.update(params)
    # 若更新了 intent_prompt 或 keyword，但未显式传入 rule_profile，自动触发单次规则编译
    if ("intent_prompt" in params or "keyword" in params) and "rule_profile" not in params:
        current["rule_profile"] = compile_quality_rule(current.get("intent_prompt", ""), current.get("keyword", ""))
    set_config("pdd_crawler_params", json.dumps(current, ensure_ascii=False))
    return current

def clear_all_runs():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM runs")
    conn.commit()
    conn.close()

def save_audit_batch(batch_id: str, audited_items: List[Dict[str, Any]], script_name: str = "pdd_gpu_crawler", task_title: Optional[str] = None, category: str = "电商采数") -> int:
    """批量保存质检评估数据"""
    if not task_title:
        try:
            cfg = get_crawler_params()
            kw = cfg.get("keyword", "RTX 5070 Ti")
            task_title = f"拼多多-{kw}采数" if script_name == "pdd_gpu_crawler" else "移动端数据采数"
        except Exception:
            task_title = "拼多多-商品采数"

    conn = get_connection()
    cursor = conn.cursor()
    
    # 避免单批次重复插入，先清空同一批次记录
    cursor.execute("DELETE FROM data_audits WHERE batch_id = ? AND script_name = ?", (batch_id, script_name))
    
    count = 0
    for item in audited_items:
        cursor.execute("""
        INSERT INTO data_audits (
            script_name, task_title, category,
            batch_id, item_seq, title, price_str, price_num, deviation_pct,
            risk_tags, confidence_score, audit_status, human_action,
            shop, tags, link, raw_shot, thumb_path,
            ai_verdict, ai_confidence, ai_root_cause, ai_analysis, ai_suggestion, ai_action
        ) VALUES (
            ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?
        )
        """, (
            script_name,
            task_title,
            category,
            batch_id,
            item.get("seq", 0),
            item.get("title", ""),
            item.get("price_str", ""),
            item.get("price_num", 0.0),
            item.get("deviation_pct", 0.0),
            json.dumps(item.get("risk_tags", []), ensure_ascii=False),
            item.get("confidence_score", 100),
            item.get("audit_status", "PASS"),
            item.get("human_action", "PENDING"),
            item.get("shop", ""),
            item.get("tags", ""),
            item.get("link", ""),
            item.get("raw_shot", ""),
            item.get("thumb_path", ""),
            item.get("ai_verdict", None),
            item.get("ai_confidence", 0),
            item.get("ai_root_cause", None),
            item.get("ai_analysis", None),
            item.get("ai_suggestion", None),
            item.get("ai_action", None)
        ))
        count += 1
        
    conn.commit()
    conn.close()
    return count

def get_audit_tasks() -> List[Dict[str, Any]]:
    """获取所有脚本任务维度的质检与盲审概览数据（支持文件柜/文件夹式任务切换）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            script_name,
            COALESCE(task_title, script_name) as task_title,
            COALESCE(category, '业务采数') as category,
            COUNT(*) as total_items,
            SUM(CASE WHEN audit_status = 'PASS' THEN 1 ELSE 0 END) as pass_count,
            SUM(CASE WHEN audit_status = 'NEED_AUDIT' THEN 1 ELSE 0 END) as need_audit_count,
            SUM(CASE WHEN audit_status = 'REJECT' THEN 1 ELSE 0 END) as reject_count,
            SUM(CASE WHEN audit_status IN ('NEED_AUDIT', 'REJECT') AND human_action = 'PENDING' THEN 1 ELSE 0 END) as pending_action,
            SUM(CASE WHEN human_action IN ('APPROVED', 'REJECTED', 'MODIFIED') THEN 1 ELSE 0 END) as reviewed_action,
            SUM(CASE WHEN ai_verdict IS NOT NULL AND ai_verdict != '' THEN 1 ELSE 0 END) as ai_diagnosed_count,
            MAX(created_at) as latest_created_at
        FROM data_audits
        GROUP BY script_name
        ORDER BY pending_action DESC, total_items DESC
    """)
    rows = cursor.fetchall()
    tasks = []
    for r in rows:
        d = dict(r)
        tot = d["total_items"] or 0
        p_cnt = d["pass_count"] or 0
        d["clean_rate"] = round((p_cnt / tot * 100), 1) if tot > 0 else 100.0
        tasks.append(d)
    conn.close()
    return tasks

def get_audit_items(script_name: Optional[str] = None, status: str = "ALL", batch_id: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    """查询质检与待审条目列表（支持按 script_name 隔离）"""
    conn = get_connection()
    cursor = conn.cursor()
    
    query = "SELECT * FROM data_audits"
    conditions = []
    params = []
    
    if script_name and script_name != "ALL":
        conditions.append("script_name = ?")
        params.append(script_name)

    if batch_id:
        conditions.append("batch_id = ?")
        params.append(batch_id)
        
    if status == "PENDING":
        conditions.append("audit_status IN ('NEED_AUDIT', 'REJECT') AND human_action = 'PENDING'")
    elif status == "NEED_AUDIT":
        conditions.append("audit_status = 'NEED_AUDIT'")
    elif status == "REJECT":
        conditions.append("audit_status = 'REJECT'")
    elif status == "PASS":
        conditions.append("audit_status = 'PASS'")
    elif status == "APPROVED":
        conditions.append("human_action = 'APPROVED'")
    elif status == "REJECTED":
        conditions.append("human_action = 'REJECTED'")
        
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
        
    query += " ORDER BY CASE WHEN human_action = 'PENDING' AND audit_status IN ('REJECT', 'NEED_AUDIT') THEN 0 ELSE 1 END, item_seq ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    
    result = []
    for r in rows:
        d = dict(r)
        if d.get("risk_tags"):
            try:
                d["risk_tags"] = json.loads(d["risk_tags"])
            except Exception:
                d["risk_tags"] = []
        else:
            d["risk_tags"] = []
            
        if d.get("replay_trace"):
            try:
                d["replay_trace"] = json.loads(d["replay_trace"])
            except Exception:
                pass
        result.append(d)
        
    conn.close()
    return result

def get_single_audit_item(audit_id: int) -> Optional[Dict[str, Any]]:
    """根据 ID 获取单条质检数据完整记录"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM data_audits WHERE id = ?", (audit_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    if d.get("risk_tags"):
        try:
            d["risk_tags"] = json.loads(d["risk_tags"])
        except Exception:
            d["risk_tags"] = []
    else:
        d["risk_tags"] = []
        
    if d.get("replay_trace"):
        try:
            d["replay_trace"] = json.loads(d["replay_trace"])
        except Exception:
            pass
    return d

def update_audit_action(audit_id: int, action: str, modified_price: Optional[float] = None) -> bool:
    """更新人工盲审动作: APPROVED (通过/真实16G), REJECTED (标记引流/剔除), MODIFIED (修正价格)"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE data_audits 
        SET human_action = ?, modified_price = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (action, modified_price, audit_id))
    affected = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return affected

def update_audit_ai_result(audit_id: int, ai_data: Dict[str, Any]) -> bool:
    """更新 DeepSeek AI 智能诊断探针分析结果"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE data_audits
        SET 
            ai_verdict = ?,
            ai_confidence = ?,
            ai_root_cause = ?,
            ai_analysis = ?,
            ai_suggestion = ?,
            ai_action = ?,
            ai_diagnosed_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        ai_data.get("verdict"),
        ai_data.get("confidence", 85),
        ai_data.get("root_cause"),
        ai_data.get("technical_analysis") or ai_data.get("analysis"),
        ai_data.get("script_fix_suggestion") or ai_data.get("suggestion"),
        ai_data.get("action_suggestion") or ai_data.get("action"),
        audit_id
    ))
    affected = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return affected

def save_audit_replay_trace(
    audit_id: int,
    trace_data: List[Dict[str, Any]],
    corrected_price: Optional[float] = None,
    replay_status: str = "COMPLETED",
    replay_summary: str = ""
) -> bool:
    """保存针对单件商品重新运行步骤捕获的证据链与修正价格"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE data_audits
        SET
            replay_trace = ?,
            corrected_price = ?,
            replay_status = ?,
            replay_summary = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        json.dumps(trace_data, ensure_ascii=False) if isinstance(trace_data, list) else str(trace_data),
        corrected_price,
        replay_status,
        replay_summary,
        audit_id
    ))
    
    # 若重测成功获取到有效实价，且原标价为待获取或 0，自动回写补全标价
    if corrected_price and float(corrected_price) > 0:
        cursor.execute("SELECT price_num, price_str FROM data_audits WHERE id = ?", (audit_id,))
        row = cursor.fetchone()
        if row:
            p_num = float(row["price_num"] or 0.0)
            p_str = str(row["price_str"] or "")
            if p_num <= 0 or "待获取" in p_str:
                formatted_p = f"¥{int(corrected_price)}" if float(corrected_price).is_integer() else f"¥{float(corrected_price):.2f}"
                cursor.execute("""
                    UPDATE data_audits
                    SET price_num = ?, price_str = ?, modified_price = COALESCE(modified_price, ?)
                    WHERE id = ?
                """, (float(corrected_price), formatted_p, float(corrected_price), audit_id))

    affected = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return affected

def apply_corrected_price(audit_id: int, corrected_price: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """将单品重测纠偏出的真实价格应用为最终审核价格，并直接核准为合格"""
    conn = get_connection()
    cursor = conn.cursor()
    if corrected_price is None:
        cursor.execute("SELECT corrected_price FROM data_audits WHERE id = ?", (audit_id,))
        row = cursor.fetchone()
        if row and row["corrected_price"]:
            corrected_price = row["corrected_price"]
        else:
            conn.close()
            return None

    cursor.execute("""
        UPDATE data_audits
        SET
            modified_price = ?,
            human_action = 'APPROVED',
            audit_status = 'PASS',
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (corrected_price, audit_id))
    conn.commit()
    conn.close()
    return get_single_audit_item(audit_id)

def adopt_ai_verdict(audit_id: int) -> Optional[Dict[str, Any]]:
    """一键采纳 AI 诊断建议，自动写入对应 human_action (若有纠偏价格自动同步)"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, ai_action, ai_verdict, corrected_price FROM data_audits WHERE id = ?", (audit_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None
        
    ai_act = row["ai_action"] or ""
    ai_ver = row["ai_verdict"] or ""
    corr_p = row["corrected_price"]
    
    target_action = "REJECTED"
    mod_price = None
    if "APPROVE" in ai_act.upper() or ai_ver in ("PASS", "CONFIRMED_FRAUD_CORRECTED"):
        target_action = "APPROVED"
        if corr_p:
            mod_price = corr_p
    elif "REJECT" in ai_act.upper() or ai_ver in ("REJECT", "FRAUD_EXCLUDED"):
        target_action = "REJECTED"
    else:
        target_action = "REJECTED"
        
    cursor.execute("""
        UPDATE data_audits
        SET human_action = ?, modified_price = COALESCE(?, modified_price), updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (target_action, mod_price, audit_id))
    
    conn.commit()
    conn.close()
    return get_single_audit_item(audit_id)

def get_quality_stats(script_name: Optional[str] = None, batch_id: Optional[str] = None) -> Dict[str, Any]:
    """统计质检大盘 KPI (支持 script_name 隔离)"""
    conn = get_connection()
    cursor = conn.cursor()
    
    conditions = []
    params = []
    if script_name and script_name != "ALL":
        conditions.append("script_name = ?")
        params.append(script_name)
    if batch_id:
        conditions.append("batch_id = ?")
        params.append(batch_id)
        
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    
    cursor.execute(f"SELECT COUNT(*) as total FROM data_audits {where}", params)
    total = cursor.fetchone()["total"]
    
    and_str = "AND" if where else "WHERE"
    
    cursor.execute(f"SELECT COUNT(*) as pass_count FROM data_audits {where} {and_str} audit_status = 'PASS'", params)
    pass_count = cursor.fetchone()["pass_count"]
    
    cursor.execute(f"SELECT COUNT(*) as need_audit FROM data_audits {where} {and_str} audit_status = 'NEED_AUDIT'", params)
    need_audit = cursor.fetchone()["need_audit"]
    
    cursor.execute(f"SELECT COUNT(*) as reject_count FROM data_audits {where} {and_str} audit_status = 'REJECT'", params)
    reject_count = cursor.fetchone()["reject_count"]
    
    cursor.execute(f"SELECT COUNT(*) as pending_action FROM data_audits {where} {and_str} audit_status IN ('NEED_AUDIT', 'REJECT') AND human_action = 'PENDING'", params)
    pending_action = cursor.fetchone()["pending_action"]
    
    cursor.execute(f"SELECT COUNT(*) as reviewed_action FROM data_audits {where} {and_str} human_action IN ('APPROVED', 'REJECTED', 'MODIFIED')", params)
    reviewed_action = cursor.fetchone()["reviewed_action"]

    cursor.execute(f"SELECT COUNT(*) as ai_diagnosed FROM data_audits {where} {and_str} ai_verdict IS NOT NULL AND ai_verdict != ''", params)
    ai_diagnosed = cursor.fetchone()["ai_diagnosed"]
    
    clean_rate = round((pass_count / total * 100), 1) if total > 0 else 100.0

    # 1. 统计 L1 规则拦截标签分布 (精准识别 L1 规则过滤的内容、标签与频次)
    tag_counts = {}
    cursor.execute(f"SELECT risk_tags FROM data_audits {where}", params)
    for row in cursor.fetchall():
        raw_tags = row["risk_tags"]
        if raw_tags:
            try:
                tags = json.loads(raw_tags) if isinstance(raw_tags, str) else raw_tags
                if isinstance(tags, list):
                    for t in tags:
                        # 归一化提取规则核心名：如 "价格偏低 -99.6%" -> "价格偏低"
                        clean_t = t.split()[0] if ("偏低" in t or "偏高" in t or "溢价" in t) else t
                        clean_t = clean_t.strip()
                        if clean_t:
                            tag_counts[clean_t] = tag_counts.get(clean_t, 0) + 1
            except Exception:
                pass
    l1_rule_breakdown = [
        {"tag": k, "count": v}
        for k, v in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
    ]

    # 2. 真实数值基准统计 (当前任务大盘中位数、区间、单位，彻底消灭硬编码魔数)
    price_where = f"{where} {and_str} price_num IS NOT NULL AND price_num > 0"
    cursor.execute(f"SELECT price_num FROM data_audits {price_where}", params)
    prices = [r["price_num"] for r in cursor.fetchall() if r["price_num"]]
    if prices:
        prices.sort()
        n_p = len(prices)
        med_p = prices[n_p // 2] if n_p % 2 == 1 else round((prices[n_p // 2 - 1] + prices[n_p // 2]) / 2.0, 2)
        value_context = {
            "has_price": True,
            "count": n_p,
            "median": med_p,
            "mean": round(sum(prices) / n_p, 2),
            "min": round(prices[0], 2),
            "max": round(prices[-1], 2),
            "primary_metric_name": "标价",
            "primary_unit": "¥"
        }
    else:
        value_context = {
            "has_price": False,
            "count": 0,
            "median": None,
            "mean": None,
            "min": None,
            "max": None,
            "primary_metric_name": "数值",
            "primary_unit": ""
        }

    # 3. 任务画像元数据与质检模态
    meta_cursor = conn.cursor()
    meta_cursor.execute(f"SELECT task_title, category FROM data_audits {where} ORDER BY id DESC LIMIT 1", params)
    meta_r = meta_cursor.fetchone()
    t_title = meta_r["task_title"] if (meta_r and meta_r["task_title"]) else (script_name or "全部任务")
    t_cat = meta_r["category"] if (meta_r and meta_r["category"]) else ("业务采数" if script_name != "ALL" else "任务汇总")

    s_name_lower = (script_name or "").lower()
    t_title_lower = t_title.lower()

    if "5070" in s_name_lower or "gpu" in s_name_lower or "5070" in t_title_lower or "显卡" in t_title_lower:
        archetype = "SPEC_FRAUD"
    elif "beer" in s_name_lower or "top" in s_name_lower or "榜" in t_title_lower or "啤酒" in t_title_lower:
        archetype = "RANK_HOTNESS"
    elif "bilibili" in s_name_lower or "video" in s_name_lower:
        archetype = "SOCIAL_METRIC"
    elif "mobile" in s_name_lower or "sample" in s_name_lower:
        archetype = "SYSTEM_TELEMETRY"
    else:
        archetype = "GENERAL"

    task_meta = {
        "script_name": script_name or "ALL",
        "task_title": t_title,
        "category": t_cat,
        "archetype": archetype
    }
    
    conn.close()
    return {
        "script_name": script_name or "ALL",
        "total_items": total,
        "pass_count": pass_count,
        "need_audit": need_audit,
        "reject_count": reject_count,
        "pending_action": pending_action,
        "reviewed_action": reviewed_action,
        "ai_diagnosed": ai_diagnosed,
        "clean_rate": clean_rate,
        "l1_rule_breakdown": l1_rule_breakdown,
        "value_context": value_context,
        "task_meta": task_meta
    }

def get_clean_report_items(script_name: Optional[str] = None, batch_id: Optional[str] = None, keyword: Optional[str] = None) -> List[Dict[str, Any]]:
    """获取所有通过质检或人工核验通过的纯净有效数据项（已剔除 REJECTED 垃圾引流项，应用人工纠偏与实测价格）"""
    conn = get_connection()
    cursor = conn.cursor()
    
    where = "WHERE ((audit_status = 'PASS' AND human_action != 'REJECTED') OR human_action = 'APPROVED')"
    params = []
    if script_name and script_name != "ALL":
        where += " AND script_name = ?"
        params.append(script_name)
    if batch_id:
        where += " AND batch_id = ?"
        params.append(batch_id)
    if keyword and keyword != "ALL":
        where += " AND (task_title LIKE ? OR title LIKE ?)"
        params.extend([f"%{keyword}%", f"%{keyword}%"])
        
    cursor.execute(f"SELECT * FROM data_audits {where} ORDER BY item_seq ASC", params)
    rows = cursor.fetchall()
    
    results = []
    for r in rows:
        d = dict(r)
        if d.get("risk_tags"):
            try:
                d["risk_tags"] = json.loads(d["risk_tags"])
            except Exception:
                d["risk_tags"] = []
        if d.get("modified_price") is not None and float(d["modified_price"]) > 0:
            m_price = float(d["modified_price"])
            d["price_num"] = m_price
            d["price_str"] = f"¥{int(m_price)}" if m_price.is_integer() else f"¥{m_price:.2f}"
        elif (float(d.get("price_num") or 0.0) <= 0 or "待获取" in str(d.get("price_str") or "")) and d.get("corrected_price") and float(d["corrected_price"]) > 0:
            c_price = float(d["corrected_price"])
            d["price_num"] = c_price
            d["price_str"] = f"¥{int(c_price)}" if c_price.is_integer() else f"¥{c_price:.2f}"
        results.append(d)
        
    conn.close()
    return results

def seed_multi_task_demo_audits():
    """预置多脚本任务分类演示数据（若只有单一任务时触发，实现多脚本文件柜式分类即刻体验）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(DISTINCT script_name) as cnt FROM data_audits")
    cnt = cursor.fetchone()["cnt"]
    if cnt <= 1:
        date_str = datetime.now().strftime("%Y-%m-%d")
        
        # 1. 社交媒体采集分类任务: B站UP主视频数据监测
        bilibili_items = [
            {
                "script_name": "bilibili_video_task",
                "task_title": "B站UP主视频数据监测",
                "category": "社交监控",
                "batch_id": date_str,
                "item_seq": 1,
                "title": "【4K画质】RTX 5070Ti 首发深度评测与实测帧率跑分",
                "price_str": "播放量 158.2万",
                "price_num": 1582000,
                "deviation_pct": 12.5,
                "risk_tags": json.dumps([], ensure_ascii=False),
                "confidence_score": 100,
                "audit_status": "PASS",
                "human_action": "PENDING",
                "shop": "极客科技评测室",
                "tags": "科技,数码,硬件",
                "link": "https://www.bilibili.com/video/BV1xx411c7mD",
                "raw_shot": "/screenshots/pdd_thumb_default.png",
                "thumb_path": "/screenshots/pdd_thumb_default.png"
            },
            {
                "script_name": "bilibili_video_task",
                "task_title": "B站UP主视频数据监测",
                "category": "社交监控",
                "batch_id": date_str,
                "item_seq": 2,
                "title": "【高能预警】震惊！5070显卡只要999？点击速看避坑",
                "price_str": "播放量 -120",
                "price_num": -120,
                "deviation_pct": -100.0,
                "risk_tags": json.dumps(["播放量负数异常", "营销号标题党特征", "疑似反爬脏数据"], ensure_ascii=False),
                "confidence_score": 25,
                "audit_status": "REJECT",
                "human_action": "PENDING",
                "shop": "营销号速报",
                "tags": "数码,低俗引流",
                "link": "https://www.bilibili.com/video/BV1mock_bad",
                "raw_shot": "/screenshots/pdd_thumb_default.png",
                "thumb_path": "/screenshots/pdd_thumb_default.png"
            },
            {
                "script_name": "bilibili_video_task",
                "task_title": "B站UP主视频数据监测",
                "category": "社交监控",
                "batch_id": date_str,
                "item_seq": 3,
                "title": "微星魔龙 RTX 5070Ti 16G 极限超频实测与功耗温度表现",
                "price_str": "播放量 42.6万",
                "price_num": 426000,
                "deviation_pct": 5.0,
                "risk_tags": json.dumps([], ensure_ascii=False),
                "confidence_score": 100,
                "audit_status": "PASS",
                "human_action": "APPROVED",
                "shop": "微星硬件研究所",
                "tags": "硬件超频",
                "link": "https://www.bilibili.com/video/BV1msi_test",
                "raw_shot": "/screenshots/pdd_thumb_default.png",
                "thumb_path": "/screenshots/pdd_thumb_default.png"
            }
        ]
        
        # 2. 移动端运维自愈巡检任务: 移动端弹窗与自愈巡检
        mobile_items = [
            {
                "script_name": "sample_mobile_task",
                "task_title": "移动端权限与自愈巡检",
                "category": "系统运维",
                "batch_id": date_str,
                "item_seq": 1,
                "title": "Redmi Note 13 Pro - 每日自动化健康打卡流",
                "price_str": "执行耗时 12.4s",
                "price_num": 12.4,
                "deviation_pct": 0.0,
                "risk_tags": json.dumps([], ensure_ascii=False),
                "confidence_score": 100,
                "audit_status": "PASS",
                "human_action": "PENDING",
                "shop": "本地应用",
                "tags": "企业微信,打卡",
                "link": "",
                "raw_shot": "/screenshots/pdd_thumb_default.png",
                "thumb_path": "/screenshots/pdd_thumb_default.png"
            },
            {
                "script_name": "sample_mobile_task",
                "task_title": "移动端权限与自愈巡检",
                "category": "系统运维",
                "batch_id": date_str,
                "item_seq": 2,
                "title": "Redmi Note 13 Pro - 支付弹窗拦截与自愈关闭验证",
                "price_str": "执行耗时 48.9s",
                "price_num": 48.9,
                "deviation_pct": 185.0,
                "risk_tags": json.dumps(["弹窗遮罩超时自愈", "执行耗时偏高 +185%"], ensure_ascii=False),
                "confidence_score": 40,
                "audit_status": "NEED_AUDIT",
                "human_action": "PENDING",
                "shop": "系统底层",
                "tags": "弹窗拦截,异常重试",
                "link": "",
                "raw_shot": "/screenshots/pdd_thumb_default.png",
                "thumb_path": "/screenshots/pdd_thumb_default.png"
            }
        ]
        
        for it in (bilibili_items + mobile_items):
            cursor.execute("""
            INSERT INTO data_audits (
                script_name, task_title, category,
                batch_id, item_seq, title, price_str, price_num, deviation_pct,
                risk_tags, confidence_score, audit_status, human_action,
                shop, tags, link, raw_shot, thumb_path
            ) VALUES (
                :script_name, :task_title, :category,
                :batch_id, :item_seq, :title, :price_str, :price_num, :deviation_pct,
                :risk_tags, :confidence_score, :audit_status, :human_action,
                :shop, :tags, :link, :raw_shot, :thumb_path
            )
            """, it)
        conn.commit()
    conn.close()

# 初始化数据库并适度填充示例多分类
init_db()
seed_multi_task_demo_audits()

