import os
import sys
import time
import re
import json
import random
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
from PIL import Image as PILImage
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from typing import Optional, List, Dict, Any

# 控制台 UTF-8 支持
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ADB_BIN = os.path.join(PROJECT_ROOT, "bin", "adb.exe")
DATE_STR = time.strftime("%Y-%m-%d")
CAPTURES_DIR = os.path.join(PROJECT_ROOT, "storage", "captures", DATE_STR)
REPORTS_DIR = os.path.join(PROJECT_ROOT, "storage", "reports", DATE_STR)
TEMP_DIR = os.path.join(PROJECT_ROOT, "storage", "temp")
PAUSE_SIGNAL_FILE = os.path.join(TEMP_DIR, "crawler_pause.signal")
CURRENT_KEYWORD = "RTX 5070 Ti"
os.makedirs(CAPTURES_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def check_pause_signal() -> bool:
    """检测外部用户暂停信号"""
    if os.path.exists(PAUSE_SIGNAL_FILE):
        try:
            os.remove(PAUSE_SIGNAL_FILE)
        except Exception:
            pass
        return True
    return False

def get_adb_path() -> str:
    if os.path.exists(ADB_BIN):
        return ADB_BIN
    return "adb"

def run_adb(args: list, timeout: int = 12) -> subprocess.CompletedProcess:
    adb = get_adb_path()
    try:
        return subprocess.run(
            [adb] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding='utf-8',
            errors='ignore',
            cwd=PROJECT_ROOT,
            timeout=timeout
        )
    except Exception as e:
        return subprocess.CompletedProcess([adb] + args, returncode=-1, stdout="", stderr=str(e))

def simulate_human_tap(x: int, y: int, bounds: list = None, hold_ms: int = None):
    """
    模拟人类指尖触控生物指纹（防机器特征风控）：
    1. 坐标高斯正态离散分布：在控件安全内边距内自适应抖动，杜绝固定像素点击特征
    2. 肉垫贴屏按压时长：利用 swipe 微动模拟 70ms~150ms 真实手指触控接触时间
    3. 微表情决策迟滞：点击前注入 0.2s~0.45s 随机停顿，模拟人眼对焦与脑神经反应
    """
    # 模拟眼球与手指移动延迟
    time.sleep(random.uniform(0.18, 0.42))
    
    if bounds and len(bounds) == 4:
        x1, y1, x2, y2 = bounds
        w = max(10, x2 - x1)
        h = max(10, y2 - y1)
        # 以控件中心为基准，使用截断正态分布
        sigma_x = min(14.0, max(3.0, w / 12.0))
        sigma_y = min(10.0, max(3.0, h / 12.0))
        rx = int(random.gauss(x, sigma_x))
        ry = int(random.gauss(y, sigma_y))
        # 边界约束：确保仍在控件可点击区域内（留 6px 安全边距）
        rx = max(x1 + 6, min(x2 - 6, rx))
        ry = max(y1 + 6, min(y2 - 6, ry))
    else:
        rx = x + int(random.gauss(0, 6))
        ry = y + int(random.gauss(0, 6))

    # 拟人手指屏幕接触时长 (75ms ~ 145ms)
    contact_duration = hold_ms or random.randint(75, 145)
    
    # swipe 同坐标瞬时微动真实模拟 MotionEvent.ACTION_DOWN -> 保持 -> ACTION_UP
    res = run_adb(["shell", "input", "swipe", str(rx), str(ry), str(rx), str(ry), str(contact_duration)], timeout=5)
    if res.returncode != 0:
        run_adb(["shell", "input", "tap", str(rx), str(ry)], timeout=5)

def simulate_tap(x: int, y: int, bounds: list = None):
    """兼容旧接口并自动路由至拟人触控引擎"""
    simulate_human_tap(x, y, bounds=bounds)

def simulate_human_swipe(direction: str = "up", target_dist: int = 750):
    """
    模拟人类拇指滑动真实生物物理特征：
    1. 随机化起始拇指落点（模拟不同握持手势的真实落点：X: 520~720, Y: 1860~2040）
    2. 人体工学横向弧度摆动（人类拇指上滑必然伴随 -35px ~ +30px 的横向轻微偏转）
    3. 滑动距离随机波动（基准约 750px，围绕目标波动 ±60px，保持 50% 画面重叠率避免跳过）
    4. 变化的手势初速度与持续时长（320ms ~ 480ms）
    5. 滑动后视觉对焦与惯性平息等待（1.3s ~ 2.2s）
    """
    # 拇指自然落点
    start_x = random.randint(520, 710)
    start_y = random.randint(1860, 2040) if direction == "up" else random.randint(900, 1100)
    
    # 自然弧度横向偏移
    drift_x = random.randint(-35, 30)
    end_x = max(100, min(1120, start_x + drift_x))
    
    # 距离随机波动
    actual_dist = target_dist + random.randint(-60, 60)
    
    if direction == "up":
        end_y = max(350, start_y - actual_dist)
    else:
        end_y = min(2250, start_y + actual_dist)
        
    duration_ms = random.randint(330, 480)
    run_adb(["shell", "input", "swipe", str(start_x), str(start_y), str(end_x), str(end_y), str(duration_ms)], timeout=6)
    
    # 惯性平息等待
    time.sleep(random.uniform(1.3, 2.1))

def simulate_swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 380):
    """兼容旧调用接口并引入自然随机抖动"""
    rx1 = x1 + random.randint(-12, 12)
    ry1 = y1 + random.randint(-15, 15)
    rx2 = x2 + random.randint(-15, 15)
    ry2 = y2 + random.randint(-15, 15)
    dur = duration_ms + random.randint(-40, 50)
    run_adb(["shell", "input", "swipe", str(rx1), str(ry1), str(rx2), str(ry2), str(max(200, dur))])
    time.sleep(random.uniform(1.2, 1.9))

def parse_bounds(b_str: str):
    m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', b_str)
    return [int(x) for x in m.groups()] if m else None

def safe_parse_xml(xml_file: str) -> ET.ElementTree:
    """安全解析 XML 文件，若文件不存在、为空或损坏，返回最小合法树，杜绝崩溃"""
    fallback_xml = '<?xml version="1.0" encoding="utf-8"?><hierarchy rotation="0"></hierarchy>'
    if not xml_file or not os.path.exists(xml_file) or os.path.getsize(xml_file) == 0:
        return ET.ElementTree(ET.fromstring(fallback_xml))
    try:
        return ET.parse(xml_file)
    except Exception as e:
        log(f"⚠️ XML 解析异常 ({os.path.basename(xml_file)}): {e}，启用安全空占位树")
        return ET.ElementTree(ET.fromstring(fallback_xml))

def dump_ui(out_file: str = "temp_dump.xml") -> str:
    # 纯 ASCII 相对路径，并统一收拢在 storage/temp 临时缓存目录，杜绝污染根目录
    ascii_tmp = os.path.join(TEMP_DIR, "rel_window_dump.xml")
    out_path = os.path.join(TEMP_DIR, out_file)
    # 每次 dump 前必须清理旧文件，杜绝 pull 失败后读取历史脏数据！
    if os.path.exists(ascii_tmp):
        try: os.remove(ascii_tmp)
        except Exception: pass
    if os.path.exists(out_path):
        try: os.remove(out_path)
        except Exception: pass
    run_adb(["shell", "rm", "-f", "/sdcard/window_dump.xml"])

    for attempt in range(3):
        res = run_adb(["shell", "uiautomator", "dump", "/sdcard/window_dump.xml"], timeout=8)
        if res.returncode != 0 and attempt == 0:
            # 若 uiautomator 被占用或报 137，释放后台占用服务
            run_adb(["shell", "am", "force-stop", "io.appium.uiautomator2.server"])
            run_adb(["shell", "am", "force-stop", "io.appium.uiautomator2.server.test"])
            run_adb(["shell", "am", "force-stop", "com.github.uiautomator"])
            time.sleep(0.5)
            continue
        run_adb(["pull", "/sdcard/window_dump.xml", ascii_tmp], timeout=6)
        if os.path.exists(ascii_tmp) and os.path.getsize(ascii_tmp) > 300:
            if ascii_tmp != out_path:
                import shutil
                shutil.copy2(ascii_tmp, out_path)
            return out_path
        time.sleep(0.5)

    # 终极安全防线：若真机 dump 彻底失败，写入最小合法空 XML，杜绝 FileNotFoundError 崩溃！
    log(f"⚠️ [UI Dump] 真机 UI Dump 暂未就绪，写入安全占位 XML 避免主流程崩溃")
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="utf-8"?><hierarchy rotation="0"></hierarchy>')
    except Exception:
        pass
    return out_path




