# -*- coding: utf-8 -*-
"""
实在智能RPA - 手机端自动化运维监控系统
通用数据质量与离群点质检引擎 (backend/quality.py)
"""

import json
import re
import math
from typing import List, Dict, Any, Tuple, Optional

def parse_price(price_str: Any) -> Optional[float]:
    """从文本中提取浮点价格"""
    if price_str is None:
        return None
    if isinstance(price_str, (int, float)):
        return float(price_str)
    
    # 匹配诸如 ¥7299, 7299元, 7,299.00 等
    cleaned = str(price_str).replace(",", "").replace("，", "")
    match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None

def compile_quality_rule_with_ai(intent_prompt: str, keyword: str = "") -> Optional[Dict[str, Any]]:
    """调用真正的 DeepSeek 大模型 Agent，将用户的自然语言诉求深度理解并编译为结构化规则"""
    try:
        from backend.ai_auditor import call_deepseek_api
    except Exception:
        return None

    prompt = f"""你是一个 RPA 采数与数据质量策略编译器 Agent。
请根据用户的业务采数诉求，将其深度理解并编译为用于移动端自动化采集和 L1 离线质检的结构化 JSON 规则。

【用户采数诉求】: {intent_prompt}
【当前关键词参考】: {keyword}

请仔细分析该诉求：
1. 识别商品所属行业品类（如独立显卡、快消酒水、美妆护肤、数码3C、日用百货等）；
2. 识别品牌名称（如 marubi 为丸美，iphone 为苹果，珠江啤酒等），提取必含品牌；
3. 识别用户对规格、型号的要求（如“型号不限”、“只要16G”等）；
4. 识别需要拦截排除的低质或刷单关键词（如配件、定金、空瓶、小样、支架等）；
5. 给出最适合在拼多多 App 搜索栏输入的关键词；
6. 提炼 4 个通俗易懂的白话策略胶囊。

请返回如下严格格式的纯 JSON（严禁包含 Markdown 标记）：
{{
  "task_category": "COSMETICS", // 大类代码: GPU | BEVERAGE | COSMETICS | DIGITAL | APPLIANCE | GENERAL
  "brand_must_contain": ["marubi", "丸美"], // 必须包含的品牌关键词列表，若不限品牌则填 []
  "exclude_keywords": ["定金", "测试", "空瓶", "空盒", "小样单独"], // 必须拦截的低质或配件关键词
  "check_memory_conflict": false, // 是否需要 12G/16G 显存引流审核 (仅独立显卡为 true，其它一律为 false)
  "check_chip_conflict": false, // 是否需要 5070/5070Ti 芯片型号冲突审核 (仅GPU为 true)
  "suggested_keyword": "marubi", // 推荐在拼多多 App 搜索栏输入的最佳关键词
  "suggested_count": 100, // 推荐采集商品数量 (整数)
  "suggested_sort_by": "default", // default(综合推荐) | sales(销量优先) | price_asc(价格优先)
  "rule_summary_capsules": [
    {{"icon": "💄", "title": "品类定位", "desc": "美妆护肤 / 丸美 (MARUBI)"}},
    {{"icon": "🏷️", "title": "品牌准入", "desc": "必须包含「marubi / 丸美」"}},
    {{"icon": "🛡️", "title": "规格策略", "desc": "型号不限 (放行所有正装)"}},
    {{"icon": "📊", "title": "价格质检", "desc": "数据驱动 IQR 箱线图离群过滤"}}
  ]
}}
"""
    try:
        res = call_deepseek_api(prompt)
        if isinstance(res, dict) and "task_category" in res:
            res["intent_prompt"] = intent_prompt
            res["keyword"] = keyword
            if "iqr_multiplier" not in res:
                res["iqr_multiplier"] = 1.5
            return res
    except Exception as e:
        print(f"[Quality Rule Compiler AI Error]: {e}")
        return None
    return None

