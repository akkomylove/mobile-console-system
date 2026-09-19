# -*- coding: utf-8 -*-
"""
实在智能RPA - 手机端自动化运维监控系统
DeepSeek AI 智能盲审诊断探针引擎 (backend/ai_auditor.py)
面向 L1 规则拦截的存疑数据，提供毫秒级智能会诊、根因溯源与脚本修复建议
"""

import os
import json
import time
import requests
from typing import Dict, Any, List, Optional
from backend.database import get_all_configs, update_audit_ai_result, get_single_audit_item

DEEPSEEK_DEFAULT_BASE = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"

def get_ai_config() -> Dict[str, str]:
    """获取当前系统的 AI 模型配置"""
    configs = get_all_configs()
    api_key = configs.get("ai_api_key") or DEEPSEEK_DEFAULT_KEY
    api_base = configs.get("ai_api_base") or DEEPSEEK_DEFAULT_BASE
    model = configs.get("ai_model") or DEEPSEEK_DEFAULT_MODEL
    return {
        "api_key": api_key.strip(),
        "api_base": api_base.strip().rstrip("/"),
        "model": model.strip()
    }

def build_diagnostic_prompt(item: Dict[str, Any], task_context: Optional[Dict[str, Any]] = None) -> str:
    """构建针对存疑数据的上下文探针 Prompt"""
    script_name = item.get("script_name", "pdd_gpu_crawler")
    task_title = item.get("task_title", "移动端数据采集")
    title = item.get("title", "")
    price_str = item.get("price_str", "")
    price_num = item.get("price_num", 0.0)
    deviation_pct = item.get("deviation_pct", 0.0)
    risk_tags = item.get("risk_tags", [])
    if isinstance(risk_tags, str):
        try:
            risk_tags = json.loads(risk_tags)
        except Exception:
            risk_tags = [risk_tags]
    shop = item.get("shop", "未知")
    status = item.get("audit_status", "NEED_AUDIT")

    # 根据任务和标题动态推导品类业务语境
    is_5070 = "5070" in title.lower() or "5070" in task_title.lower()
    if is_5070:
        business_intent = (
            "【业务目标】从移动端 App 抓取纯正『RTX 5070Ti 16G 显卡』真实售价。\n"
            "【行业痛点】大量商家在标题中堆叠『5070 12G / 5070Ti 16G』复合关键词，"
            "页面默认展示 12G 标准版的起步引流超低价。\n"
            "自动化脚本若未成功穿透进入 SKU 抽屉精准选中 16G 规格卡片，极易抓取到 12G 引流价格导致数据污染。"
        )
    elif "pdd" in script_name or "采数" in task_title or "电商" in task_title:
        category_name = task_title.replace("拼多多-", "").replace("采数", "").strip() or "目标商品"
        business_intent = (
            f"【业务目标】从电商 App 抓取纯正『{category_name}』真实在售价格，排除低配引流、二手充新、配件定金与虚标。\n"
            f"【核心痛点】部分商家标题堆叠多个规格或型号，页面默认高亮起步引流低价，采数脚本若未深入规格抽屉核准真实规格，可能误采引流价格。"
        )
    elif "bilibili" in script_name:
        business_intent = (
            "【业务目标】抓取 B 站 UP 主视频有效播放量、点赞投币等关键运营指标。\n"
            "【核心痛点】需警惕反爬假数据、负数播放量、营销号标题党以及列表页截流占位异常。"
        )
    else:
        business_intent = (
            "【业务目标】手机端应用业务流巡检与自动化执行。\n"
            "【核心痛点】需排查是否存在未消除的弹窗遮罩、选择器漂移、版本更新拦截或权限阻塞。"
        )

    prompt = f"""请对以下这笔被本地规则引擎拦截的自动化存疑数据进行专业会诊与根因排查：

{business_intent}

【被拦截数据项详情】
- 脚本任务：{task_title} ({script_name})
- 数据序号：#{item.get('item_seq', item.get('id', 0))}
- 商品/目标标题：{title}
- 抓取标价/指标：{price_str} (数值: {price_num})
- 统计大盘偏离度：{deviation_pct}% (若为负数说明显著低于市场均价)
- 本地规则命中标签：{', '.join(risk_tags) if risk_tags else '无'}
- 商家/主体信息：{shop}
- 本地初审定性：{status}

【分析纪律约束】：严格基于上述商品实际标题与指标展开针对性分析，严禁机械套用非本品类的固定模板！

【请按如下结构输出严格的 JSON 诊断报告，严禁包含多余 Markdown 标记】：
{{
  "verdict": "REJECT", // 可选: REJECT (确认是虚假/引流脏数据), PASS (经比对系真实优惠无误), WARNING (严重模棱两可需人工确认)
  "confidence": 95, // 0-100 置信度整数
  "root_cause": "简明扼要的异常根因中文概括，严格结合实际商品",
  "technical_analysis": "详细的技术深度剖析，结合标题字眼、大盘均价偏离度分析为何会出现此问题",
  "script_fix_suggestion": "针对 RPA 自动化脚本代码逻辑、UiSelector选择器或断言机制的具体改写指导",
  "action_suggestion": "REJECTED" // 可选: REJECTED (建议从正式报表剔除), APPROVED (建议核准保留)
}}"""
    return prompt