def get_current_window() -> tuple:
    """获取真机当前前台活跃窗口的 (package, activity)"""
    res = run_adb(["shell", "dumpsys", "window"], timeout=5)
    for line in res.stdout.splitlines():
        if "mCurrentFocus" in line:
            m = re.search(r'mCurrentFocus=Window\{[0-9a-fA-F]+\s+[^\s]+\s+([^/\s]+)/([^}\s]+)\}', line)
            if m:
                return m.group(1), m.group(2)
            m2 = re.search(r'([a-zA-Z0-9_\.]+)/([a-zA-Z0-9_\.]+)', line)
            if m2:
                return m2.group(1), m2.group(2)
    return "", ""

def dismiss_system_notifications():
    """收起/关闭顶部横幅浮动通知与状态栏下拉，防止触控误点击外部推送消息"""
    try:
        # 调用 Android IStatusBarService.collapsePanels 底层指令，强行收起所有悬浮通知
        run_adb(["shell", "service", "call", "statusbar", "2"], timeout=4)
    except Exception:
        pass

def ensure_pinduoduo_foreground() -> bool:
    """
    强力前台守护：
    确保拼多多处于前台运行，若检测到被外部应用（如微信、短信、QQ、系统弹窗等）抢占，则自动自愈脱困
    """
    pkg, act = get_current_window()
    if pkg == "com.xunmeng.pinduoduo":
        return True
        
    log(f"🚨 [前台偏离拦截] 检测到当前处于外部应用 [{pkg or '桌面/未知'}]，正在执行自动自愈脱困...")
    dismiss_system_notifications()
    
    # 优先连续按返回键尝试退出外部临时消息弹层
    for _ in range(3):
        run_adb(["shell", "input", "keyevent", "4"])
        time.sleep(0.6)
        pkg, act = get_current_window()
        if pkg == "com.xunmeng.pinduoduo":
            log("✔ 通过返回键已成功切回拼多多！")
            return True
            
    # 若返回键未生效，强制拉起拼多多主界面
    log("正在强制拉起拼多多恢复前台...")
    run_adb(["shell", "am", "start", "-n", "com.xunmeng.pinduoduo/.ui.activity.MainFrameActivity"])
    time.sleep(2.0)
    return False

def check_and_dismiss_popups(tree: ET.ElementTree) -> bool:
    """自动消除拼多多偶发的各类营销券、签到、授权与通知弹窗（确保不误伤搜索页取消）"""
    dismiss_keywords = [
        "我知道了", "放弃", "暂不", "关闭", "去使用", "下次再说",
        "残忍拒绝", "先不开启", "稍后再说", "放弃优惠", "不开启", "收下",
        "开心收下", "立即签到", "好的", "知道了", "同意并继续", "允许", "去意已决", "继续退出"
    ]
    for n in tree.iter():
        txt = (n.attrib.get('text') or n.attrib.get('content-desc') or '').strip()
        b = parse_bounds(n.attrib.get('bounds', ''))

        # 严格过滤：若文本是 '取消'，但位置处于搜索栏顶部 (y < 400, x > 800)，这是搜索取消按钮，严禁当作弹窗消除！
        if txt == "取消":
            if b and (b[1] < 400 and b[0] > 800):
                continue
            if b and (b[1] >= 750 and b[3] <= 2100):
                cx = (b[0] + b[2]) // 2
                cy = (b[1] + b[3]) // 2
                log(f"检测到对话框取消文本 [{txt}]，自动点击消除: ({cx}, {cy})")
                simulate_tap(cx, cy)
                time.sleep(0.6)
                return True
            continue

        if txt in dismiss_keywords or txt in ['x', 'X', '×', '✕', '关闭']:
            if b:
                cx = (b[0] + b[2]) // 2
                cy = (b[1] + b[3]) // 2
                log(f"检测到干扰弹窗文本 [{txt}]，自动点击消除: ({cx}, {cy})")
                simulate_tap(cx, cy)
                time.sleep(0.6)
                return True
                
        r_id = (n.attrib.get('resource-id') or '').lower()
        if any(k in r_id for k in ['btn_close', 'iv_close', 'close_btn', 'dialog_close']):
            if b:
                cx = (b[0] + b[2]) // 2
                cy = (b[1] + b[3]) // 2
                log(f"检测到弹窗关闭按钮 ({r_id})，自动点击消除: ({cx}, {cy})")
                simulate_tap(cx, cy)
                time.sleep(0.6)
                return True
    return False

def detect_current_page(xml_source) -> str:
    """
    全域状态机判定中枢：
    确定性识别当前处于哪个真实页面：
    - PAGE_SEARCH_RESULTS: 搜索结果商品列表页
    - PAGE_PRODUCT_DETAIL: 商品详情页
    - PAGE_SKU_DRAWER: 规格抽屉面板
    - PAGE_SEARCH_INPUT: 搜索关键词输入/历史页
    - PAGE_HOME: 拼多多首页/推荐流
    - PAGE_POPUP: 拦截性弹窗
    - PAGE_UNKNOWN: 未知/过渡态
    """
    try:
        tree = ET.parse(xml_source) if isinstance(xml_source, str) else xml_source
    except Exception:
        return "PAGE_UNKNOWN"

    texts, ids, classes, edit_texts, nodes_info = [], [], [], [], []
    for n in tree.iter():
        t = (n.attrib.get('text') or n.attrib.get('content-desc') or '').strip()
        rid = (n.attrib.get('resource-id') or '').strip()
        cls = (n.attrib.get('class') or '').strip()
        bounds = parse_bounds(n.attrib.get('bounds', ''))
        if t: texts.append(t)
        if rid: ids.append(rid)
        if cls: classes.append(cls)
        if cls == 'android.widget.EditText':
            edit_texts.append({'text': t, 'bounds': bounds, 'id': rid})
        nodes_info.append({'text': t, 'id': rid, 'class': cls, 'bounds': bounds})

    all_text_str = " ".join(texts)

    # 1. 规格抽屉判定
    is_sku = any(k in all_text_str for k in ['已选:', '已选：', '选择规格', '型号', '属性']) and any(k in texts for k in ['关闭', '确定', '增加数量', '减少数量', '加入拼单'])
    if is_sku:
        return "PAGE_SKU_DRAWER"

    # 2. 商品详情页判定
    has_detail_bottom_action = any(k in all_text_str for k in ["发起拼单", "单独购买", "立即购买", "加入购物车"])
    has_detail_nav = any(k in all_text_str for k in ["客服", "店铺", "收藏"])
    has_detail_store = any(k in all_text_str for k in ["来自", "官方旗舰店", "正品", "拼单即将结束", "直接拼成"])
    if has_detail_bottom_action and (has_detail_nav or has_detail_store):
        return "PAGE_PRODUCT_DETAIL"

    # 3. 搜索输入页判定
    has_search_cancel_or_btn = any(n['text'] in ['搜索', '取消'] and n['bounds'] and n['bounds'][1] < 400 for n in nodes_info)
    has_search_history_hints = any(k in all_text_str for k in ["历史搜索", "搜索历史", "大家都在搜", "搜索发现", "最近搜索"])
    if len(edit_texts) > 0 and (has_search_cancel_or_btn or has_search_history_hints):
        return "PAGE_SEARCH_INPUT"

    # 4. 首页推荐流与底部大 Tab 判定 (必须在搜索结果前判定，因为首页推荐流也包含商品卡片和价格)
    # 检测底部大导航 Tab（首页、多多视频、分类、聊天、个人中心）通常位于底部 y > 1800
    has_bottom_nav = any(n['text'] in ["首页", "多多视频", "分类", "聊天", "个人中心"] and n['bounds'] and n['bounds'][1] > 1800 for n in nodes_info)
    has_home_blocks = any(k in texts for k in ["限时秒杀", "百亿补贴", "多多买菜", "大促精选", "充值中心", "免费领水果"])
    if has_bottom_nav or has_home_blocks:
        return "PAGE_HOME"

    # 5. 搜索结果列表页判定 (必须具备搜索排序栏，且绝不能带有首页底部导航)
    has_sort_tabs = any(k in texts for k in ["综合", "销量", "价格", "筛选"])
    has_goods_titles = any('tv_title' in r for r in ids)
    has_goods_prices = (all_text_str.count("¥") >= 2 and any(k in all_text_str for k in ["已拼", "人付款", "评价", "券后", "旗舰店"]))

    if (has_sort_tabs and (has_goods_titles or has_goods_prices)) or (has_sort_tabs and not has_bottom_nav):
        return "PAGE_SEARCH_RESULTS"

    # 6. 弹窗判定
    popup_words = ["残忍离开", "放弃", "暂不", "去意已决", "继续浏览", "我知道了", "允许", "升级", "跳过"]
    if any(k in texts for k in popup_words):
        return "PAGE_POPUP"

    return "PAGE_UNKNOWN"