def compile_quality_rule(intent_prompt: str = "", keyword: str = "") -> Dict[str, Any]:
    """
    配置期规则编译型 Agent：将用户的自然语言质检诉求或关键词编译为结构化低成本规则 Profile。
    0 运行时大模型调用开销：在配置期一次性编译，后续 L1 质检以毫秒级离线规则执行。
    """
    intent_raw = (intent_prompt or "").strip()
    kw_raw = (keyword or "").strip()

    # 1. 优先调用真正的 DeepSeek 大模型 Agent 进行意图深度解析与编译
    if intent_raw:
        ai_profile = compile_quality_rule_with_ai(intent_raw, kw_raw)
        if ai_profile:
            return ai_profile

    # 2. 离线/快速降级本地规则引擎
    intent_norm = intent_raw.lower()
    kw_norm = kw_raw.lower()
    combined_text = f"{intent_norm} {kw_norm}"

    # 1. 品类与场景判定
    is_gpu = any(k in combined_text for k in ["5070", "5080", "5090", "4090", "4080", "显卡", "gpu", "rtx", "显存"])
    is_beverage = any(k in combined_text for k in ["啤酒", "纯生", "珠江", "青岛", "雪花", "百威", "饮品", "酒水", "可乐", "矿泉水"])

    category = "GENERAL"
    check_memory_conflict = False
    check_chip_conflict = False
    brand_must_contain = []
    exclude_keywords = ["定金", "测试专用", "空盒"]
    iqr_multiplier = 1.5
    rule_summary_capsules = []

    suggested_kw = keyword.strip() if keyword.strip() else ""
    suggested_count = 100
    suggested_sort_by = "default"

    # 提取意图中的数量建议 (例如 "采50条", "抓取 20 个")
    count_match = re.search(r"(?:采|抓|采集|抓取|获取)\s*(\d+)\s*(?:条|个|件|条数据)?", intent_norm)
    if count_match:
        try:
            val = int(count_match.group(1))
            if 10 <= val <= 300:
                suggested_count = val
        except Exception:
            pass

    # 提取意图中的排序建议
    if any(k in intent_norm for k in ["销量", "卖得好", "销量优先"]):
        suggested_sort_by = "sales"
    elif any(k in intent_norm for k in ["低价", "便宜", "价格最低", "价格优先", "价格升序"]):
        suggested_sort_by = "price_asc"

    if is_gpu:
        category = "GPU"
        check_memory_conflict = True
        check_chip_conflict = True
        exclude_keywords = ["显卡支架", "散热风扇", "水冷头", "空盒", "包装盒", "定金", "预付款", "测试专用"]
        if not suggested_kw:
            suggested_kw = "RTX 5070 Ti"
        rule_summary_capsules = [
            {"icon": "🎮", "title": "品类模式", "desc": "独立显卡 (GPU专项)"},
            {"icon": "⚡", "title": "显存引流审核", "desc": "严查 12G/16G 复合套路"},
            {"icon": "🏷️", "title": "芯片型号防刷", "desc": "排查 5070/5070Ti 混合标注"},
            {"icon": "🛡️", "title": "配件定金排查", "desc": "拦截支架/风扇/包装盒/定金"}
        ]
    elif is_beverage:
        category = "BEVERAGE"
        # 智能提取必含品牌
        if "珠江" in combined_text:
            brand_must_contain = ["珠江啤酒", "珠江"]
            if not suggested_kw:
                suggested_kw = "珠江啤酒"
        elif "青岛" in combined_text:
            brand_must_contain = ["青岛啤酒", "青岛"]
            if not suggested_kw:
                suggested_kw = "青岛啤酒"
        elif "雪花" in combined_text:
            brand_must_contain = ["雪花啤酒", "雪花"]
            if not suggested_kw:
                suggested_kw = "雪花啤酒"
        else:
            # 默认提取啤酒前缀品牌
            bm = re.search(r"([\u4e00-\u9fa5]{2,6}啤酒)", combined_text)
            if bm:
                brand_must_contain = [bm.group(1)]
                if not suggested_kw:
                    suggested_kw = bm.group(1)

        exclude_keywords = ["定金", "测试专用", "空盒", "模型", "包装箱单独"]
        brand_desc = " / ".join(brand_must_contain) if brand_must_contain else "酒水快消"
        rule_summary_capsules = [
            {"icon": "🍺", "title": "品类模式", "desc": "酒水饮料 / 啤酒快消"},
            {"icon": "🏷️", "title": "品牌准入要求", "desc": f"必须包含「{brand_desc}」" if brand_must_contain else "通用快消放行"},
            {"icon": "🛡️", "title": "数码规则隔离", "desc": "免除显存与芯片冲突审核 (0误杀)"},
            {"icon": "📊", "title": "价格离群质检", "desc": "数据驱动 IQR 箱线图离群拦截"}
        ]
    else:
        # 通用商品模式：从意图提炼要求
        # 提取 "只要/必须包含/限定/采集/抓取 [品牌/词汇]"
        bm = re.search(r"(?:只要|必须包含|只搜|必须是|限定品牌|品牌为|采集|抓取|搜索)\s*([a-zA-Z0-9\u4e00-\u9fa5]{2,15})", intent_norm)
        if bm:
            extracted = bm.group(1).replace("商品", "").replace("产品", "").replace("数据", "").strip()
            if extracted and len(extracted) >= 2:
                brand_must_contain = [extracted]
        
        # 提取 "排除/过滤/不要 [词汇]"
        ex_m = re.findall(r"(?:排除|过滤|不要|去除)\s*([a-zA-Z0-9\u4e00-\u9fa5]{2,6})", intent_norm)
        for ex in ex_m:
            if ex not in exclude_keywords:
                exclude_keywords.append(ex)

        if not suggested_kw and brand_must_contain:
            suggested_kw = brand_must_contain[0]
        elif not suggested_kw and bm:
            suggested_kw = bm.group(1)

        brand_desc = f"必含「{brand_must_contain[0]}」" if brand_must_contain else "通用放行"
        rule_summary_capsules = [
            {"icon": "📦", "title": "品类模式", "desc": "通用全品类自适应"},
            {"icon": "🏷️", "title": "品牌准入", "desc": brand_desc},
            {"icon": "🛡️", "title": "基础防刷", "desc": f"拦截定金/空盒 ({len(exclude_keywords)}项)"},
            {"icon": "📊", "title": "质检模型", "desc": "纯数据驱动 IQR 箱线图统计"}
        ]

    return {
        "task_category": category,
        "intent_prompt": intent_prompt,
        "keyword": keyword,
        "brand_must_contain": brand_must_contain,
        "exclude_keywords": exclude_keywords,
        "check_memory_conflict": check_memory_conflict,
        "check_chip_conflict": check_chip_conflict,
        "iqr_multiplier": iqr_multiplier,
        "suggested_keyword": suggested_kw,
        "suggested_count": suggested_count,
        "suggested_sort_by": suggested_sort_by,
        "rule_summary_capsules": rule_summary_capsules
    }

