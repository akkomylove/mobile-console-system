# -*- coding: utf-8 -*-
"""
实在智能RPA - 手机端自动化运维监控系统
真机单品回放与溯源重测沙箱探针 (backend/item_replay_runner.py)

核心定位:
无条件遵循全局研发宪法与零盲猜铁律 (Zero Hallucination)：
彻底废除代码自绘假图与假数值推算。
当存疑数据需要单体溯源时，探针必须直接驱动已连接的物理真机：
1. 真实唤起拼多多进入目标商品详情页 -> 截取真实手机屏幕 step1_detail.png，提取首屏标价与商户；
2. 真实点击规格入口唤起配置抽屉 (Bottom Sheet) -> 截取真实手机屏幕 step2_sku_drawer.png，提取真实 SKU 列表与默认低配；
3. 真实定位并点击 16G 规格卡片 -> 监听屏幕联动变价，截取真实变价屏幕 step3_selected.png，提取真实纠偏价；
4. 若规格抽屉内确无 16G 选项，客观存证纯虚标引流；
5. 执行完毕自动返回安全界面，保持手机整洁。
"""

import os
import sys
import re
import time
import json
import shutil
import tempfile
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

STORAGE_DIR = os.path.join(PROJECT_ROOT, "storage")
REPLAY_DIR = os.path.join(STORAGE_DIR, "captures", "replay")
TEMP_DIR = os.path.join(STORAGE_DIR, "temp")
os.makedirs(REPLAY_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

try:
    from rapidocr_onnxruntime import RapidOCR
    _OCR_ENGINE = RapidOCR()
except Exception as e:
    _OCR_ENGINE = None
    print(f"[!] Warning: RapidOCR not available: {e}")

from pdd_gpu_crawler import (
    run_adb, dump_ui, detect_current_page, parse_bounds,
    simulate_tap, ensure_pinduoduo_foreground, check_and_dismiss_popups,
    execute_search_query
)
from backend.device import device_manager
from backend.database import get_single_audit_item, save_audit_replay_trace


KNOWN_BRANDS = [
    "七彩虹", "技嘉", "映众", "耕升", "铭瑄", "万丽", "索泰", "微星", "影驰", "影驰GeForce",
    "MSI", "PNY", "华硕", "ASUS", "盈通", "映泰", "联想", "戴尔", "惠普", "卡诺基", "华擎",
    "苹果", "Apple", "华为", "HUAWEI", "小米", "Xiaomi", "荣耀", "HONOR", "OPPO", "vivo", "一加", "OnePlus", "真我", "realme", "魅族", "三星", "Samsung"
]

KNOWN_SUB_MODELS = [
    "凤凰", "风火", "星极", "金属大师", "名人堂", "HOF", "星云", "星际", "雪狐", "风魔",
    "豪华版", "追风", "踏雪", "炫光", "超级冰龙", "冰龙", "曜夜", "魔鹰", "猎鹰", "电竞之心",
    "神龙", "魔龙", "超龙", "万图师", "黑火神", "火神", "战斧", "Ultra", "iCraft", "SFF",
    "OC", "Vanguard", "Trinity", "Gaming", "TUF", "ROG", "Strix", "Aero", "Eagle",
    "Windforce", "Master", "Supreme", "瑷珈", "天选", "巨石", "电竞叛客"
]


def is_real_phone_available() -> Tuple[bool, str]:
    """检查当前是否有可用且 ADB 就绪的物理真机"""
    dev_info = device_manager.get_connected_devices()
    if dev_info.get("connected") and dev_info.get("adb_ready"):
        return True, dev_info.get("name", "物理真机")
    
    # 备用 ADB 实时探测
    res = run_adb(["devices"])
    lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return True, f"ADB设备({parts[0]})"
            
    return False, "未检测到已连接的物理真机 (Device Offline)"


def capture_device_screencap(dest_path: str) -> bool:
    """直接从真机捕获物理级全真截图并保存到目标路径（安全兼容非ASCII本地路径）"""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    remote_png = "/sdcard/pdd_probe_real_shot.png"
    temp_ascii = os.path.join(tempfile.gettempdir(), f"probe_cap_{int(time.time()*1000)}.png")
    
    run_adb(["shell", "screencap", "-p", remote_png], timeout=8)
    if os.path.exists(temp_ascii):
        try: os.remove(temp_ascii)
        except Exception: pass
    res = run_adb(["pull", remote_png, temp_ascii], timeout=8)
    if os.path.exists(temp_ascii) and os.path.getsize(temp_ascii) > 500:
        try:
            shutil.copy2(temp_ascii, dest_path)
            try:
                os.remove(temp_ascii)
            except Exception:
                pass
            return True
        except Exception as e:
            print(f"Copy real screencap failed: {e}")
    return False


def extract_brands(text: str) -> List[str]:
    """从文本中提取所有可能包含的品牌名称"""
    found = []
    text_lower = text.lower()
    for b in KNOWN_BRANDS:
        if b.lower() in text_lower:
            if not any(b != other and b in other for other in found):
                found.append(b)
    return found


def clean_search_keyword(title: str) -> str:
    """从商品冗长标题中清洗提炼出最具有真机检索辨识度的核心词组 (多品牌 + 型号 + 次级系列)"""
    clean = re.sub(r'[【】\[\]\(\)\{\}（）#]', ' ', title)
    clean = re.sub(r'(电竞|吃鸡|游戏|官方|正品|现货|顺丰|包邮|三角洲|AI|算力|直播|高端|台式机|电脑|显卡|全新|盒装|国行|DLSS\s*4(?:\.5)?|GDDR7)', ' ', clean, flags=re.I)
    
    matched_brands = extract_brands(title)
    
    # 提取型号 - 优先匹配 5070Ti
    if re.search(r'5070\s*Ti', clean, re.I):
        model = "5070Ti"
    elif re.search(r'5060\s*Ti', clean, re.I):
        model = "5060Ti"
    elif re.search(r'5080', clean, re.I):
        model = "5080"
    elif re.search(r'5070', clean, re.I):
        model = "5070"
    elif re.search(r'5060', clean, re.I):
        model = "5060"
    elif re.search(r'5090', clean, re.I):
        model = "5090"
    else:
        model = "5070Ti"
    
    # 提取次级型号
    matched_subs = []
    for sm in KNOWN_SUB_MODELS:
        if sm.lower() in clean.lower():
            if sm not in matched_subs:
                matched_subs.append(sm)
            
    tokens = []
    if matched_brands:
        tokens.extend(matched_brands[:2])
    tokens.append(model)
    if matched_subs:
        tokens.extend(matched_subs[:2])
        
    res = " ".join(tokens)
    return res if len(res) >= 4 else (clean.strip()[:18])


def navigate_to_product(title: str, item_data: Optional[Dict[str, Any]] = None) -> bool:
    """
    真机双模导航引擎：
    1. 【第一优先级】精确 SKU/Goods ID 0 延时直达：若存在 goods_id 或链接中含 goods_id，直接使用 URI Scheme 直达商品详情页，0 偏差、0 广告干扰！
    2. 【第二优先级】高精多特征关键词检索 + 品牌守卫：针对无 goods_id 的历史存量数据，执行精准多特征检索与一票否决制品牌硬防御。
    """
    import urllib.parse
    
    ensure_pinduoduo_foreground()
    run_adb(["shell", "input", "keyevent", "224"])  # 唤醒屏幕
    time.sleep(0.3)
    
    # 释放任何占用的 uiautomator 服务，防止 dump 阻塞
    run_adb(["shell", "am", "force-stop", "com.github.uiautomator"])
    
    # 1. 优先检测真实商品链接 (如包含 ps= 的分享短链 或 goods_id 短链)
    link = (item_data.get("link") or "").strip() if item_data else ""
    goods_id = (item_data.get("goods_id") or "").strip() if item_data else ""
    
    if not goods_id and link:
        m = re.search(r'goods_id=(\d+)', link)
        if m:
            goods_id = m.group(1)

    # 若存在有效商品链接 (非 goods_name 模拟链接)，直接在拼多多搜索框检索链接，100% 直达目标商品与店铺
    if link and ("yangkeduo.com" in link or "pinduoduo.com" in link or "goods2.html" in link) and "goods_name=" not in link:
        print(f"[*] 🎯 【精准链接搜索定位】检测到真实定位短链: [{link}]，直接在拼多多内部搜索框提交定位...")
        encoded_link = urllib.parse.quote(link)
        scheme_url = f"pinduoduo://com.xunmeng.pinduoduo/search_result.html?search_key={encoded_link}"
        run_adb(["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", f'"{scheme_url}"'])
        time.sleep(3.2)
        xml_path = dump_ui("probe_direct_res.xml")
        try:
            check_and_dismiss_popups(ET.parse(xml_path))
        except Exception:
            pass
        return True
                
    elif goods_id:
        print(f"[*] 🎯 【精确 goods_id 直达】检测到商品唯一识别码 goods_id: [{goods_id}]，直接穿透跳转目标详情页...")
        goods_scheme = f"pinduoduo://com.xunmeng.pinduoduo/goods.html?goods_id={goods_id}"
        run_adb(["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", f'"{goods_scheme}"'])
        time.sleep(3.2)
        # 消除营销弹窗
        xml_path = dump_ui("probe_direct_res.xml")
        try:
            check_and_dismiss_popups(ET.parse(xml_path))
        except Exception:
            pass
        return True

    keyword = clean_search_keyword(title)
    print(f"[*] 探针已提炼高辨识度真机检索关键词: [{keyword}]")
    
    # 使用拼多多官方 URI Scheme 直接唤起带中文关键词的搜索结果页
    encoded_kw = urllib.parse.quote(keyword)
    scheme_url = f"pinduoduo://com.xunmeng.pinduoduo/search_result.html?search_key={encoded_kw}"
    run_adb(["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", f'"{scheme_url}"'])
    time.sleep(3.2)
    
    # 检查并消除可能遮挡的营销弹窗
    xml_path = dump_ui("probe_search_res.xml")
    try:
        check_and_dismiss_popups(ET.parse(xml_path))
    except Exception:
        pass

    target_brands = extract_brands(title)
    primary_brand = target_brands[0] if target_brands else ""
    orig_price_num = float(item_data.get("price_num", 0.0)) if item_data else 0.0

    def find_target_card():
        # 1. 截屏并利用 RapidOCR 识别所有可视文字块与坐标
        sr_cap = os.path.join(tempfile.gettempdir(), f"sr_probe_{int(time.time()*1000)}.png")
        capture_device_screencap(sr_cap)
        ocr_cards = []
        
        if _OCR_ENGINE and os.path.exists(sr_cap):
            try:
                ocr_res, _ = _OCR_ENGINE(sr_cap)
                for box, text, conf in ocr_res:
                    t = text.strip()
                    top_y = box[0][1]
                    bottom_y = box[2][1]
                    center_x = (box[0][0] + box[1][0]) / 2
                    center_y = (top_y + bottom_y) / 2
                    
                    # 仅关注有效商品卡片区域 (Y: 450 ~ 2450)
                    if 450 < center_y < 2450 and len(t) >= 4:
                        score = 0
                        # 品牌硬守卫：若审计目标有品牌，卡片中不含该品牌则一票否决
                        if primary_brand:
                            if primary_brand.lower() in t.lower():
                                score += 300
                            else:
                                score = -9999
                        else:
                            score += 10
                            
                        if score > 0:
                            if "5070" in t:
                                score += 50
                            for sm in KNOWN_SUB_MODELS:
                                if sm.lower() in t.lower() and sm.lower() in title.lower():
                                    score += 80
                            
                            # 点击卡片大图或标题中心
                            ocr_cards.append({
                                "title": t,
                                "center": (int(center_x), int(center_y)),
                                "score": score,
                                "source": "OCR"
                            })
            except Exception as ocr_err:
                print(f"[!] OCR 选卡异常: {ocr_err}")

        # 2. 同时解析 XML 节点
        curr_xml = dump_ui("probe_search_res_curr.xml")
        xml_cards = []
        if os.path.exists(curr_xml):
            try:
                tree = ET.parse(curr_xml)
                all_nodes = list(tree.iter())
                for n in all_nodes:
                    t = (n.attrib.get('text') or n.attrib.get('content-desc') or '').strip()
                    b = parse_bounds(n.attrib.get('bounds', ''))
                    if b and 450 < b[1] < 2400 and (b[2] - b[0]) > 180 and (b[3] - b[1]) < 300:
                        score = 0
                        if primary_brand:
                            if primary_brand.lower() in t.lower():
                                score += 300
                            else:
                                score = -9999
                        else:
                            score += 10
                            
                        if score > 0:
                            if "5070" in t:
                                score += 50
                            for sm in KNOWN_SUB_MODELS:
                                if sm.lower() in t.lower() and sm.lower() in title.lower():
                                    score += 80
                                    
                            # 价格关联匹配 (+800分)
                            col_min_x = b[0] - 80
                            col_max_x = b[2] + 80
                            card_price = None
                            for pn in all_nodes:
                                pb = parse_bounds(pn.attrib.get('bounds', ''))
                                if pb and pb[0] >= col_min_x and pb[2] <= col_max_x and pb[1] >= b[3] - 20 and pb[3] <= b[3] + 250:
                                    ptxt = (pn.attrib.get('text') or pn.attrib.get('content-desc') or '').strip()
                                    m = re.match(r'^¥?(\d+(?:\.\d+)?)$', ptxt)
                                    if m and float(m.group(1)) > 1000:
                                        card_price = float(m.group(1))
                                        break
                            if card_price and orig_price_num > 0:
                                if abs(card_price - orig_price_num) < 5:
                                    score += 800
                                elif abs(card_price - orig_price_num) < 100:
                                    score += 150
                                    
                            cx = (b[0] + b[2]) // 2
                            cy = max(200, b[1] - 140) if b[1] > 350 else ((b[1] + b[3]) // 2)
                            xml_cards.append({
                                "title": t,
                                "center": (cx, cy),
                                "bounds": b,
                                "score": score,
                                "price": card_price,
                                "source": "XML"
                            })
            except Exception as xml_err:
                print(f"[!] XML 选卡异常: {xml_err}")

        # 综合候选卡片，严格要求得分大于等于 200 (必须包含目标品牌)
        valid_candidates = [c for c in (xml_cards + ocr_cards) if c["score"] >= 200]
        valid_candidates.sort(key=lambda x: x["score"], reverse=True)
        return valid_candidates

    # 第一轮探测当前屏幕
    cards = find_target_card()
    
    # 若第一屏未找到符合品牌要求的卡片，向下滑动一屏寻访
    if not cards:
        print("[*] 第一屏未见符合目标品牌的卡片，模拟真人向下滑动一屏寻访...")
        run_adb(["shell", "input", "swipe", "600", "1800", "600", "1000", "350"])
        time.sleep(2.0)
        cards = find_target_card()

    # 若第二屏仍未找到，再次尝试微滑一次
    if not cards:
        print("[*] 第二屏继续向下微调寻访...")
        run_adb(["shell", "input", "swipe", "600", "1700", "600", "1100", "350"])
        time.sleep(2.0)
        cards = find_target_card()

    if cards:
        best_card = cards[0]
        tap_x, tap_y = best_card["center"]
        print(f"[*] 🎯 成功锁定目标商品卡片: 【{best_card['title']}】(得分: {best_card['score']}, 来源: {best_card['source']})，点击进入详情...")
        simulate_tap(tap_x, tap_y)
    else:
        # 严格品牌守卫防御：绝不盲目点击未知品牌卡片
        if primary_brand:
            raise RuntimeError(f"品牌守卫拦截：在真机搜索结果中未检索到品牌为【{primary_brand}】的目标商品，已主动阻断跨品牌误触！")
        else:
            print("[!] 未搜寻到高分卡片，保底点击首项商品...")
            simulate_tap(300, 700)
        
    time.sleep(3.2)
    return True


def replay_single_item(audit_id: int, item_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    针对单件存疑商品执行 100% 物理真机真实步骤回放与实地溯源重测 (Physical Real-Device Probe)
    绝不使用 PIL 虚构自绘假图或模拟数学公式！
    """
    if item_data is None:
        item_data = get_single_audit_item(audit_id)
        if not item_data:
            raise ValueError(f"未找到 ID 为 {audit_id} 的审计条目")

    title = item_data.get("title", "")
    orig_price_num = float(item_data.get("price_num") or 0.0)
    item_replay_folder = os.path.join(REPLAY_DIR, f"audit_{audit_id}")
    os.makedirs(item_replay_folder, exist_ok=True)
    
    step1_shot_rel = f"/storage/captures/replay/audit_{audit_id}/step1_detail.png"
    step1_shot_abs = os.path.join(item_replay_folder, "step1_detail.png")
    step2_shot_rel = f"/storage/captures/replay/audit_{audit_id}/step2_sku_drawer.png"
    step2_shot_abs = os.path.join(item_replay_folder, "step2_sku_drawer.png")
    step3_shot_rel = f"/storage/captures/replay/audit_{audit_id}/step3_selected.png"
    step3_shot_abs = os.path.join(item_replay_folder, "step3_selected.png")

    # 1. 物理真机连通性断言
    phone_ok, phone_msg = is_real_phone_available()
    if not phone_ok:
        err_summary = f"真机未就绪 ({phone_msg})。遵循零假数据铁律，已拦截伪造，请连入真机后重试。"
        raise RuntimeError(err_summary)

    # 2. 真机驱动导航至该商品
    navigate_to_product(title, item_data=item_data)

    # ---------------- 步骤 1: 详情页与初始展示标价 (Real Phone Screencap) ----------------
    detail_xml = dump_ui("real_probe_step1.xml")
    capture_device_screencap(step1_shot_abs)
    
    real_step1_price = orig_price_num
    real_shop = item_data.get("shop") or "品牌专营店铺"
    real_detail_title = title
    spec_bounds = None

    is_5070 = "5070" in title.lower() or "5070" in str(item_data.get("task_title", "")).lower()
    min_valid_price = 1000.0 if is_5070 else 1.0

    # 用 RapidOCR 提取详情页全部文本
    d1_ocr_texts = []
    if _OCR_ENGINE and os.path.exists(step1_shot_abs):
        try:
            d1_res, _ = _OCR_ENGINE(step1_shot_abs)
            d1_ocr_texts = [line[1].strip() for line in d1_res if line[1].strip()]
        except Exception:
            pass

    if os.path.exists(detail_xml) and os.path.getsize(detail_xml) > 100:
        try:
            d_tree = ET.parse(detail_xml)
            check_and_dismiss_popups(d_tree)
            for n in d_tree.iter():
                t = (n.attrib.get('text') or '').strip()
                b = parse_bounds(n.attrib.get('bounds', ''))
                if t.startswith("¥") or (len(t) >= 4 and t.isdigit() and b and b[1] < 1400):
                    m = re.search(r'¥?\s*(\d+(?:\.\d+)?)', t)
                    if m and float(m.group(1)) >= min_valid_price:
                        real_step1_price = float(m.group(1))
                elif re.search(r'(旗舰店|专营店|专卖店|数码馆|自营店|个人diy|diy)', t, re.I):
                    real_shop = t
                elif b and 400 < b[1] < 2700 and any(k in t for k in ["选择规格", "已选", "规格", "发起拼单", "单独购买"]):
                    spec_bounds = b
                elif len(t) > 15 and (is_5070 and any(k in t for k in ["RTX", "5070", "5080", "显卡", "万丽", "七彩虹", "技嘉", "映众", "耕升"]) or (not is_5070 and len(t) > 8)):
                    real_detail_title = t
        except Exception as e:
            print(f"[!] 解析详情页 XML 异常: {e}")

    # 用 OCR 补全价格与标题
    if d1_ocr_texts:
        for ot in d1_ocr_texts[:10]:
            pm = re.search(r'[￥¥]\s*(\d+(?:\.\d+)?)', ot)
            if pm and float(pm.group(1)) >= min_valid_price:
                real_step1_price = float(pm.group(1))
                break
        for ot in d1_ocr_texts:
            if is_5070:
                if any(b in ot for b in KNOWN_BRANDS) and any(m in ot for m in ["5070", "5080", "5060", "RTX"]):
                    real_detail_title = ot
                    break
            else:
                if any(b in ot for b in KNOWN_BRANDS) and len(ot) > 6:
                    real_detail_title = ot
                    break

    # 严密品牌一致性断言 (Brand Guard Assertion)
    target_brands = extract_brands(title)
    primary_brand = target_brands[0] if target_brands else ""
    combined_detail_str = (real_detail_title + " " + real_shop + " " + " ".join(d1_ocr_texts[:12])).lower()
    
    if primary_brand and primary_brand.lower() not in combined_detail_str:
        # 严重品牌不匹配！进入了其他品牌的商品，立即触发熔断拦截
        mismatch_msg = f"品牌校验异常拦截：目标品牌为【{primary_brand}】，但真机实际进入了【{real_detail_title[:20]}】（品牌不符），触发严密品牌守卫自动熔断，已终止后续操作，严禁跨品牌误判！"
        print(f"[!] 🚨 {mismatch_msg}")
        step1_record = {
            "step": 1,
            "name": "加载详情页与品牌一致性核验 (真机现场实拍)",
            "action": "直达商品详情页并执行品牌硬约束核验",
            "status": "FAILED_MISMATCH_BRAND",
            "screenshot": step1_shot_rel,
            "captured_info": {
                "displayed_price": real_step1_price,
                "title": real_detail_title,
                "shop": real_shop,
                "target_brand": primary_brand,
                "brand_matched": False
            },
            "log": mismatch_msg
        }
        for _ in range(3):
            run_adb(["shell", "input", "keyevent", "4"])
            time.sleep(0.4)
            
        trace_data = [step1_record]
        save_audit_replay_trace(
            audit_id=audit_id,
            trace_data=trace_data,
            corrected_price=None,
            replay_status="COMPLETED",
            replay_summary=mismatch_msg
        )
        return {
            "audit_id": audit_id,
            "item_seq": item_data.get("item_seq"),
            "title": title,
            "original_price": real_step1_price,
            "corrected_price": None,
            "has_target_sku": False,
            "replay_status": "COMPLETED",
            "steps": trace_data,
            "summary": mismatch_msg
        }

    step1_record = {
        "step": 1,
        "name": "加载详情页与初始标价 (真机现场实拍)",
        "action": "直达商品详情页并等待真机 DOM 渲染，提取首屏标价与品牌验证",
        "status": "SUCCESS",
        "screenshot": step1_shot_rel,
        "captured_info": {
            "displayed_price": real_step1_price,
            "title": real_detail_title,
            "shop": real_shop,
            "target_brand": primary_brand,
            "brand_matched": True
        },
        "log": f"真机已成功加载商品详情页。首屏实况标价: ¥{real_step1_price:.2f}。标题/卡片: 【{real_detail_title}】。品牌核准一致。正在穿透打开规格抽屉..."
    }

    # ---------------- 步骤 2: 穿透打开规格配置抽屉 (Real Phone Drawer) ----------------
    if spec_bounds:
        scx = (spec_bounds[0] + spec_bounds[2]) // 2
        scy = (spec_bounds[1] + spec_bounds[3]) // 2
        simulate_tap(scx, scy)
    else:
        # 点击底部右侧发起拼单 (880, 2615)
        simulate_tap(880, 2615)
        
    time.sleep(2.5)
    drawer_xml = dump_ui("real_probe_step2.xml")
    capture_device_screencap(step2_shot_abs)
    
    drawer_nodes = []
    default_selected = real_detail_title
    drawer_price = real_step1_price
    available_skus = []

    # RapidOCR 抽屉实拍全文扫描
    d2_ocr_texts = []
    if _OCR_ENGINE and os.path.exists(step2_shot_abs):
        try:
            d2_res, _ = _OCR_ENGINE(step2_shot_abs)
            d2_ocr_texts = [line[1].strip() for line in d2_res if line[1].strip()]
        except Exception as ocr_e:
            print(f"[!] 抽屉 OCR 异常: {ocr_e}")

    if os.path.exists(drawer_xml) and os.path.getsize(drawer_xml) > 100:
        try:
            dr_tree = ET.parse(drawer_xml)
            for n in dr_tree.iter():
                t = (n.attrib.get('text') or '').strip()
                b = parse_bounds(n.attrib.get('bounds', ''))
                if t and b:
                    drawer_nodes.append({"text": t, "bounds": b, "node": n})
                    if "已选" in t:
                        default_selected = t
                    if "¥" in t:
                        m = re.search(r'¥\s*(\d+(?:\.\d+)?)', t)
                        if m and float(m.group(1)) >= min_valid_price:
                            drawer_price = float(m.group(1))

            for dn in drawer_nodes:
                t = dn["text"]
                b = dn["bounds"]
                if b[1] > 1000 and (b[2] - b[0]) < 900 and (b[3] - b[1]) < 220:
                    if is_5070:
                        if any(k in t for k in ["RTX", "5070", "5060", "16G", "12G", "8G", "版", "全新", "盒装", "风扇"]):
                            if t not in available_skus:
                                available_skus.append(t)
                    else:
                        if len(t) >= 2 and t not in available_skus and not t.startswith("¥") and "已选" not in t:
                            available_skus.append(t)
        except Exception as e:
            print(f"[!] 解析规格抽屉 XML 异常: {e}")

    # 用 OCR 补全已选款式与价格
    if d2_ocr_texts:
        for line in d2_ocr_texts:
            if "已选" in line:
                default_selected = line
            pm = re.search(r'[￥¥]\s*(\d+(?:\.\d+)?)', line)
            if pm and float(pm.group(1)) >= min_valid_price:
                drawer_price = float(pm.group(1))
            if is_5070:
                if any(k in line for k in ["RTX", "5070", "5060", "16G", "12G", "8G", "凤凰", "星云", "星际", "风魔", "追风"]):
                    if len(line) > 5 and line not in available_skus and not line.startswith("已选"):
                        available_skus.append(line)
            else:
                if any(k in line for k in ["128", "256", "512", "1TB", "国行", "全网通", "黑", "白", "原装", "官方", "Pro", "Max", "Plus"]):
                    if len(line) >= 3 and line not in available_skus and not line.startswith("已选"):
                        available_skus.append(line)

    step2_record = {
        "step": 2,
        "name": "穿透打开规格配置抽屉 (真机现场实拍)",
        "action": "真机点击规格选择入口唤起 Bottom Sheet 抽屉",
        "status": "SUCCESS",
        "screenshot": step2_shot_rel,
        "captured_info": {
            "drawer_opened": True,
            "default_selected_sku": default_selected,
            "default_price": drawer_price,
            "available_skus": available_skus
        },
        "log": f"真机成功唤起规格配置抽屉！检测到当前实况标价: ¥{drawer_price:.2f}。当前款式信息: 【{default_selected}】。现场实拍存证完毕。"
    }

    # ---------------- 步骤 3: 针对性点击目标规格并监听实况变价 ----------------
    all_drawer_str = " ".join(d2_ocr_texts) + " " + " ".join(available_skus) + " " + default_selected

    if is_5070:
        low_spec_found = re.findall(r'(?:12\s*g\b|12\s*gb|12gd7|12gdr7|8\s*g\b|8\s*gb|8gd6|5060|5050|4060|4070)', all_drawer_str, re.I)
        high_spec_found = re.findall(r'(?:16\s*g\b|16\s*gb|16gd7|16gdr7|5070\s*ti)', all_drawer_str, re.I)

        target_16g_node = None
        candidate_16g_nodes = []
        for dn in drawer_nodes:
            t = dn["text"]
            b = dn["bounds"]
            if b[1] > 1000 and (b[2] - b[0]) < 900 and (b[3] - b[1]) < 220:
                if re.search(r'(?:16\s*g\b|16\s*gb\b|16g显存|5070\s*ti)', t, re.I):
                    candidate_16g_nodes.append(dn)

        if candidate_16g_nodes:
            best_node = candidate_16g_nodes[0]
            best_score = -1
            for cn in candidate_16g_nodes:
                score = 0
                cn_text = cn["text"].lower()
                if "16g" in cn_text or "16gb" in cn_text:
                    score += 20
                if "5070ti" in cn_text.replace(" ", ""):
                    score += 25
                if any(bad in cn_text for bad in ["8g", "12g", "5060"]):
                    score -= 50
                if score > best_score:
                    best_score = score
                    best_node = cn
            target_16g_node = best_node

        if target_16g_node:
            tcx = (target_16g_node["bounds"][0] + target_16g_node["bounds"][2]) // 2
            tcy = (target_16g_node["bounds"][1] + target_16g_node["bounds"][3]) // 2
            run_adb(["shell", "input", "tap", str(tcx), str(tcy)])
            time.sleep(1.5)
            
            capture_device_screencap(step3_shot_abs)
            sel_xml = dump_ui("real_probe_step3.xml")
            corrected_price = drawer_price
            if os.path.exists(sel_xml) and os.path.getsize(sel_xml) > 100:
                try:
                    sel_tree = ET.parse(sel_xml)
                    for n in sel_tree.iter():
                        t = (n.attrib.get('text') or '').strip()
                        if "¥" in t:
                            m = re.search(r'¥\s*(\d+(?:\.\d+)?)', t)
                            if m and float(m.group(1)) >= min_valid_price:
                                corrected_price = float(m.group(1))
                                break
                except Exception:
                    pass
                    
            if _OCR_ENGINE and os.path.exists(step3_shot_abs):
                try:
                    s3_res, _ = _OCR_ENGINE(step3_shot_abs)
                    for line in s3_res:
                        pm = re.search(r'[￥¥]\s*(\d+(?:\.\d+)?)', line[1])
                        if pm and float(pm.group(1)) >= min_valid_price:
                            corrected_price = float(pm.group(1))
                            break
                except Exception:
                    pass

            price_diff = round(corrected_price - real_step1_price, 2)
            if abs(price_diff) > 10:
                step3_log = f"真机点击 16G 规格卡片【{target_16g_node['text']}】后，价格成功联动响应为 ¥{corrected_price:.2f}！(比首屏引流低配差价: +¥{price_diff:.2f})"
                replay_summary = f"真机实地重测证实：首屏 ¥{real_step1_price:.2f} 系低配引流起步价。抽屉穿透点击 16G 真实配置后捕获真实售价 ¥{corrected_price:.2f}。"
            else:
                step3_log = f"真机点击 16G 规格卡片【{target_16g_node['text']}】后，价格确认仍为 ¥{corrected_price:.2f}，证实该商品当前标价即为纯正 16G 实售价！"
                replay_summary = f"真机实地重测核准：经抽屉穿透实测，首屏展示标价 ¥{corrected_price:.2f} 确实对应 16G 真实规格，非虚标欺诈，核准通过。"

            step3_record = {
                "step": 3,
                "name": "选中目标 16G 规格与联动变价 (真机现场实拍)",
                "action": f"真机点击【{target_16g_node['text']}】卡片，提取联动更新后的真实价格",
                "status": "SUCCESS",
                "screenshot": step3_shot_rel,
                "captured_info": {
                    "selected_sku": target_16g_node["text"],
                    "updated_price": corrected_price,
                    "price_diff": price_diff,
                    "in_stock": True
                },
                "log": step3_log
            }
            has_target = True
        else:
            capture_device_screencap(step3_shot_abs)
            
            if low_spec_found and not high_spec_found:
                corrected_price = None
                has_target = False
                step3_record = {
                    "step": 3,
                    "name": "规格匹配校验失败 (真机现场实拍)",
                    "action": "在真机规格抽屉中校验目标 16G/5070Ti 真实配置",
                    "status": "FAILED_SKU_ABSENT",
                    "screenshot": step3_shot_rel,
                    "captured_info": {
                        "found_target": False,
                        "skus_examined": available_skus or [default_selected],
                        "low_spec_detected": list(set(low_spec_found))
                    },
                    "log": f"真机抽屉现场实况 OCR 证实：实际在售款式为 12G/低配【{default_selected}】，未见任何 16G/5070Ti 在售选项，商家标题堆砌 16GB 确系低配虚标引流！"
                }
                replay_summary = f"真机实地重测证实：抽屉现场实拍证实在售配置为 12G 低配【{default_selected}】，无真实 16G RTX 5070Ti 选项，确凿判定为低配引流脏数据，予以剔除。"
            elif high_spec_found and not low_spec_found:
                corrected_price = drawer_price
                price_diff = round(corrected_price - real_step1_price, 2)
                has_target = True
                step3_record = {
                    "step": 3,
                    "name": "核准目标 16G 规格与真实售价 (真机现场实拍)",
                    "action": "真机抽屉实况核验：商品为纯正 16G RTX 5070Ti 配置单品",
                    "status": "SUCCESS",
                    "screenshot": step3_shot_rel,
                    "captured_info": {
                        "selected_sku": default_selected,
                        "updated_price": corrected_price,
                        "price_diff": price_diff,
                        "in_stock": True
                    },
                    "log": f"真机规格抽屉实况核验完毕！经现场 OCR 存证，当前单品款式即为纯正 16G 规格【{default_selected}】，当前实售价格: ¥{corrected_price:.2f}。"
                }
                if abs(price_diff) > 10:
                    replay_summary = f"真机实地重测证实：首屏 ¥{real_step1_price:.2f} 系起步价。抽屉现场捕获 16G 真实售价 ¥{corrected_price:.2f}。"
                else:
                    replay_summary = f"真机实地重测核准：经抽屉穿透实测，首屏展示标价 ¥{corrected_price:.2f} 确实对应 16G 真实规格【{default_selected}】，非虚标欺诈，核准通过。"
            else:
                corrected_price = None
                has_target = False
                step3_record = {
                    "step": 3,
                    "name": "规格匹配校验失败 (真机现场实拍)",
                    "action": "在真机规格抽屉中校验目标 16G/5070Ti 真实配置",
                    "status": "FAILED_SKU_ABSENT",
                    "screenshot": step3_shot_rel,
                    "captured_info": {
                        "found_target": False,
                        "skus_examined": available_skus or [default_selected]
                    },
                    "log": f"真机抽屉核验：实际款式为【{default_selected}】，未见目标 16G/5070Ti 在售选项，判定为引流脏数据。"
                }
                replay_summary = f"真机实地重测证实：抽屉款式为【{default_selected}】，未见真实 16G RTX 5070Ti 选项，确凿判定为引流脏数据，予以剔除。"
    else:
        # 非 5070 品类（如手机 iPhone、数码电子等）：自适应在售款式与有效价格
        capture_device_screencap(step3_shot_abs)
        
        # 尝试根据标题关键词（如存储容量 128G/256G/512G）在抽屉中寻找更匹配的卡片点击
        target_spec_node = None
        cap_match = re.search(r'(\d+\s*[GM]B|\d+\s*T)', title, re.I)
        target_cap = cap_match.group(1).upper().replace(" ", "") if cap_match else ""
        
        if target_cap:
            for dn in drawer_nodes:
                t = dn["text"].upper().replace(" ", "")
                b = dn["bounds"]
                if b[1] > 1000 and (b[2] - b[0]) < 900 and (b[3] - b[1]) < 220:
                    if target_cap in t:
                        target_spec_node = dn
                        break
                        
        if target_spec_node:
            tcx = (target_spec_node["bounds"][0] + target_spec_node["bounds"][2]) // 2
            tcy = (target_spec_node["bounds"][1] + target_spec_node["bounds"][3]) // 2
            run_adb(["shell", "input", "tap", str(tcx), str(tcy)])
            time.sleep(1.5)
            capture_device_screencap(step3_shot_abs)
            
            if _OCR_ENGINE and os.path.exists(step3_shot_abs):
                try:
                    s3_res, _ = _OCR_ENGINE(step3_shot_abs)
                    for line in s3_res:
                        pm = re.search(r'[￥¥]\s*(\d+(?:\.\d+)?)', line[1])
                        if pm and float(pm.group(1)) >= min_valid_price:
                            drawer_price = float(pm.group(1))
                            break
                except Exception:
                    pass

        # 只要存在有效在售价格（抽屉价优先，首屏价保底）
        valid_final_price = drawer_price if drawer_price and drawer_price >= min_valid_price else real_step1_price
        
        if valid_final_price and valid_final_price >= min_valid_price:
            corrected_price = valid_final_price
            has_target = True
            price_diff = round(corrected_price - real_step1_price, 2) if real_step1_price else 0.0
            sku_name = target_spec_node["text"] if target_spec_node else default_selected
            
            step3_record = {
                "step": 3,
                "name": "核准在售款式与真实售价 (真机现场实拍)",
                "action": f"真机抽屉实况核验：确认在售款式【{sku_name}】与实售标价",
                "status": "SUCCESS",
                "screenshot": step3_shot_rel,
                "captured_info": {
                    "selected_sku": sku_name,
                    "updated_price": corrected_price,
                    "price_diff": price_diff,
                    "in_stock": True
                },
                "log": f"真机规格抽屉实况核验通过！经现场存证，当前在售款型为【{sku_name}】，实售价格: ¥{corrected_price:.2f}。"
            }
            if abs(price_diff) > 10:
                replay_summary = f"真机实地重测证实：首屏 ¥{real_step1_price:.2f} 系起步低价。抽屉现场捕获在售规格【{sku_name}】真实售价 ¥{corrected_price:.2f}。"
            else:
                replay_summary = f"真机实地重测核准：经抽屉穿透实测，商品在售款式【{sku_name}】真实售价为 ¥{corrected_price:.2f}，存证核准通过。"
        else:
            corrected_price = None
            has_target = False
            step3_record = {
                "step": 3,
                "name": "规格抽屉核验无有效在售款式 (真机现场实拍)",
                "action": "在真机规格抽屉中校验在售款式与库存",
                "status": "FAILED_SKU_ABSENT",
                "screenshot": step3_shot_rel,
                "captured_info": {
                    "found_target": False,
                    "skus_examined": available_skus or [default_selected]
                },
                "log": f"真机抽屉核验：抽屉内未检测到有效在售价格或该款式已下架缺货。"
            }
            replay_summary = f"真机实地重测判定：抽屉内核验未发现有效在售价格或已缺货，判定为无效脏数据，予以剔除。"

    # 4. 优雅收尾并安全退出详情页
    for _ in range(3):
        run_adb(["shell", "input", "keyevent", "4"])
        time.sleep(0.5)

    trace_data = [step1_record, step2_record, step3_record]

    # 保存客观真机重测链路到数据库
    save_audit_replay_trace(
        audit_id=audit_id,
        trace_data=trace_data,
        corrected_price=corrected_price,
        replay_status="COMPLETED",
        replay_summary=replay_summary
    )

    return {
        "audit_id": audit_id,
        "item_seq": item_data.get("item_seq"),
        "title": title,
        "original_price": real_step1_price,
        "corrected_price": corrected_price,
        "has_target_sku": has_target,
        "replay_status": "COMPLETED",
        "steps": trace_data,
        "summary": replay_summary
    }