def execute_search_query(target_keyword: str = "gtx 5070ti") -> bool:
    """
    智能搜索执行器：
    1. 优先点击搜索历史中的目标标签（0 键盘冲突，100% 稳定性）；
    2. 动态定位 EditText，精准清空已有内容，杜绝文本叠加污染；
    3. 模拟输入目标关键词并通过原生按键/点击搜索完成提交。
    """
    log(f"🔎 [搜索执行器] 准备在搜索输入界面检索: {target_keyword}...")
    dump_path = dump_ui("search_input_check.xml")
    tree = ET.parse(dump_path)
    
    # 策略 1: 查找搜索历史标签中是否有高度吻合的目标关键词
    target_lower = target_keyword.lower().strip()
    for n in tree.iter():
        txt = (n.attrib.get('text') or '').strip().lower()
        if txt and (txt == target_lower or (len(target_lower) >= 6 and target_lower in txt)):
            b = parse_bounds(n.attrib.get('bounds', ''))
            if b and b[1] > 300 and b[3] < 2200:
                cx = (b[0] + b[2]) // 2
                cy = (b[1] + b[3]) // 2
                log(f"🎯 [历史标签直达] 发现匹配历史搜索标签 [{txt}]，直接点击复用: ({cx}, {cy})")
                simulate_tap(cx, cy)
                time.sleep(2.0)
                return True
                
    # 策略 2: 动态定位真实的 EditText
    edit_node = None
    for n in tree.iter():
        cls = n.attrib.get('class', '')
        if 'EditText' in cls or 'search' in n.attrib.get('resource-id', '').lower():
            edit_node = n
            break
            
    if edit_node:
        eb = parse_bounds(edit_node.attrib.get('bounds', ''))
        curr_text = (edit_node.attrib.get('text') or '').strip()
        ecx = (eb[0] + eb[2]) // 2
        ecy = (eb[1] + eb[3]) // 2
        
        # 若已有完全一致的目标文本，直接触发搜索
        if target_keyword.lower() in curr_text.lower():
            log(f"✔ 输入框已有目标文本 [{curr_text}]，直接触发提交搜索...")
        else:
            log(f"🧹 清理输入框原有内容 (原内容: '{curr_text}') 并输入目标词...")
            simulate_tap(ecx, ecy)
            time.sleep(0.3)
            # 通过底层键码稳妥清空：先移动到末尾，再连续发送 40 个退格键
            run_adb(["shell", "input", "keyevent", "123"])
            run_adb(["shell", "for i in $(seq 1 40); do input keyevent 67; done"])
            time.sleep(0.3)
            
            # 输入目标文本 (空格转义为 %s)
            escaped_kw = target_keyword.replace(" ", "%s")
            run_adb(["shell", "input", "text", escaped_kw])
            time.sleep(0.5)
    else:
        # Fallback 坐标保底
        simulate_tap(500, 190)
        time.sleep(0.3)
        run_adb(["shell", "input", "keyevent", "123"])
        run_adb(["shell", "for i in $(seq 1 40); do input keyevent 67; done"])
        time.sleep(0.3)
        run_adb(["shell", "input", "text", "gtx%s5070ti"])
        time.sleep(0.5)
        
    # 提交搜索: 优先寻找文本为 '搜索' 的按钮，并配合原生回车/搜索键码
    new_tree = ET.parse(dump_ui("search_submit_check.xml"))
    search_btn_tapped = False
    for n in new_tree.iter():
        txt = (n.attrib.get('text') or '').strip()
        b = parse_bounds(n.attrib.get('bounds', ''))
        if txt == "搜索" and b and b[1] < 400:
            cx = (b[0] + b[2]) // 2
            cy = (b[1] + b[3]) // 2
            log(f"🔘 点击「搜索」按钮: ({cx}, {cy})")
            simulate_tap(cx, cy)
            search_btn_tapped = True
            time.sleep(1.8)
            break
            
    if not search_btn_tapped:
        # 发送软键盘搜索动作 (Enter & Search)
        run_adb(["shell", "input", "keyevent", "66"])
        time.sleep(0.3)
        run_adb(["shell", "input", "keyevent", "84"])
        time.sleep(1.8)
        
    log("已触发搜索提交，等待结果页加载...")
    return True

def apply_search_sort(sort_by: str = "default") -> bool:
    """
    智能切换拼多多搜索结果排序方式：
    - default: 综合推荐（默认）
    - sales: 销量优先
    - price_asc: 价格优先
    """
    if sort_by == "default":
        log("📊 [排序规则] 保持默认【综合推荐】排序")
        return True

    sort_map = {
        "sales": "销量",
        "price_asc": "价格"
    }
    target_text = sort_map.get(sort_by, "销量")
    log(f"📊 [排序切换] 正在检测并切换至【{target_text}优先】...")

    for attempt in range(3):
        dump_path = dump_ui("sort_tab_check.xml")
        try:
            tree = ET.parse(dump_path)
            for n in tree.iter():
                txt = (n.attrib.get('text') or '').strip()
                if txt == target_text:
                    b = parse_bounds(n.attrib.get('bounds', ''))
                    # 确保是顶栏排序 Tab 区域 (y 坐标通常在 200~450 之间)
                    if b and 200 <= b[1] <= 450:
                        cx = (b[0] + b[2]) // 2
                        cy = (b[1] + b[3]) // 2
                        log(f"🔘 点击切换排序标签 [{txt}]: ({cx}, {cy})")
                        simulate_tap(cx, cy)
                        time.sleep(2.0)
                        return True
        except Exception as e:
            log(f"切换排序检测异常: {e}")
        time.sleep(0.8)

    log(f"⚠️ 未能自动定位到【{target_text}】排序标签，保持当前视图继续采数")
    return False