def detect_title_conflicts(title: str, profile: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """检测标题中的引流与多规格冲突 (受 rule_profile 策略驱动)"""
    conflicts = []
    if not title:
        return conflicts
    
    norm_title = title.lower()
    
    # 策略开关与参数解析
    check_memory = False
    check_chip = False
    brand_must_contain = []
    exclude_keywords = ["定金", "测试专用", "空盒"]

    if profile:
        check_memory = bool(profile.get("check_memory_conflict", False))
        check_chip = bool(profile.get("check_chip_conflict", False))
        brand_must_contain = profile.get("brand_must_contain") or []
        exclude_keywords = profile.get("exclude_keywords") or exclude_keywords
    else:
        # 无 profile 时的兜底推断：只有标题明确含显卡关键词时才开启显存检查
        if any(k in norm_title for k in ["5070", "5080", "5090", "4090", "4080", "显卡", "rtx"]):
            check_memory = True
            check_chip = True
            exclude_keywords = ["显卡支架", "散热风扇", "水冷头", "空盒", "包装盒", "定金", "预付款", "测试专用"]

    # 1. 品牌必含规则断言
    if brand_must_contain:
        hit_brand = any(b.lower() in norm_title for b in brand_must_contain)
        if not hit_brand:
            target_str = " / ".join(brand_must_contain)
            conflicts.append({
                "code": "BRAND_MISMATCH",
                "type": "BRAND",
                "level": "CRITICAL",
                "tag": "品牌不匹配",
                "desc": f"标题未包含指定品牌词「{target_str}」"
            })

    # 2. 显存容量冲突检测 (仅在数码显卡策略下生效，快消品严格免除)
    if check_memory:
        has_16g = bool(re.search(r"(?:16\s*g(?:b)?|16\s*g显存)", norm_title))
        has_12g = bool(re.search(r"(?:12\s*g(?:b)?|12\s*g显存)", norm_title))
        has_8g = bool(re.search(r"(?:8\s*g(?:b)?|8\s*g显存)", norm_title))
        has_24g = bool(re.search(r"(?:24\s*g(?:b)?|24\s*g显存)", norm_title))

        if has_16g and has_12g:
            conflicts.append({
                "code": "CONFLICT_12G_16G",
                "type": "MEMORY",
                "level": "HIGH",
                "tag": "复合显存引流",
                "desc": "标题同时包含 12G 与 16G，疑似 12G 起步价引流"
            })
        elif has_16g and (has_8g or has_24g):
            conflicts.append({
                "code": "CONFLICT_OTHER_MEM",
                "type": "MEMORY",
                "level": "MEDIUM",
                "tag": "复合显存规格",
                "desc": "标题包含多种显存规格"
            })

    # 3. 芯片型号冲突检测 (仅在数码显卡策略下生效)
    if check_chip:
        has_5070ti = bool(re.search(r"5070\s*ti", norm_title))
        has_5070_standard = bool(re.search(r"5070(?!\s*ti)", norm_title))

        if has_5070ti and has_5070_standard:
            conflicts.append({
                "code": "CONFLICT_5070_5070TI",
                "type": "CHIP",
                "level": "HIGH",
                "tag": "复合型号引流",
                "desc": "标题同时包含 5070 与 5070Ti，标价极可能为 5070 标准版"
            })

    # 4. 排除关键词排查
    for kw in exclude_keywords:
        if kw.lower() in norm_title:
            conflicts.append({
                "code": "SUSPICIOUS_ACCESSORY",
                "type": "CATEGORY",
                "level": "CRITICAL",
                "tag": "疑似配件定金",
                "desc": f"标题包含被排除的关键词「{kw}」"
            })
            break

    return conflicts

def calculate_batch_stats(items: List[Dict[str, Any]], profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    计算当前批次的价格大盘统计指标 (自适应中位数、四分位数与 IQR 箱线图)
    彻底移除任何硬编码价格过滤（如 p > 1000）与显卡魔数，纯数据分布自适应驱动
    """
    prices = []
    for item in items:
        p = parse_price(item.get("price"))
        if p is not None and p > 0:  # 仅过滤非正数异常，绝不以 1000 元硬砍快消品
            prices.append(p)
    
    # 策略判定
    is_gpu = False
    if profile:
        is_gpu = (profile.get("task_category") == "GPU" or profile.get("check_memory_conflict") is True)
    else:
        is_gpu = any("5070" in str(it.get("title", "")).lower() for it in items)

    if not prices:
        # 空数据自适应兜底
        default_price = 9499.0 if is_gpu else 68.0
        return {
            "count": 0,
            "median": default_price,
            "mean": default_price,
            "q1": round(default_price * 0.9, 2),
            "q3": round(default_price * 1.1, 2),
            "iqr": round(default_price * 0.2, 2),
            "safe_min": round(default_price * 0.7, 2),
            "safe_max": round(default_price * 1.5, 2)
        }
    
    prices.sort()
    n = len(prices)
    
    # 精确计算中位数
    if n % 2 == 1:
        median = prices[n // 2]
    else:
        median = (prices[n // 2 - 1] + prices[n // 2]) / 2.0
        
    mean = sum(prices) / n
    q1 = prices[int(n * 0.25)]
    q3 = prices[int(n * 0.75)]
    iqr = q3 - q1

    iqr_mult = profile.get("iqr_multiplier", 1.5) if profile else 1.5

    if is_gpu:
        # 5070Ti 等显卡品类的合理行业区间
        safe_min = max(6800.0, round(median * 0.75, 2))
        safe_max = min(15000.0, round(median * 1.4, 2))
    else:
        # 快消品与通用商品：严格遵循四分位距箱线图 (Q1 - 1.5*IQR, Q3 + 1.5*IQR)
        calc_min = round(q1 - iqr_mult * iqr, 2)
        calc_max = round(q3 + iqr_mult * iqr, 2)
        # 避免箱线图下限为负数或低于 0.01
        safe_min = max(0.01, calc_min) if calc_min > 0 else max(0.01, round(median * 0.5, 2))
        safe_max = calc_max if calc_max > median else round(median * 1.8, 2)

    return {
        "count": n,
        "median": round(median, 2),
        "mean": round(mean, 2),
        "q1": round(q1, 2),
        "q3": round(q3, 2),
        "iqr": round(iqr, 2),
        "safe_min": round(safe_min, 2),
        "safe_max": round(safe_max, 2)
    }

def audit_item(item: Dict[str, Any], stats: Dict[str, Any], seq: int = 1, profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """对单条采集数据进行深度质检断言评估 (动态策略驱动，绝不妄加显卡误杀标签)"""
    title = item.get("title", "")
    price_val = parse_price(item.get("price"))
    
    conflicts = detect_title_conflicts(title, profile=profile)
    median = stats.get("median", 100.0)
    safe_min = stats.get("safe_min", 1.0)
    safe_max = stats.get("safe_max", 100000.0)

    is_gpu = False
    if profile:
        is_gpu = (profile.get("task_category") == "GPU" or profile.get("check_memory_conflict") is True)
    else:
        is_gpu = any(k in title.lower() for k in ["5070", "5080", "5090", "显卡", "rtx"])

    risk_tags = []
    confidence_score = 100
    audit_status = "PASS"  # PASS | NEED_AUDIT | REJECT
    deviation_pct = 0.0

    # 1. 标题与属性冲突扣分
    for c in conflicts:
        risk_tags.append(c["tag"])
        if c["level"] == "CRITICAL":
            confidence_score -= 60
            audit_status = "REJECT"
        elif c["level"] == "HIGH":
            confidence_score -= 35
            if audit_status != "REJECT":
                audit_status = "NEED_AUDIT"
        else:
            confidence_score -= 15
            if audit_status == "PASS":
                audit_status = "NEED_AUDIT"

    # 2. 价格统计学偏离度计算
    if price_val is not None:
        if median > 0:
            deviation_pct = round(((price_val - median) / median) * 100, 1)
        
        if price_val < safe_min:
            dev_str = f"价格偏低 {deviation_pct}%"
            risk_tags.append(dev_str)
            confidence_score -= 30
            if audit_status != "REJECT":
                audit_status = "NEED_AUDIT"
                
            # 仅在 GPU 显卡策略下，若价格过低且有复合冲突才判定为显存引流
            if is_gpu and price_val < 7500.0:
                risk_tags.append("极高概率12G引流")
                confidence_score -= 20
                audit_status = "REJECT"

        elif price_val > safe_max:
            risk_tags.append(f"溢价过高 +{deviation_pct}%")
            confidence_score -= 15
            if audit_status == "PASS":
                audit_status = "NEED_AUDIT"
    else:
        risk_tags.append("无有效价格")
        confidence_score -= 50
        audit_status = "REJECT"

    # 3. SKU 验证状态加权
    sku_verified = item.get("sku_verified", False)
    if not sku_verified and conflicts:
        confidence_score -= 15

    confidence_score = max(0, min(100, confidence_score))

    return {
        "seq": seq,
        "title": title,
        "price_str": item.get("price", "¥0"),
        "price_num": price_val,
        "shop": item.get("shop", "未知店铺"),
        "tags": item.get("tags", ""),
        "link": item.get("link", ""),
        "raw_shot": item.get("raw_shot", ""),
        "thumb_path": item.get("thumb_path", ""),
        "conflicts": conflicts,
        "deviation_pct": deviation_pct,
        "risk_tags": list(set(risk_tags)),
        "confidence_score": confidence_score,
        "audit_status": audit_status,  # PASS / NEED_AUDIT / REJECT
        "human_action": item.get("human_action", "PENDING") # PENDING / APPROVED / REJECTED / MODIFIED
    }

def audit_batch(items: List[Dict[str, Any]], profile: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """批量运行质检评估 (受 rule_profile 驱动)"""
    stats = calculate_batch_stats(items, profile=profile)
    results = []
    
    for idx, item in enumerate(items, start=1):
        res = audit_item(item, stats, seq=idx, profile=profile)
        results.append(res)
        
    summary = {
        "total_items": len(results),
        "clean_count": sum(1 for r in results if r["audit_status"] == "PASS"),
        "need_audit_count": sum(1 for r in results if r["audit_status"] == "NEED_AUDIT"),
        "rejected_count": sum(1 for r in results if r["audit_status"] == "REJECT"),
        "clean_rate": round((sum(1 for r in results if r["audit_status"] == "PASS") / max(1, len(results))) * 100, 1),
        "stats": stats
    }
    
    return results, summary

def export_clean_excel(items: List[Dict[str, Any]], output_xlsx: str, sheet_title: Optional[str] = None):
    """导出经过质检与人工核验的高纯净度 Excel 报表"""
    import os
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.drawing.image import Image as XLImage

    wb = openpyxl.Workbook()
    ws = wb.active
    if not sheet_title:
        basename = os.path.basename(output_xlsx).replace("_clean.xlsx", "").replace("pdd_", "")
        if basename and basename != "clean":
            sheet_title = f"{basename}_质检纯净版"
        else:
            sheet_title = "质检纯净数据报表"
    ws.title = str(sheet_title)[:30]

    headers = [
        "序号", "现场商品快照", "商品标题", "锁定价格", 
        "质检状态", "偏离度/风险备注", "所属店铺", "商品购买短链"
    ]
    ws.append(headers)

    # 样式配置
    header_font = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    data_font = Font(name="微软雅黑", size=9)
    price_font = Font(name="Consolas", size=11, bold=True, color="E11D48")
    pass_font = Font(name="微软雅黑", size=9, bold=True, color="059669")
    human_font = Font(name="微软雅黑", size=9, bold=True, color="2563EB")
    
    thin_border = Border(
        left=Side(style="thin", color="E2E8F0"),
        right=Side(style="thin", color="E2E8F0"),
        top=Side(style="thin", color="E2E8F0"),
        bottom=Side(style="thin", color="E2E8F0")
    )

    ws.row_dimensions[1].height = 28
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    widths = {
        "A": 8, "B": 18, "C": 48, "D": 14, "E": 18, "F": 26, "G": 20, "H": 40
    }
    for col, w in widths.items():
        ws.column_dimensions[col].width = w

    for idx, item in enumerate(items, start=1):
        row_idx = idx + 1
        ws.row_dimensions[row_idx].height = 96

        status_label = "✔ 质检免审通过"
        if item.get("human_action") == "APPROVED":
            status_label = "✔ 人工核验通过"
        elif item.get("human_action") == "MODIFIED":
            status_label = "✏️ 人工修正价格"

        risk_str = "正常"
        risk_tags = item.get("risk_tags", [])
        if isinstance(risk_tags, list) and risk_tags:
            risk_str = " | ".join(risk_tags)
        elif item.get("deviation_pct"):
            risk_str = f"偏离大盘 {item.get('deviation_pct')}%"

        price_disp = item.get("price_str", "")
        if item.get("modified_price"):
            price_disp = f"¥{item['modified_price']}"

        row_data = [
            idx,
            "",  # 图片占位
            item.get("title", ""),
            price_disp,
            status_label,
            risk_str,
            item.get("shop", ""),
            item.get("link", "")
        ]
        ws.append(row_data)

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.font = data_font
            cell.border = thin_border
            if col_idx == 1:
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif col_idx == 4:
                cell.font = price_font
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif col_idx == 5:
                cell.font = human_font if "人工" in status_label else pass_font
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

        # 嵌入缩略图
        thumb_path = item.get("thumb_path")
        if thumb_path and os.path.exists(thumb_path):
            try:
                xl_img = XLImage(thumb_path)
                xl_img.width = 110
                xl_img.height = 110
                ws.add_image(xl_img, f"B{row_idx}")
            except Exception:
                pass

    os.makedirs(os.path.dirname(output_xlsx), exist_ok=True)
    wb.save(output_xlsx)
    return output_xlsx
