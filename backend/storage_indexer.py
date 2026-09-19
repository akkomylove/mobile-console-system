import os
import time
import json
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE_ROOT = os.path.join(PROJECT_ROOT, "storage")

def get_storage_stats():
    """扫描 storage 目录，生成结构化文件索引与容量统计"""
    stats = {
        "storage_root": STORAGE_ROOT,
        "total_size_bytes": 0,
        "total_size_mb": 0.0,
        "reports": [],
        "captures_summary": {},
        "logs_summary": {},
        "database_size_kb": 0.0
    }
    
    if not os.path.exists(STORAGE_ROOT):
        return stats

    total_bytes = 0

    # 1. 扫描 reports 目录
    reports_dir = os.path.join(STORAGE_ROOT, "reports")
    if os.path.exists(reports_dir):
        for root, _, files in os.walk(reports_dir):
            for file in files:
                if file.startswith("~$") or file.endswith(".tmp"):
                    continue
                full_path = os.path.join(root, file)
                size = os.path.getsize(full_path)

                total_bytes += size
                mtime = os.path.getmtime(full_path)
                rel_path = os.path.relpath(full_path, STORAGE_ROOT).replace("\\", "/")
                
                stats["reports"].append({
                    "name": file,
                    "rel_path": rel_path,
                    "url": f"/storage/{rel_path}",
                    "size_bytes": size,
                    "size_kb": round(size / 1024, 1),
                    "modified_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)),
                    "type": "excel" if file.endswith(".xlsx") else ("json" if file.endswith(".json") else "other")
                })
        # 按修改时间倒序
        stats["reports"].sort(key=lambda x: x["modified_time"], reverse=True)

    # 2. 扫描 captures 目录
    captures_dir = os.path.join(STORAGE_ROOT, "captures")
    if os.path.exists(captures_dir):
        for date_dir in os.listdir(captures_dir):
            day_path = os.path.join(captures_dir, date_dir)
            if os.path.isdir(day_path):
                files = os.listdir(day_path)
                day_size = sum(os.path.getsize(os.path.join(day_path, f)) for f in files)
                total_bytes += day_size
                stats["captures_summary"][date_dir] = {
                    "count": len(files),
                    "size_mb": round(day_size / (1024 * 1024), 2),
                    "path": f"storage/captures/{date_dir}"
                }

    # 3. 扫描 logs 目录
    logs_dir = os.path.join(STORAGE_ROOT, "logs")
    if os.path.exists(logs_dir):
        for date_dir in os.listdir(logs_dir):
            day_path = os.path.join(logs_dir, date_dir)
            if os.path.isdir(day_path):
                files = os.listdir(day_path)
                day_size = sum(os.path.getsize(os.path.join(day_path, f)) for f in files)
                total_bytes += day_size
                stats["logs_summary"][date_dir] = {
                    "count": len(files),
                    "size_kb": round(day_size / 1024, 1),
                    "path": f"storage/logs/{date_dir}"
                }

    # 4. 扫描 database 目录
    db_file = os.path.join(STORAGE_ROOT, "database", "monitor.db")
    if os.path.exists(db_file):
        db_size = os.path.getsize(db_file)
        total_bytes += db_size
        stats["database_size_kb"] = round(db_size / 1024, 1)

    stats["total_size_bytes"] = total_bytes
    stats["total_size_mb"] = round(total_bytes / (1024 * 1024), 2)
    return stats

def generate_index_markdown():
    """生成易读美观的 storage/INDEX.md 目录索引清单"""
    stats = get_storage_stats()
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    
    md = [
        "# 📁 手机端流程控制台 —— 存储数据与报表索引清单 (Storage Index)",
        f"> **最后更新时间**: `{now_str}` | **存储总容量**: `{stats['total_size_mb']} MB`",
        "",
        "本文档由运维系统自动生成与维护，规范化索引当前项目生成的所有报表、原画快照、执行日志与核心数据库文件。",
        "",
        "---",
        "",
        "## 📊 一、结构化业务报表 (Reports)",
        "",
        "| 报表名称 | 文件格式 | 大小 | 归档相对路径 | 生成时间 | 下载与预览 |",
        "| :--- | :---: | :---: | :--- | :---: | :---: |"
    ]
    
    for r in stats["reports"]:
        fmt = "📊 Excel" if r["type"] == "excel" else ("📄 JSON" if r["type"] == "json" else "📁 其它")
        md.append(f"| **{r['name']}** | {fmt} | `{r['size_kb']} KB` | `{r['rel_path']}` | {r['modified_time']} | [点击查看/下载](file:///{os.path.join(STORAGE_ROOT, r['rel_path']).replace(chr(92), '/')}) |")
        
    md.extend([
        "",
        "---",
        "",
        "## 📸 二、现场快照截屏库 (Captures)",
        "",
        "按采集日期 `YYYY-MM-DD` 严格归档，每个采集项均包含 `raw`（原画详情页快照）与 `thumb`（等比缩放的 Excel 嵌入缩略图）：",
        "",
        "| 日期分区 | 快照总数 | 存储占用 | 目录位置 |",
        "| :--- | :---: | :---: | :--- |"
    ])
    
    for date_key, cap in stats["captures_summary"].items():
        md.append(f"| 📅 **{date_key}** | `{cap['count']} 张图片` | `{cap['size_mb']} MB` | [`{cap['path']}`](file:///{os.path.join(PROJECT_ROOT, cap['path']).replace(chr(92), '/')}) |")
        
    md.extend([
        "",
        "---",
        "",
        "## 📝 三、运维执行日志库 (Logs)",
        "",
        "记录每一次脚本触发的终端完整输出流与异常诊断痕迹：",
        "",
        "| 日期分区 | 日志文件数 | 占用大小 | 目录位置 |",
        "| :--- | :---: | :---: | :--- |"
    ])
    
    for date_key, log_item in stats["logs_summary"].items():
        md.append(f"| 📅 **{date_key}** | `{log_item['count']} 份日志` | `{log_item['size_kb']} KB` | [`{log_item['path']}`](file:///{os.path.join(PROJECT_ROOT, log_item['path']).replace(chr(92), '/')}) |")
        
    md.extend([
        "",
        "---",
        "",
        "## 🗄️ 四、核心监控数据库 (Database)",
        f"- **数据库文件**: [`storage/database/monitor.db`](file:///{os.path.join(STORAGE_ROOT, 'database', 'monitor.db').replace(chr(92), '/')})",
        f"- **当前体积**: `{stats['database_size_kb']} KB`",
        "- **数据表说明**:",
        "  - `runs`: 全量任务执行历史、状态、耗时、异常定性及 AI 根因报告",
        "  - `configs`: 钉钉/飞书/企微 Webhook 告警配置及大模型引擎参数",
        "",
        "---",
        "> 💡 **查找建议**：在 Web 监控看板首页可直接下载最新报表；如需历史版本，直接进入对应的 `storage/reports/YYYY-MM-DD/` 目录即可。"
    ])
    
    index_file = os.path.join(STORAGE_ROOT, "INDEX.md")
    with open(index_file, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    return index_file

if __name__ == "__main__":
    generated = generate_index_markdown()
    print("Storage index generated successfully at:", generated)