def ensure_in_search_results(keyword: str = "RTX 5070 Ti") -> bool:
    """
    智能搜索结果页就绪保证引擎：
    1. 优先检测当前手机界面是否已处于目标关键词的搜索结果列表；
    2. 若否，直接通过拼多多原生 URI Scheme 秒级唤起目标关键词结果页（0 键盘冲突、0 盲触）；
    3. 自动消除弹窗，精准校验结果页就绪状态；
    4. 若原生路由未命中，启用全域状态机脱困降级策略。
    """
    log(f"检查当前手机界面状态 (目标品类: {keyword})...")
    ensure_pinduoduo_foreground()
    dismiss_system_notifications()

    # 第一优先级：检查当前是否已经在目标关键词搜索结果页
    dump_path = dump_ui("page_state_initial.xml")
    tree = safe_parse_xml(dump_path)
    check_and_dismiss_popups(tree)
    page_type = detect_current_page(tree)
    
    # 提取屏幕上所有可见文字
    all_texts = [n.attrib.get('text', '') or n.attrib.get('content-desc', '') for n in tree.iter()]
    all_text_str = " ".join(all_texts).lower()
    kw_clean = re.sub(r'[^\w\s\u4e00-\u9fa5]', ' ', keyword).strip()
    kw_tokens = [tok.lower() for tok in kw_clean.split() if len(tok) >= 2]
    weak_words = {'全新', '官方', '正品', '国行', '旗舰', '包邮', '现货', '手机', '显卡', '电脑'}
    strong_tokens = [t for t in kw_tokens if t not in weak_words]
    eval_tokens = strong_tokens if strong_tokens else kw_tokens
    
    is_keyword_matched = any(tok in all_text_str for tok in eval_tokens) if eval_tokens else True

    if page_type == "PAGE_SEARCH_RESULTS" and is_keyword_matched:
        log(f"✔ 确认当前已停留在 [{keyword}] 搜索结果列表中，直接继续！")
        return True

    # 第二优先级：利用拼多多官方 URI Scheme 直达指定关键词搜索结果页
    log(f"🚀 [URI Scheme 直达] 正在调用拼多多原生路由直达 [{keyword}] 搜索结果页...")
    encoded_kw = urllib.parse.quote(keyword)
    scheme_url = f"pinduoduo://com.xunmeng.pinduoduo/search_result.html?search_key={encoded_kw}"
    run_adb(["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", scheme_url])
    time.sleep(2.5)

    # 检查直达后的界面
    dump_path = dump_ui("page_state_scheme.xml")
    tree = safe_parse_xml(dump_path)
    check_and_dismiss_popups(tree)
    page_type = detect_current_page(tree)

    if page_type == "PAGE_SEARCH_RESULTS":
        log(f"✔ [URI Scheme 直达成功] 已成功进入 [{keyword}] 搜索结果列表！")
        return True

    # 第三优先级：多轮状态机容错自愈
    log(f"⚠️ 原生路由后界面判定为 [{page_type}]，进入状态机自愈校验...")
    for attempt in range(4):
        dump_path = dump_ui(f"page_state_{attempt}.xml")
        tree = safe_parse_xml(dump_path)
        check_and_dismiss_popups(tree)
        page_type = detect_current_page(tree)
        log(f"🔍 [状态机诊断] 当前界面判定为: {page_type} (自愈轮次: {attempt + 1}/4)")

        if page_type == "PAGE_SEARCH_RESULTS":
            log(f"✔ 确认已恢复至 [{keyword}] 搜索结果列表！")
            return True

        elif page_type == "PAGE_SKU_DRAWER":
            log("检测到滞留在规格抽屉面板，正在收起抽屉...")
            run_adb(["shell", "input", "keyevent", "4"])
            time.sleep(0.8)

        elif page_type == "PAGE_PRODUCT_DETAIL":
            log("检测到滞留在商品详情页，发送返回键退回搜索列表...")
            run_adb(["shell", "input", "keyevent", "4"])
            time.sleep(0.8)

        elif page_type == "PAGE_SEARCH_INPUT":
            log(f"检测到处于搜索输入/历史页，执行智能搜索提交: {keyword}...")
            execute_search_query(keyword)
            time.sleep(1.5)

        elif page_type == "PAGE_HOME":
            log("检测到处于拼多多首页，通过原生路由重新直达...")
            run_adb(["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", scheme_url])
            time.sleep(2.0)

        else: # PAGE_UNKNOWN
            log("当前页面处于未知或过渡状态，尝试返回键脱困...")
            run_adb(["shell", "input", "keyevent", "4"])
            time.sleep(0.8)

    final_tree = safe_parse_xml(dump_ui("final_state.xml"))
    if detect_current_page(final_tree) == "PAGE_SEARCH_RESULTS":
        log("✔ 最终校验就绪：成功恢复至搜索结果列表！")
        return True

    log(f"⚠️ 兜底唤起 [{keyword}] 搜索结果页...")
    run_adb(["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", scheme_url])
    time.sleep(2.0)
    return True

def get_visible_cards(xml_file: str, keyword: str = "RTX 5070 Ti") -> list:
    tree = safe_parse_xml(xml_file)
    check_and_dismiss_popups(tree)
    all_nodes = list(tree.iter())
    title_nodes = []
    
    for n in all_nodes:
        r_id = n.attrib.get('resource-id', '')
        if 'tv_title' in r_id:
            title = n.attrib.get('content-desc') or n.attrib.get('text')
            bounds = parse_bounds(n.attrib.get('bounds', ''))
            # 过滤在可视卡片区域之外的元素
            if title and bounds and bounds[1] > 360 and bounds[3] < 2500:
                title_nodes.append({
                    "title": title.strip(),
                    "bounds": bounds,
                    "node": n
                })
    
    is_5070 = "5070" in keyword.lower()

    cards = []
    for t in title_nodes:
        b = t["bounds"]
        col_min_x = b[0] - 60
        col_max_x = b[2] + 60
        y_min = b[3]
        y_max = b[3] + 280
        
        tags = []
        price = ""
        
        for n in all_nodes:
            nb = parse_bounds(n.attrib.get('bounds', ''))
            if not nb:
                continue
            # 在同列标题下方区域寻找价格与优惠标签
            if nb[0] >= col_min_x and nb[2] <= col_max_x and nb[1] >= y_min - 20 and nb[3] <= y_max:
                txt = (n.attrib.get('text') or n.attrib.get('content-desc') or '').strip()
                if not txt or txt in ('¥', '广告'):
                    continue
                
                # 价格解析
                if re.match(r'^\d+(\.\d+)?$', txt) and int(float(txt)) > 0:
                    if not price:
                        price = f"¥{txt}"
                elif '券后' in txt:
                    pass
                elif any(k in txt for k in ['立减', '件', '分期', '包邮', '想拼', '好评', '已抢', '恢复原价', '满', '折', '补贴', '特惠']):
                    if txt not in tags:
                        tags.append(txt)
        
        # 严格过滤与目标品类无关的卡片 (防推荐流杂质、防广告偏离)
        is_valid = True
        title_lower = t["title"].lower()
        if is_5070:
            is_valid = bool(re.search(r'(16\s*g\b|16\s*gb\b|16g显存|16gb显存|16\s*g)', t["title"], re.I))
        elif keyword:
            kw_clean = re.sub(r'[^\w\s\u4e00-\u9fa5]', ' ', keyword).strip()
            kw_tokens = [tok.lower() for tok in kw_clean.split() if len(tok) >= 2]
            weak_words = {'全新', '官方', '正品', '国行', '旗舰', '包邮', '现货', '手机', '显卡', '电脑'}
            strong_tokens = [tok for tok in kw_tokens if tok not in weak_words]
            eval_tokens = strong_tokens if strong_tokens else kw_tokens
            if eval_tokens:
                # 要求标题中必须包含至少一个强特征核心词 (如 16, 4060, iphone 等)
                has_match = any(tok in title_lower for tok in eval_tokens)
                if not has_match:
                    is_valid = False

        cards.append({
            "title": t["title"],
            "bounds": b,
            "is_16g": is_valid,
            "is_valid": is_valid,
            "price": price or "¥待获取",
            "tags": " | ".join(tags) if tags else "限时特惠"
        })
    return cards