def call_deepseek_api(prompt: str, config: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """调用 DeepSeek 官方 API 获取诊断结果"""
    if config is None:
        config = get_ai_config()

    url = f"{config['api_base']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": config["model"],
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个资深的手机端 RPA 自动化专家与数据质量审核专家。"
                    "你的任务是对移动端采集到的异常存疑数据进行溯源诊断，"
                    "推断是商家引流虚标、详情页弹窗选择器未生效、还是页面元素漂移，"
                    "并给出对 RPA 脚本代码的具体修改方案。必须以标准 JSON 格式回答。"
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.2,
        "max_tokens": 800,
        "response_format": {"type": "json_object"}
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=25)
    if resp.status_code != 200:
        raise RuntimeError(f"DeepSeek API 请求失败: HTTP {resp.status_code} - {resp.text}")

    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    
    # 解析 JSON
    try:
        parsed = json.loads(content)
        return parsed
    except Exception:
        cleaned = content.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        return json.loads(cleaned.strip())

def build_replay_diagnostic_prompt(item: Dict[str, Any], replay_res: Optional[Dict[str, Any]] = None) -> str:
    """构建包含单品重跑全流程步骤证据链的深度探针 Prompt"""
    script_name = item.get("script_name", "pdd_gpu_crawler")
    task_title = item.get("task_title", "移动端数据采集")
    title = item.get("title", "")
    price_str = item.get("price_str", "")
    price_num = item.get("price_num", 0.0)
    deviation_pct = item.get("deviation_pct", 0.0)
    risk_tags = item.get("risk_tags", [])
    if isinstance(risk_tags, str):
        try:
            risk_tags = json.loads(risk_tags)
        except Exception:
            risk_tags = [risk_tags]
    shop = item.get("shop", "未知")

    is_5070 = "5070" in title.lower() or "5070" in task_title.lower()
    category_name = task_title.replace("拼多多-", "").replace("采数", "").strip() or ("RTX 5070Ti 16G 显卡" if is_5070 else "目标商品")

    replay_section = ""
    corr_p_val = "null"
    if replay_res and replay_res.get("steps"):
        step_lines = []
        for s in replay_res["steps"]:
            step_lines.append(f"  • 步骤 {s['step']} [{s['name']}]: {s['log']}")
        if replay_res.get("corrected_price"):
            corr_p_val = str(replay_res["corrected_price"])
            corr_txt = f"¥{replay_res['corrected_price']}"
        else:
            corr_txt = "未发现目标有效规格或在售配置 (实测无有效款型/已缺货)"

        constraint_tip = ""
        if not replay_res.get("has_target_sku") or not replay_res.get("corrected_price"):
            if "品牌" in (replay_res.get("summary") or ""):
                constraint_tip = "\n【客观事实证据】：真机探针已触发品牌守卫拦截（真机检索误入非目标品牌商品），请务必判定 verdict: NEED_HUMAN, action_suggestion: MANUAL_REVIEW, corrected_price: null！"
            elif is_5070:
                constraint_tip = "\n【客观事实证据】：真机探针经抽屉实拍与 OCR 验真，证实该显卡在售款式为 12G/低配，完全无 16G 选项，系低配引流。必须判定 verdict: FRAUD_EXCLUDED, action_suggestion: REJECTED, corrected_price: null！"
            else:
                constraint_tip = f"\n【客观事实证据】：真机探针经抽屉实拍现场验真，未检测到该『{category_name}』的有效在售款型或已缺货。请判定 verdict: FRAUD_EXCLUDED, action_suggestion: REJECTED, corrected_price: null！"
        else:
            selected_sku = ""
            for s in reversed(replay_res.get("steps", [])):
                if s.get("captured_info", {}).get("selected_sku"):
                    selected_sku = s["captured_info"]["selected_sku"]
                    break
            constraint_tip = f"\n【客观事实证据】：真机探针已成功穿透至规格抽屉，捕获到当前真实在售款型【{selected_sku}】，确认实售价为 ¥{replay_res['corrected_price']}。请判定 verdict: CONFIRMED_FRAUD_CORRECTED (若与首屏起步价有差价) 或 PASS (若首屏标价即为此价), action_suggestion: APPROVED_WITH_CORRECTION, corrected_price: {corr_p_val}！"

        replay_section = f"""
【自动化单品重跑全流程执行证据链 (Step-by-Step Evidence)】
为解决“假成功与模糊疑点”，探针已单独针对该商品重新跑了一遍完整采集流程并完成步骤存证：
{chr(10).join(step_lines)}
- 重测捕获的真实目标规格价格: {corr_txt}
- 重测全链路结论: {replay_res.get('summary', '')}{constraint_tip}
"""

    prompt = f"""你是一个资深的手机端 RPA 自动化专家与数据质量审核专家。
请对以下这笔被本地规则初筛拦截的自动化存疑数据进行终审深度研判与代码修复指导：

【业务目标与痛点】
- 目标：从移动端 App 抓取真实的目标商品『{category_name}』在售价格，排除低配引流与虚假标价。
- 痛点：商家可能在标题堆叠多型号多规格，页面默认展示最低配起步价，原采数脚本极易因未进入规格抽屉精准选中目标款型而误采脏数据。

【分析纪律（极其重要）】：
1. 严禁套用模板：必须严格基于下方的【商品标题】、【商家】、【原始抓取标价】与【步骤证据链】展开客观剖析。
2. 严禁跨品类混淆：当前品类为『{category_name}』。若商品是手机，绝对禁止出现显卡、5070、显存等无关词汇！若商品是显卡，绝对禁止出现手机相关词汇！

【被拦截数据项详情】
- 脚本任务：{task_title} ({script_name})
- 数据序号：#{item.get('item_seq', item.get('id', 0))}
- 商品标题：{title}
- 原始抓取标价：{price_str} (数值: {price_num})
- 偏离度：{deviation_pct}%
- 规则命中标签：{', '.join(risk_tags) if risk_tags else '无'}
- 商家店铺：{shop}
{replay_section}
【请根据以上单品重跑执行证据链进行综合裁决，以标准 JSON 格式输出，严禁包含多余 Markdown 标记】：
{{
  "verdict": "CONFIRMED_FRAUD_CORRECTED", // 可选: CONFIRMED_FRAUD_CORRECTED (证实引流且重测已捕获真实价格), FRAUD_EXCLUDED (确凿为虚假引流无真实规格予以剔除), PASS (经重测比对系真实在售价), NEED_HUMAN (重测后仍无法断定交人工复核)
  "confidence": 98, // 0-100 置信度
  "root_cause": "中文简明根因，严格结合实际商品（如：页面展示引流起步价，探针已深入规格抽屉纠偏出真实在售款式价格¥{corr_p_val}）",
  "technical_analysis": "结合单品重跑步骤1、步骤2与步骤3证据链展开深度剖析，解释原脚本为何会误采以及重测如何定位到真实规格",
  "script_fix_suggestion": "针对采数脚本代码逻辑、选择器或断言机制的具体修改代码指导",
  "corrected_price": {corr_p_val}, // 重测纠偏出的真实价格数值或 null
  "action_suggestion": "APPROVED_WITH_CORRECTION" // 可选: APPROVED_WITH_CORRECTION (建议以纠偏真实价格通过), REJECTED (建议剔除引流), MANUAL_REVIEW (转交人工)
}}"""
    return prompt

