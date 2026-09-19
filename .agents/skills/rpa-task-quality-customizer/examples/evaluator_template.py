# -*- coding: utf-8 -*-
"""
RPA 任务定制 L1 规则评估器标准模板 (evaluator_template.py)
======================================================
遵循准则：
1. 0 Token 成本、本地毫秒级纯计算。
2. 纯数据驱动与自适应箱线图，绝不硬编码跨品类魔数（如价格 > 1000 或 7500）。
3. 严格输出标准实体：PASS / NEED_AUDIT / REJECT，以及结构化 risk_tags。
"""

from typing import List, Dict, Any, Tuple, Optional

def evaluate_single_item(item: Dict[str, Any], context_stats: Dict[str, Any], seq: int = 1) -> Dict[str, Any]:
    """
    对单条数据进行规则裁决
    :param item: 采数脚本捕获的单条原始字典
    :param context_stats: 当前批次或行业统计指标（如中位数、分位数、总件数等）
    :param seq: 条目序号
    :return: 包含标准质检字段的字典
    """
    risk_tags = []
    confidence_score = 100
    audit_status = "PASS"  # PASS | NEED_AUDIT | REJECT

    # 1. 核心真理字段非空与格式校验 (P0)
    # 例如：榜单类检查 rank 是否存在；电商类检查价格是否有效；社交类检查播放量是否提取成功
    title = str(item.get("title", "")).strip()
    if not title:
        risk_tags.append("标题缺失")
        confidence_score -= 50
        audit_status = "REJECT"

    # 2. 业务准入守卫 (白名单与黑名单一票否决)
    # 示例：啤酒品类守卫，非啤酒直接一票否决
    category_guards = context_stats.get("category_guards", [])
    if category_guards:
        has_guard = any(g.lower() in title.lower() for g in category_guards)
        if not has_guard:
            risk_tags.append("跨品类杂质")
            confidence_score -= 60
            audit_status = "REJECT"

    # 3. 统计学或连续性离群校验
    # 示例：价格 IQR 偏离度，或榜单跳号
    # ... 自定义业务轻量逻辑 ...

    return {
        "seq": seq,
        "title": title,
        "primary_metric": item.get("primary_metric", ""), # 如排名/售价/播放量
        "risk_tags": list(set(risk_tags)),
        "confidence_score": max(0, min(100, confidence_score)),
        "audit_status": audit_status,
        "human_action": item.get("human_action", "PENDING")
    }

def evaluate_batch(items: List[Dict[str, Any]], custom_profile: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    对整批采集数据进行宏观统计与微观逐条裁决
    """
    # 1. 宏观统计计算（如大盘中位数、连号率、有效率等）
    total = len(items)
    stats = {
        "total_count": total,
        "category_guards": custom_profile.get("category_guards", []) if custom_profile else []
    }

    # 2. 逐条评估
    audited = []
    for idx, it in enumerate(items, start=1):
        res = evaluate_single_item(it, stats, seq=idx)
        audited.append(res)

    # 3. 总结汇总
    summary = {
        "total_items": total,
        "clean_count": sum(1 for r in audited if r["audit_status"] == "PASS"),
        "need_audit_count": sum(1 for r in audited if r["audit_status"] == "NEED_AUDIT"),
        "rejected_count": sum(1 for r in audited if r["audit_status"] == "REJECT"),
        "clean_rate": round(sum(1 for r in audited if r["audit_status"] == "PASS") / max(1, total) * 100, 1),
        "stats": stats
    }
    return audited, summary