def select_16g_sku_in_detail(xml_file: str) -> dict:
    """
    穿透到商品详情页底部的规格选择抽屉，精确寻找并点击 16G 规格 Tag，
    锁定真实 16G 单价，杜绝外显低配起步价引流
    """
    sku_info = {
        "sku_verified": False,
        "selected_spec": "",
        "spec_price": None,
        "is_sold_out": False
    }
    
    try:
        tree = safe_parse_xml(xml_file)
        spec_entry_bounds = None
        for n in tree.iter():
            txt = (n.attrib.get('text') or n.attrib.get('content-desc') or '').strip()
            if any(k in txt for k in ["选择规格", "选择", "已选"]):
                b = parse_bounds(n.attrib.get('bounds', ''))
                if b and 400 < b[1] < 2200:
                    spec_entry_bounds = b
                    break
                    
        if spec_entry_bounds:
            cx = (spec_entry_bounds[0] + spec_entry_bounds[2]) // 2
            cy = (spec_entry_bounds[1] + spec_entry_bounds[3]) // 2
            log(f"   🔍 [规格穿透] 发现规格入口，拟人点击唤起 SKU 抽屉 ({cx}, {cy})...")
            simulate_tap(cx, cy)
            time.sleep(0.9)
            
            # Dump 规格抽屉面板
            drawer_xml = dump_ui("sku_drawer.xml")
            d_tree = safe_parse_xml(drawer_xml)
            
            target_tag_bounds = None
            tag_name = ""
            for node in d_tree.iter():
                t = (node.attrib.get('text') or node.attrib.get('content-desc') or '').strip()
                if re.search(r'(?:16\s*g\b|16\s*gb\b|16g显存)', t, re.I):
                    b = parse_bounds(node.attrib.get('bounds', ''))
                    # 仅寻找规格选项栏 (通常位于抽屉中下部)
                    if b and b[1] > 700 and (b[2] - b[0]) < 650:
                        target_tag_bounds = b
                        tag_name = t
                        if "售罄" in t or "缺货" in t or node.attrib.get("clickable") == "false":
                            sku_info["is_sold_out"] = True
                        break
                        
            if target_tag_bounds and not sku_info["is_sold_out"]:
                tcx = (target_tag_bounds[0] + target_tag_bounds[2]) // 2
                tcy = (target_tag_bounds[1] + target_tag_bounds[3]) // 2
                log(f"   🎯 [规格定位] 锁定 16G 规格选项「{tag_name}」，拟人点击锁定真实价格...")
                simulate_tap(tcx, tcy)
                time.sleep(0.6)
                
                # 读取抽屉顶部联动刷新的真实价格
                updated_drawer = dump_ui("sku_drawer_selected.xml")
                u_tree = safe_parse_xml(updated_drawer)
                for node in u_tree.iter():
                    t = (node.attrib.get('text') or node.attrib.get('content-desc') or '').strip()
                    if re.match(r'^\d+(\.\d+)?$', t) and float(t) > 3000:
                        sku_info["spec_price"] = f"¥{t}"
                        sku_info["sku_verified"] = True
                        sku_info["selected_spec"] = tag_name
                        log(f"   ✨ [真实单价锁定] 成功穿透获取 16G 真实价格: ¥{t}")
                        break
            elif sku_info["is_sold_out"]:
                log(f"   ⚠️ [规格售罄] 16G 规格选项已置灰或售罄！")
                
            # 关闭规格抽屉，返回商品详情页主界面
            run_adb(["shell", "input", "keyevent", "4"])
            time.sleep(0.5)
    except Exception as e:
        log(f"规格穿透逻辑忽略性异常: {e}")
        try:
            run_adb(["shell", "input", "keyevent", "4"])
            time.sleep(0.4)
        except Exception:
            pass
            
    return sku_info

def extract_shop_from_detail(xml_file: str) -> str:
    """从商品详情页中精准提取所属店铺名称"""
    try:
        tree = safe_parse_xml(xml_file)
        jin_bounds = None
        for n in tree.iter():
            if n.attrib.get('text') == '进店':
                jin_bounds = parse_bounds(n.attrib.get('bounds', ''))
                break
        if jin_bounds:
            for n in tree.iter():
                b = parse_bounds(n.attrib.get('bounds', ''))
                if b and abs(b[1] - jin_bounds[1]) < 90 and b[0] < 650:
                    txt = (n.attrib.get('text') or '').strip()
                    if txt and txt not in ('进店', '店铺', '三包承诺', '举报'):
                        return txt
        for n in tree.iter():
            txt = (n.attrib.get('text') or '').strip()
            if re.search(r'(旗舰店|专营店|专卖店|数码馆|电竞馆|数码店|电脑店|电脑配件)', txt):
                return txt
    except Exception as e:
        log(f"解析店铺异常: {e}")
    return "品牌专营店铺"

def capture_product_screenshot(raw_path: str, thumb_path: str):
    """截取当前商品详情页并生成紧凑缩略图用于嵌入Excel"""
    ascii_shot = os.path.join(TEMP_DIR, "rel_screen_shot.png")
    run_adb(["shell", "screencap", "-p", "/sdcard/pdd_current_shot.png"], timeout=8)
    run_adb(["pull", "/sdcard/pdd_current_shot.png", ascii_shot], timeout=8)
    if os.path.exists(ascii_shot):
        try:
            import shutil
            shutil.copy2(ascii_shot, raw_path)
            with PILImage.open(ascii_shot) as img:
                img.thumbnail((120, 120))
                img.save(thumb_path, "PNG")
        except Exception as e:
            log(f"保存或缩略图片异常: {e}")


def extract_goods_id_from_device() -> str:
    """从 Android 前台 Activity/Fragment 运行时栈中高速提取当前前台活跃商品的真实 goods_id (0.12s 极速直取，严格隔离历史栈)"""
    res = run_adb(["shell", "dumpsys", "activity", "com.xunmeng.pinduoduo"], timeout=5)
    if res.returncode == 0 and res.stdout:
        blocks = res.stdout.split("Local FragmentActivity ")
        for block in reversed(blocks):
            # 严格约束：仅当当前前台活跃窗口为处于 Resumed 状态的商品详情页时才提取
            if "mResumed=true" in block and "mStopped=false" in block:
                if "ProductDetailFragment" in block:
                    m = re.findall(r'goods_id=(\d+)', block)
                    if m:
                        return m[0]
                # 若当前顶层不是详情页，绝不复用后台历史遗留 ID
                return ""
    return ""


def get_product_link(item_title: str) -> str:
    """
    高速提取移动端真实商品直达链接与唯一 goods_id (零界面干扰直取)
    """
    # 优先从 Android Activity 栈毫秒级提取真实 goods_id (无需弹分享抽屉，零延时零干扰)
    gid = extract_goods_id_from_device()
    if gid:
        return f"https://mobile.yangkeduo.com/goods.html?goods_id={gid}"
        
    encoded_title = urllib.parse.quote(item_title[:28])
    return f"https://mobile.yangkeduo.com/goods.html?goods_name={encoded_title}"