def diagnose_audit_item(audit_id: int, item_data: Optional[Dict[str, Any]] = None, auto_replay: bool = True) -> Dict[str, Any]:
    """
    对单笔存疑审计条目发起 DeepSeek AI 智能会诊与单品重跑探针：
    1. 自动针对该商品重新执行采数步骤（进入详情 -> 展开抽屉 -> 选中目标规格 -> 变价监听），记录步骤截图；
    2. 将真实执行链路证据喂送 DeepSeek AI，给出精准诊断、纠偏真实价格与脚本修复建议；
    3. 自动回写数据库。
    """
    from backend.item_replay_runner import replay_single_item

    if item_data is None:
        item_data = get_single_audit_item(audit_id)
        if not item_data:
            raise ValueError(f"未找到 ID 为 {audit_id} 的审计条目")

    # 1. 单品自动化回放与步骤抓取
    replay_res = None
    if auto_replay:
        try:
            replay_res = replay_single_item(audit_id, item_data)
            fresh = get_single_audit_item(audit_id)
            if fresh:
                item_data = fresh
        except Exception as e:
            print(f"Replay notice: {e}")
    else:
        # 如果不重新驱动真机，但条目已有真机实测证据链，读取现有真机存证
        if item_data.get("replay_status") == "COMPLETED" and item_data.get("replay_trace"):
            replay_res = {
                "steps": item_data.get("replay_trace"),
                "corrected_price": item_data.get("corrected_price"),
                "summary": item_data.get("replay_summary"),
                "has_target_sku": bool(item_data.get("corrected_price"))
            }

    # 2. 构建包含执行链路证据的 Prompt
    prompt = build_replay_diagnostic_prompt(item_data, replay_res)
    ai_config = get_ai_config()

    try:
        start_t = time.time()
        ai_res = call_deepseek_api(prompt, ai_config)
        duration = round(time.time() - start_t, 2)

        verdict = ai_res.get("verdict", "CONFIRMED_FRAUD_CORRECTED" if replay_res and replay_res.get("corrected_price") else "REJECT")
        confidence = int(ai_res.get("confidence", 95))
        root_cause = ai_res.get("root_cause", "检测到复合规格低价引流")
        analysis = ai_res.get("technical_analysis") or ai_res.get("analysis", "根据单品重测步骤证据链判定")
        suggestion = ai_res.get("script_fix_suggestion") or ai_res.get("suggestion", "增加SKU规格抽屉显式穿透与价格变动监听")
        action = ai_res.get("action_suggestion") or ("APPROVED_WITH_CORRECTION" if replay_res and replay_res.get("corrected_price") else "REJECTED")
        
        # 物理实测数据具有最高真理级别，绝不允许被大模型脑补覆盖
        real_phone_price = replay_res.get("corrected_price") if replay_res else None
        has_target = replay_res.get("has_target_sku", False) if replay_res else False

        if replay_res:
            if not has_target or real_phone_price is None:
                if "品牌" in (replay_res.get("summary") or ""):
                    verdict = "NEED_HUMAN"
                    action = "MANUAL_REVIEW"
                else:
                    verdict = "FRAUD_EXCLUDED"
                    action = "REJECTED"
                corrected_price = None
            else:
                corrected_price = real_phone_price
                if verdict in ["FRAUD_EXCLUDED", "REJECT", "REJECTED"]:
                    verdict = "CONFIRMED_FRAUD_CORRECTED"
                    action = "APPROVED_WITH_CORRECTION"
        else:
            corrected_price = ai_res.get("corrected_price")

        normalized = {
            "verdict": verdict,
            "confidence": confidence,
            "root_cause": root_cause,
            "technical_analysis": analysis,
            "script_fix_suggestion": suggestion,
            "action_suggestion": action,
            "corrected_price": corrected_price,
            "diagnose_duration": duration,
            "replay_summary": replay_res.get("summary") if replay_res else ""
        }

        update_audit_ai_result(audit_id, normalized)
        return normalized

    except Exception as e:
        err_msg = str(e)
        corr_p = replay_res.get("corrected_price") if replay_res else None
        cat_tag = item_data.get("task_title", "").replace("拼多多-", "").replace("采数", "").strip() or "目标商品"
        fallback = {
            "verdict": "CONFIRMED_FRAUD_CORRECTED" if corr_p else "REJECT",
            "confidence": 90 if corr_p else 75,
            "root_cause": f"单品重测已核准在售款式 (实价已纠偏为¥{corr_p})" if corr_p else "本地规则初筛定性",
            "technical_analysis": f"单品重跑探针已执行真机核验，获取到在售价格 ¥{corr_p}。DeepSeek 响应: {err_msg}",
            "script_fix_suggestion": f"在详情页点击展开规格抽屉，遍历匹配目标『{cat_tag}』款式卡片后再提取价格",
            "action_suggestion": "APPROVED_WITH_CORRECTION" if corr_p else "REJECTED",
            "corrected_price": corr_p,
            "diagnose_duration": 0.0,
            "replay_summary": replay_res.get("summary") if replay_res else ""
        }
        update_audit_ai_result(audit_id, fallback)
        return fallback

def batch_diagnose_script_anomalies(script_name: str, max_items: int = 15) -> Dict[str, Any]:
    """批量对指定脚本下的所有待审/异常项发起单品重跑 + DeepSeek AI 智能诊断"""
    from backend.database import get_audit_items
    
    pending_items = get_audit_items(script_name=script_name, status="PENDING", limit=max_items)
    
    if not pending_items:
        all_abnormal = get_audit_items(script_name=script_name, status="REJECT", limit=max_items)
        pending_items = all_abnormal
        
    diagnosed_count = 0
    results = []

    for it in pending_items:
        aid = it["id"]
        # 如果已经重跑且有完整 AI 诊断，跳过
        if it.get("ai_verdict") and it.get("ai_confidence", 0) > 0 and it.get("replay_status") == "COMPLETED":
            continue
            
        diag = diagnose_audit_item(aid, it, auto_replay=True)
        results.append({
            "id": aid,
            "seq": it.get("item_seq"),
            "title": it.get("title"),
            "diagnosis": diag
        })
        diagnosed_count += 1
        time.sleep(0.3)

    return {
        "script_name": script_name,
        "diagnosed_count": diagnosed_count,
        "items": results
    }
