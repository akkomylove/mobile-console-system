import re
import json
import requests
from typing import Dict, Any, Optional

class AnomalyAnalyzer:
    def __init__(self):
        pass

    def diagnose(
        self,
        logs: str,
        error_message: str,
        scenario: Optional[str] = None,
        ai_config: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        进行异常根因分析 (RCA - Root Cause Analysis)。
        优先使用本地启发式专家诊断体系，若配置了大模型 API 则调用大模型生成高级诊断。
        """
        # 1. 首先运行本地专家模式进行精准定性
        diagnosis = self._heuristic_analysis(logs, error_message, scenario)

        # 2. 如果开启了大模型 API，进行 LLM 深度解读
        if ai_config and ai_config.get("ai_engine") == "openai_compatible" and ai_config.get("ai_api_key"):
            try:
                llm_res = self._call_llm(logs, error_message, diagnosis, ai_config)
                if llm_res:
                    diagnosis["ai_enhanced"] = True
                    diagnosis["root_cause"] = llm_res.get("root_cause", diagnosis["root_cause"])
                    diagnosis["actionable_suggestion"] = llm_res.get("suggestion", diagnosis["actionable_suggestion"])
            except Exception as e:
                diagnosis["llm_fallback_reason"] = f"LLM 调用失败，降级为本地专家规则: {str(e)}"

        return diagnosis

    def _heuristic_analysis(self, logs: str, error_message: str, scenario: Optional[str] = None) -> Dict[str, Any]:
        combined_text = f"{error_message}\n{logs}"

        # 规则 1: 系统权限弹窗阻断 (MIUI / HyperOS 权限弹窗)
        if scenario == "mock_permission" or any(kw in combined_text for kw in [
            "SystemDialogActivity", "com.miui.securitycenter", "PermissionDialog", 
            "权限拦截", "悬浮窗权限", "通知权限", "允许此应用", "权限申请"
        ]):
            return {
                "error_type": "PERMISSION_INTERRUPTION",
                "type_label": "MIUI系统权限拦截弹窗",
                "severity": "HIGH",
                "confidence": 0.98,
                "ai_summary": "自动化被小米系统权限弹窗打断，目标按钮被权限申请对话框遮挡无法点击。",
                "root_cause": "Redmi/小米 HyperOS 系统在脚本启动应用时触发了系统级安全提示（如悬浮窗/读取屏幕/后台自启动权限），导致当前界面层级从目标业务页面切换至系统权限申请窗口，自动化元素查找焦点丢失。",
                "affected_layer": "System UI / Permission Center",
                "actionable_suggestion": (
                    "1. 【设备设置】前往手机「设置 -> 应用设置 -> 应用管理 -> 找到目标App」，手动开启「自启动」、「显示悬浮窗」及所有必要权限；\n"
                    "2. 【脚本优化】在自动化脚本流程开头加入「权限弹窗自动放行/点击允许」检测子流程；\n"
                    "3. 【开发者选项】关闭 MIUI 优化或在开发者选项中开启「USB调试（安全设置）」。"
                )
            }

        # 规则 2: App 强制版本升级弹窗
        elif scenario == "mock_update" or any(kw in combined_text for kw in [
            "UpgradeDialog", "版本更新", "发现新版本", "立即升级", "暂不更新", 
            "强制更新", "AppUpdateException", "version_upgrade"
        ]):
            return {
                "error_type": "APP_UPDATE_INTERRUPTION",
                "type_label": "App版本更新阻断弹窗",
                "severity": "CRITICAL",
                "confidence": 0.96,
                "ai_summary": "目标应用发布了强制升级包，弹出升级对话框并锁死了主界面操作。",
                "root_cause": "目标业务 App 启动后检测到服务端有新版本（如 v2.4.0），触发强制升级弹窗蒙层，脚本既定的点击坐标或控件节点被模态蒙层遮挡，导致主业务流程完全阻塞。",
                "affected_layer": "Application Modal / Upgrade Dialog",
                "actionable_suggestion": (
                    "1. 【版本对齐】自动化运维团队需联系业务方升级手机端 App 至最新稳定版，确保与 RPA 脚本版本基准一致；\n"
                    "2. 【容错处理】在脚本公共前置模块中添加「若检测到升级弹窗，点击暂不升级/关闭」；若为不可跳过的强制升级，应在监控平台标记版本告警并暂停跑批；\n"
                    "3. 【预发布环境】配置测试机指向禁用热更新/强制升级的测试服务器通道。"
                )
            }

        # 规则 3: 控件定位超时 / UI 改版
        elif scenario == "mock_timeout" or any(kw in combined_text for kw in [
            "TimeoutException", "NoSuchElementException", "未找到控件", 
            "元素查找超时", "btn_submit_approval", "WaitTimeout"
        ]):
            return {
                "error_type": "ELEMENT_TIMEOUT",
                "type_label": "控件定位超时 (疑似界面改版)",
                "severity": "HIGH",
                "confidence": 0.92,
                "ai_summary": "目标业务按钮查找超时（>15秒），页面可能发生了改版或网络加载卡顿。",
                "root_cause": "自动化脚本等待目标控件（如 #btn_submit_approval）超时。排查现场可能原因为：App 界面改版导致控件 ID / XPath 属性变更、网络弱网导致详情页数据渲染迟滞、或者前置步骤未成功渲染该元素。",
                "affected_layer": "App DOM / UI Hierarchy",
                "actionable_suggestion": (
                    "1. 【比对快照】核对本次捕获的手机现场快照与基准快照，检查目标按钮文案、位置或 ID 是否已变更；\n"
                    "2. 【智能容错】在实在智能 RPA 中将单一的精确 ID 定位升级为「文本模糊匹配 + OCR 多模态辅助识别」复合定位；\n"
                    "3. 【超时时间】如果属于弱网加载，可将针对该页面的隐式等待时间适当延长至 20 秒。"
                )
            }

        # 规则 4: 应用崩溃或闪退 ANR
        elif any(kw in combined_text for kw in [
            "ANR", "Fatal signal", "NullPointerException", "Crash", 
            "Force Close", "应用无响应"
        ]):
            return {
                "error_type": "APP_CRASH_OR_ANR",
                "type_label": "应用闪退或无响应 (Crash/ANR)",
                "severity": "CRITICAL",
                "confidence": 0.95,
                "ai_summary": "目标业务 App 发生底层异常闪退，退回至手机桌面。",
                "root_cause": "目标移动端应用在执行批量数据处理或界面渲染时耗尽内存或触发未捕获异常，Android 系统进程守护机制将其强行 Kill 掉，脚本操作丢失宿主窗口。",
                "affected_layer": "Android OS / App Process",
                "actionable_suggestion": (
                    "1. 【日志提取】通过 `adb logcat -d` 提取该时间段崩溃堆栈并同步给 App 研发人员；\n"
                    "2. 【保护重试】在运维监控平台开启「崩溃自动重启 App 并重试本次事务」机制；\n"
                    "3. 【缓存清理】定时调用清理脚本清空 App 临时缓存或残留缓存文件。"
                )
            }

        # 默认兜底：未知通用异常
        else:
            return {
                "error_type": "SCRIPT_EXECUTION_ERROR",
                "type_label": "脚本执行未捕获异常",
                "severity": "MEDIUM",
                "confidence": 0.75,
                "ai_summary": f"脚本执行异常终止: {error_message[:60]}",
                "root_cause": f"脚本在执行自动化指令序列时抛出异常：{error_message}，未命中特定的系统弹窗或改版特征。",
                "affected_layer": "Python Runner / RPA Logic",
                "actionable_suggestion": "查看详细控制台输出日志，检查脚本参数传参、手机网络环境以及目标 App 登录态是否有效。"
            }

    def _call_llm(
        self,
        logs: str,
        error_message: str,
        heuristic_res: Dict[str, Any],
        config: Dict[str, str]
    ) -> Optional[Dict[str, str]]:
        """调用外部兼容 OpenAI 格式的大模型接口进行深度解读"""
        api_key = config.get("ai_api_key", "")
        api_base = config.get("ai_api_base", "https://api.openai.com/v1").rstrip("/")
        model = config.get("ai_model", "gpt-4o-mini")

        prompt = f"""你是一名资深手机端RPA自动化运维专家。以下手机自动化脚本在 Redmi Note 13 Pro 手机上执行时发生故障：
【错误信息】
{error_message}

【上下文日志片段】
{logs[-800:]}

【初步分类】
类型: {heuristic_res.get('type_label')}

请输出 JSON 格式（不要包含任何 markdown 代码块标识），包含：
{{
  "root_cause": "深入通俗的技术根因分析（100字内）",
  "suggestion": "给自动化运维工程师的2-3条具体排查与修改建议"
}}
"""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2
        }

        resp = requests.post(f"{api_base}/chat/completions", headers=headers, json=data, timeout=8)
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"].strip()
            content = re.sub(r"^```json\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
            return json.loads(content)
        return None

analyzer = AnomalyAnalyzer()