def return_to_search_results(keyword: str = "") -> bool:
    """
    高可靠状态机返回导航：从详情页安全回退至搜索结果列表
    结合明确的页面分类与左上角返回按钮识别，绝不误触其他区域。
    """
    ensure_pinduoduo_foreground()
    dismiss_system_notifications()

    for attempt in range(4):
        dump_path = dump_ui(f"return_state_{attempt}.xml")
        try:
            tree = safe_parse_xml(dump_path)
            page_type = detect_current_page(tree)
            
            if page_type == "PAGE_SEARCH_RESULTS":
                return True
                
            if page_type == "PAGE_SKU_DRAWER":
                # 收起抽屉
                run_adb(["shell", "input", "keyevent", "4"])
                time.sleep(0.6)
                continue
                
            if page_type == "PAGE_PRODUCT_DETAIL":
                # 直接发送 Android 原生 BACK 键退回搜索列表
                run_adb(["shell", "input", "keyevent", "4"])
                time.sleep(0.8)
                continue
                
            check_and_dismiss_popups(tree)
            run_adb(["shell", "input", "keyevent", "4"])
            time.sleep(0.8)
        except Exception:
            run_adb(["shell", "input", "keyevent", "4"])
            time.sleep(0.8)

    # 校验最终页面
    final_tree = safe_parse_xml(dump_ui("return_final.xml"))
    if detect_current_page(final_tree) != "PAGE_SEARCH_RESULTS":
        log("⚠️ 返回导航未直接到达搜索列表，执行自愈中枢修复...")
        ensure_in_search_results(keyword=keyword or CURRENT_KEYWORD)
    return True


def generate_excel_report(items: list, output_xlsx: str, sheet_title: Optional[str] = None):
    """
    构建符合企业级规范的 Excel 报告，并将现场截图直接嵌入单元格中
    """
    wb = Workbook()
    ws = wb.active
    ws.title = (sheet_title or "商品监控数据")[:30]

    headers = ["序号", "现场截图", "商品名称", "券后/商品价格", "优惠详情", "所属店铺", "商品链接"]
    ws.append(headers)

    # 样式配置：专业科技蓝表头
    header_fill = PatternFill(start_color="1E88E5", end_color="1E88E5", fill_type="solid")
    header_font = Font(name="Microsoft YaHei", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="Microsoft YaHei", size=10)
    price_font = Font(name="Microsoft YaHei", size=11, bold=True, color="D32F2F") # 红色高亮价格
    
    thin_border = Border(
        left=Side(style='thin', color='D0D0D0'),
        right=Side(style='thin', color='D0D0D0'),
        top=Side(style='thin', color='D0D0D0'),
        bottom=Side(style='thin', color='D0D0D0')
    )

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
    ws.row_dimensions[1].height = 32

    # 设置列宽
    col_widths = {
        "A": 8,   # 序号
        "B": 18,  # 现场截图
        "C": 45,  # 商品名称
        "D": 16,  # 价格
        "E": 28,  # 优惠详情
        "F": 24,  # 所属店铺
        "G": 42   # 商品链接
    }
    for col, w in col_widths.items():
        ws.column_dimensions[col].width = w

    for idx, item in enumerate(items, start=1):
        row_idx = idx + 1
        ws.row_dimensions[row_idx].height = 96
        
        row_data = [
            idx,
            "", # 截图占位
            item["title"],
            item["price"],
            item["tags"],
            item["shop"],
            item["link"]
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
            except Exception as e:
                log(f"嵌入图片异常: {e}")

    wb.save(output_xlsx)
    log(f"Excel 报告已成功输出并保存至: {output_xlsx}")

CHECKPOINT_FILE = os.path.join(REPORTS_DIR, "pdd_crawler_checkpoint.json")

def load_checkpoint() -> list:
    """
    检查并读取当前任务的历史断点存档数据：
    优先读取 pdd_crawler_checkpoint.json，次选当日已有的 pdd_gtx5070ti_16g.json
    若标记为 RESET 则视为空，严禁恢复历史已完结数据
    """
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    if data.get("status") == "RESET":
                        log("ℹ️ 检查到断点存档标记为【RESET(已清空)】，将重新从第 1 项开始全新采集。")
                        return []
                    return data.get("items", [])
                elif isinstance(data, list):
                    return data
        except Exception as e:
            log(f"读取断点文件 {CHECKPOINT_FILE} 异常: {e}")

    fallback_json = os.path.join(REPORTS_DIR, "pdd_gtx5070ti_16g.json")
    if os.path.exists(fallback_json):
        try:
            with open(fallback_json, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    if data.get("status") == "RESET":
                        return []
                    return data.get("items", [])
                elif isinstance(data, list):
                    return data
        except Exception as e:
            log(f"读取备用断点文件 {fallback_json} 异常: {e}")

    return []

def save_checkpoint(items: list, target_count: int, status: str = "IN_PROGRESS"):
    """实时持久化断点状态，确保任一时刻中断均可从上一条安全续接"""
    payload = {
        "task_name": "pdd_gtx5070ti_16g",
        "date": DATE_STR,
        "target_count": target_count,
        "current_count": len(items),
        "status": status,
        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "items": items
    }
    try:
        with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"保存断点存档异常: {e}")

def reset_checkpoint():
    """清空断点存档以支持全新从头采集"""
    import shutil
    json_file = os.path.join(REPORTS_DIR, "pdd_gtx5070ti_16g.json")
    if os.path.exists(json_file):
        try:
            ts = time.strftime("%H%M%S")
            archive_path = os.path.join(REPORTS_DIR, f"pdd_gtx5070ti_16g_archived_{ts}.json")
            shutil.move(json_file, archive_path)
            log(f"📦 已将历史采集数据安全归档至: {os.path.basename(archive_path)}")
        except Exception as e:
            log(f"归档旧数据异常: {e}")

    reset_payload = {
        "task_name": "pdd_gtx5070ti_16g",
        "date": DATE_STR,
        "target_count": 100,
        "current_count": 0,
        "status": "RESET",
        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "items": []
    }
    try:
        with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
            json.dump(reset_payload, f, ensure_ascii=False, indent=2)
        log("🧹 已清空历史断点存档并置为全新状态 (0/100)，将从第 1 个商品开始采集。")
    except Exception as e:
        log(f"清空断点存档异常: {e}")

def run_crawler(target_count: int = 100, resume: bool = True, keyword: str = "RTX 5070 Ti", sort_by: str = "default") -> list:
    global CURRENT_KEYWORD
    CURRENT_KEYWORD = keyword
    start_time = time.time()
    sort_desc = "综合推荐" if sort_by == "default" else ("销量优先" if sort_by == "sales" else "价格优先")
    log(f"==================================================")
    log(f"🚀 开始执行拼多多商品自动化采集任务 (高防风控拟人增强版)")
    log(f"🔍 采集品类 / 关键词: 【{keyword}】")
    log(f"🎯 目标采集数量: {target_count} 个有效商品")
    log(f"📊 结果排序规则: 【{sort_desc}】")
    log(f"🛡️ 防风控策略: 拟人触控离散化 + 进店随机驻留 + 微滑交互 + 疲劳思考缓冲")
    log(f"🔄 断点继跑模式: {'【开启】(遇到历史记录自动续接)' if resume else '【关闭】(全新从头采集)'}")
    log(f"==================================================")

    # 启动前清理任何可能残留的暂停控制信号，确保本次任务正常执行
    if os.path.exists(PAUSE_SIGNAL_FILE):
        try:
            os.remove(PAUSE_SIGNAL_FILE)
        except Exception:
            pass

    # 1. 确保停留在目标品类的搜索结果页并切换至指定排序
    ensure_in_search_results(keyword=keyword)
    if sort_by != "default":
        apply_search_sort(sort_by=sort_by)

    collected_items = []
    collected_titles = set()

    is_5070 = "5070" in keyword.lower()
    safe_kw = re.sub(r'[\s/\\:\*\?"<>\|]+', '_', keyword).strip('_') or "goods"
    report_file = os.path.join(REPORTS_DIR, f"pdd_{safe_kw}.xlsx")
    latest_file = os.path.join(PROJECT_ROOT, "storage", "reports", f"pdd_{safe_kw}_latest.xlsx")
    json_file = os.path.join(REPORTS_DIR, f"pdd_{safe_kw}.json")

    # 向下兼容 5070 老路径
    default_5070_file = os.path.join(REPORTS_DIR, "pdd_gtx5070ti_16g.xlsx")
    default_5070_latest = os.path.join(PROJECT_ROOT, "storage", "reports", "pdd_gtx5070ti_16g_latest.xlsx")
    default_5070_json = os.path.join(REPORTS_DIR, "pdd_gtx5070ti_16g.json")

    # 2. 断点继跑检查与加载
    if resume:
        cached_items = load_checkpoint()
        if cached_items:
            for it in cached_items:
                t = it.get("title", "").strip()
                if t and t not in collected_titles:
                    collected_items.append(it)
                    collected_titles.add(t)
            
            log(f"🔄 【断点继跑激活】成功恢复断点存档: 已完成 {len(collected_items)}/{target_count} 项！")
            log(f"⏩ 自动跳过这 {len(collected_items)} 项已有记录，搜索流将直接向下寻访第 [{len(collected_items) + 1}/{target_count}] 项！")
            
            if len(collected_items) >= target_count:
                log(f"🎉 目标数量已达成 (已采集 {len(collected_items)} >= {target_count})，无需继续翻页！")
                generate_excel_report(collected_items, report_file, sheet_title=f"{keyword}商品数据")
                return collected_items
    else:
        log("🆕 【全新采集模式】忽略历史断点缓存，重新从第 1 项开始采集。")
        reset_checkpoint()

    no_new_item_count = 0
    max_swipes = max(450, target_count * 5)
    last_fatigue_checkpoint = len(collected_items)
    next_fatigue_interval = random.randint(8, 12)

    def _persist_pause_state():
        log(f"⏸️ 【用户主动暂停】检测到外部暂停控制信号，正在封存当前断点 ({len(collected_items)}/{target_count}) 并安全退出...")
        save_checkpoint(collected_items, target_count, status="PAUSED")
        generate_excel_report(collected_items, report_file)
        try:
            import shutil
            shutil.copy2(report_file, latest_file)
        except Exception:
            pass
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(collected_items, f, ensure_ascii=False, indent=2)
        log(f"💾 数据与断点已完全保存。手机现在可安全断开连接，下次启动时开启「断点续跑」即可从第 {len(collected_items) + 1} 项继续。")

    while len(collected_items) < target_count and no_new_item_count < 25 and max_swipes > 0:
        if check_pause_signal():
            _persist_pause_state()
            return collected_items

        max_swipes -= 1
        dump_path = dump_ui("search_page.xml")
        visible_cards = get_visible_cards(dump_path, keyword=keyword)
        
        found_in_this_view = 0
        for card in visible_cards:
            if check_pause_signal():
                _persist_pause_state()
                return collected_items

            title = card["title"]
            if title in collected_titles:
                continue
            
            # 规格与有效性校验 (5070 校验 16G，其他品类放行)
            if not card.get("is_valid", True):
                log(f"⏩ [规格过滤] 跳过商品: {title[:28]}...")
                continue
            
            found_in_this_view += 1
            current_num = len(collected_items) + 1
            log(f"🎯 [命中目标] 正在采集第 [{current_num}/{target_count}] 个商品...")
            log(f"   标题: {title[:35]}...")
            log(f"   标价: {card['price']} | 优惠: {card['tags']}")
            
            # 1. 点击前收起任何可能出现的浮动通知与横幅，确保前台处于拼多多
            dismiss_system_notifications()
            ensure_pinduoduo_foreground()

            # 点击卡片进入详情页 (引入边界高斯离散与拟人按压接触时长)
            b = card["bounds"]
            tap_x = (b[0] + b[2]) // 2
            tap_y = b[1] - 140 if b[1] > 300 else b[1] + 30
            simulate_human_tap(tap_x, tap_y, bounds=[b[0], max(100, tap_y - 80), b[2], tap_y + 80])
            
            # ⏱️ 进店随机等待时间（防风控核心：模拟真人视觉对焦首图、阅读标题与价格）
            dwell_time = round(random.uniform(2.5, 4.8), 2)
            log(f"   ⏱️ [防风控拟人驻留] 进店随机浏览等待: {dwell_time}s (模拟真人看图比价)")
            time.sleep(dwell_time)
            
            # 2. 核心容错校验：检查点击后是否误触进入了外部应用（如微信、短信、QQ等系统通知）
            curr_pkg, curr_act = get_current_window()
            if curr_pkg != "com.xunmeng.pinduoduo":
                log(f"🚨 [误触消息拦截自愈] 检测到点击误入了外部应用 [{curr_pkg}] (如微信/短信弹出横幅)！")
                log(f"   正在执行自动脱困并返回拼多多...")
                ensure_pinduoduo_foreground()
                return_to_search_results()
                time.sleep(1.0)
                continue  # 坚决跳过当前项，绝不产生外部应用的错误截图与脏数据！

            # 3. 检查详情页是否处于有效状态
            detail_dump = dump_ui("detail_page.xml")
            try:
                tree_detail = safe_parse_xml(detail_dump)
                # 若遇到拼多多内部营销弹窗，自动消除
                if check_and_dismiss_popups(tree_detail):
                    time.sleep(0.8)
                    detail_dump = dump_ui("detail_page.xml")
                    tree_detail = safe_parse_xml(detail_dump)
                    
                texts_detail = [n.attrib.get('text', '') or n.attrib.get('content-desc', '') for n in tree_detail.iter()]
                # 若仍在搜索列表界面（点击可能未击中），跳过
                if "综合" in texts_detail and any(k in texts_detail for k in ["销量", "价格", "筛选"]):
                    log("⚠️ 点击未进入商品详情 (可能触控偏移或被遮挡)，跳过当前项继续寻访...")
                    continue
            except Exception as check_err:
                log(f"详情页结构初筛异常: {check_err}")

            # 4. 25% 概率触发详情页内轻触微滑交互（模拟滑看图文详情或评价）
            if random.random() < 0.25:
                micro_dist = random.randint(200, 360)
                log(f"   👀 [防风控拟人交互] 模拟轻滑微调 {micro_dist}px 查看详情参数...")
                simulate_human_swipe(direction="up", target_dist=micro_dist)
                time.sleep(random.uniform(0.8, 1.6))
            
            # 解析详情页
            shop_name = extract_shop_from_detail(detail_dump)
            log(f"   店铺: {shop_name}")

            # 规格与单价校验 (5070 场景深入抽屉校准 16G 真实单价)
            if is_5070:
                sku_info = select_16g_sku_in_detail(detail_dump)
                real_price = sku_info["spec_price"] if (sku_info["sku_verified"] and sku_info["spec_price"]) else card["price"]
            else:
                sku_info = {"sku_verified": True, "selected_spec": "标准规格", "spec_price": None, "is_sold_out": False}
                real_price = card["price"]
            
            # 截取现场截图与生成缩略图 (三位编号便于100+项排序)
            raw_shot = os.path.join(CAPTURES_DIR, f"pdd_gpu_{current_num:03d}_raw.png")
            thumb_shot = os.path.join(CAPTURES_DIR, f"pdd_gpu_{current_num:03d}_thumb.png")
            capture_product_screenshot(raw_shot, thumb_shot)
            
            # 提取商品分享链接
            link = get_product_link(title)
            log(f"   链接: {link}")
            
            # 安全返回搜索结果列表（防连击误退与外部滞留）
            return_to_search_results(keyword=keyword)
            # 离开详情页后的重聚焦随机等待
            post_return_wait = round(random.uniform(1.1, 2.2), 2)
            time.sleep(post_return_wait)
            
            m_gid = re.search(r'goods_id=(\d+)', link)
            goods_id_val = m_gid.group(1) if m_gid else ""

            item_data = {
                "title": title,
                "price": real_price,
                "raw_price": card["price"],
                "sku_verified": sku_info["sku_verified"],
                "selected_spec": sku_info["selected_spec"],
                "tags": card["tags"],
                "shop": shop_name,
                "goods_id": goods_id_val,
                "link": link,
                "raw_shot": raw_shot,
                "thumb_path": thumb_shot
            }
            collected_items.append(item_data)
            collected_titles.add(title)
            
            # 实时更新断点持久化，确保随时中断均可安全无缝续跑
            save_checkpoint(collected_items, target_count, status="IN_PROGRESS")
            
            # 进度汇报
            elapsed = int(time.time() - start_time)
            log(f"✅ [进度 {len(collected_items)}/{target_count}] 已采集: {shop_name} (累计耗时: {elapsed}s)")
            
            # 💾 阶段性增量保存机制：每采集满 10 个自动刷盘一次，杜绝因突发中断导致的数据丢失
            if len(collected_items) % 10 == 0:
                try:
                    import shutil
                    generate_excel_report(collected_items, report_file)
                    shutil.copy2(report_file, latest_file)
                    if is_5070:
                        shutil.copy2(report_file, default_5070_file)
                        shutil.copy2(report_file, default_5070_latest)
                    with open(json_file, "w", encoding="utf-8") as f:
                        json.dump(collected_items, f, ensure_ascii=False, indent=2)
                    from backend.storage_indexer import generate_index_markdown
                    generate_index_markdown()
                    log(f"💾 [增量备份] 已安全归档前 {len(collected_items)} 条数据至 Excel 与最新快照！")
                except Exception as save_err:
                    log(f"增量保存异常 (不影响继续采集): {save_err}")
            
            # ☕ 拟人思考疲劳停顿机制 (每爬取 8~12 个商品触发一次 4~8 秒的长停顿)
            if len(collected_items) - last_fatigue_checkpoint >= next_fatigue_interval:
                fatigue_pause = round(random.uniform(4.5, 7.8), 1)
                log(f"☕ [防风控拟人机制] 连续浏览多项商品，触发拟人思考/选品停顿 ({fatigue_pause}s)...")
                time.sleep(fatigue_pause)
                last_fatigue_checkpoint = len(collected_items)
                next_fatigue_interval = random.randint(8, 13)

            if len(collected_items) >= target_count:
                break
        
        if len(collected_items) >= target_count:
            break
        
        if found_in_this_view == 0:
            no_new_item_count += 1
            # 连续多次（8次以上）未发现新商品时，才自检一次当前页面是否意外偏离
            if no_new_item_count >= 8:
                page_audit = dump_ui("page_audit.xml")
                curr_page = detect_current_page(page_audit)
                if curr_page != "PAGE_SEARCH_RESULTS":
                    log(f"⚠️ [页面状态机自愈] 检测到当前页面偏离搜索列表 (当前页面: {curr_page})，正在自愈恢复...")
                    ensure_in_search_results(keyword=keyword)
                    if sort_by != "default":
                        apply_search_sort(sort_by=sort_by)
                    no_new_item_count = 0
                else:
                    if no_new_item_count >= 15:
                        log(f"📜 [深度翻页] 当前仍处于搜索列表中 (已完成 {len(collected_items)}/{target_count})，继续深度滑动寻访...")
                        no_new_item_count = 0
        else:
            no_new_item_count = 0
            
        # 模拟自然平滑翻页滑动（滑动半屏约750px，重叠1行商品，避免单次滑过4个商品导致漏采）
        log(f"📜 向上平滑滑动翻页... (当前累计采集 {len(collected_items)}/{target_count})")
        simulate_human_swipe(direction="up", target_dist=750)
        
    total_time = int(time.time() - start_time)
    log(f"==================================================")
    log(f"🎉 采集流程结束！耗时 {total_time} 秒，当前已采集 {len(collected_items)}/{target_count} 个 [{keyword}] 商品。")
    
    # 状态标记
    final_status = "COMPLETED" if len(collected_items) >= target_count else "PAUSED"
    save_checkpoint(collected_items, target_count, status=final_status)
    
    # 最终输出全量完整报告
    generate_excel_report(collected_items, report_file, sheet_title=f"{keyword}商品数据")
    try:
        import shutil
        shutil.copy2(report_file, latest_file)
        if is_5070:
            shutil.copy2(report_file, default_5070_file)
            shutil.copy2(report_file, default_5070_latest)
    except Exception:
        pass
    
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(collected_items, f, ensure_ascii=False, indent=2)
    if is_5070:
        try:
            import shutil
            shutil.copy2(json_file, default_5070_json)
        except Exception:
            pass
    log(f"JSON 数据已备份至: {json_file}")
    
    # 执行自动化数据质量与离群点质检断言
    try:
        from backend.quality import audit_batch, export_clean_excel
        from backend.database import save_audit_batch
        audited_results, q_summary = audit_batch(collected_items)
        save_audit_batch(DATE_STR, audited_results, script_name="pdd_gpu_crawler", task_title=f"拼多多-{keyword}采数", category="电商采数")
        clean_excel_path = os.path.join(REPORTS_DIR, f"pdd_{safe_kw}_clean.xlsx")
        clean_items = [r for r in audited_results if r["audit_status"] == "PASS"]
        export_clean_excel(clean_items, clean_excel_path)
        log(f"🛡️ [数据质量质检完成] 纯净合格率: {q_summary['clean_rate']}%，已拦截异常引流: {q_summary['rejected_count'] + q_summary['need_audit_count']} 项，纯净版 Excel 报表已生成！")
    except Exception as q_err:
        log(f"质检评估异常 (不影响原始数据存储): {q_err}")

    try:
        from backend.storage_indexer import generate_index_markdown
        generate_index_markdown()
    except Exception:
        pass
    
    return collected_items

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="拼多多自动化商品采集脚本")
    parser.add_argument("--count", type=int, default=100, help="目标采集商品数")
    parser.add_argument("--keyword", type=str, default="RTX 5070 Ti", help="目标采集品类或搜索关键词")
    parser.add_argument("--sort", type=str, default="default", choices=["default", "sales", "price_asc"], help="排序方式 (default:综合推荐, sales:销量优先, price_asc:价格优先)")
    parser.add_argument("--resume", dest="resume", action="store_true", default=True, help="开启断点继跑模式 (默认开启)")
    parser.add_argument("--fresh", "--reset", dest="resume", action="store_false", help="全新重新开始采集 (忽略历史断点)")
    parser.add_argument("--scenario", type=str, default="pdd_gpu", help="任务场景名称")
    args = parser.parse_args()

    try:
        run_crawler(target_count=args.count, resume=args.resume, keyword=args.keyword, sort_by=args.sort)
    except KeyboardInterrupt:
        log("⏸️ 控制台收到 Ctrl+C 中断信号，断点已安全封存，正常退出。")
