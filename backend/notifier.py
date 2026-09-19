import json
import requests
from typing import Dict, Any, Optional

class AlertNotifier:
    def __init__(self):
        pass

    def send_alert(self, run: Dict[str, Any], diagnosis: Dict[str, Any], configs: Dict[str, str]) -> Dict[str, Any]:
        """
        向配置好的 Webhook 渠道（飞书、钉钉、企业微信、通用 Webhook）派发告警
        """
        results = {}
        alert_enabled = configs.get("alert_enabled", "true").lower() == "true"
        if not alert_enabled:
            return {"status": "skipped", "reason": "告警总开关已关闭"}

        # 飞书机器人告警
        feishu_url = configs.get("feishu_webhook", "").strip()
        if feishu_url:
            results["feishu"] = self._send_feishu_card(feishu_url, run, diagnosis)

        # 钉钉机器人告警
        dingtalk_url = configs.get("dingtalk_webhook", "").strip()
        if dingtalk_url:
            results["dingtalk"] = self._send_dingtalk_markdown(dingtalk_url, run, diagnosis)

        # 企业微信机器人告警
        wecom_url = configs.get("wecom_webhook", "").strip()
        if wecom_url:
            results["wecom"] = self._send_wecom_markdown(wecom_url, run, diagnosis)

        # 自定义 Webhook
        custom_url = configs.get("custom_webhook", "").strip()
        if custom_url:
            results["custom"] = self._send_custom_webhook(custom_url, run, diagnosis)

        return results

    def _send_feishu_card(self, webhook_url: str, run: Dict[str, Any], diagnosis: Dict[str, Any]) -> bool:
        """发送飞书交互式卡片消息"""
        title = f"🚨 手机端流程控制台 · 自动化执行异常告警"
        template_color = "red" if diagnosis.get("severity") == "CRITICAL" else "orange"
        
        card = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": template_color
                },
                "elements": [
                    {
                        "tag": "div",
                        "fields": [
                            {"is_short": True, "text": {"tag": "lark_md", "content": f"**任务脚本:**\n{run.get('script_name')}"}},
                            {"is_short": True, "text": {"tag": "lark_md", "content": f"**受影响设备:**\n{run.get('device_name', 'Redmi Note 13 Pro')}"}},
                            {"is_short": True, "text": {"tag": "lark_md", "content": f"**异常分类:**\n{diagnosis.get('type_label')}"}},
                            {"is_short": True, "text": {"tag": "lark_md", "content": f"**执行耗时:**\n{run.get('duration')} 秒"}}
                        ]
                    },
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**🤖 AI 根因分析:**\n{diagnosis.get('root_cause')}\n\n**💡 运维建议:**\n{diagnosis.get('actionable_suggestion')}"
                        }
                    },
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "🔍 前往手机端流程控制台排查现场"},
                                "type": "primary",
                                "url": "http://localhost:8000"
                            }
                        ]
                    }
                ]
            }
        }
        try:
            resp = requests.post(webhook_url, json=card, timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def _send_dingtalk_markdown(self, webhook_url: str, run: Dict[str, Any], diagnosis: Dict[str, Any]) -> bool:
        """发送钉钉 Markdown 格式告警"""
        text = f"""### 🚨 手机端流程控制台 · 自动化异常告警
---
- **脚本名称**: {run.get('script_name')}
- **关联设备**: {run.get('device_name', 'Redmi Note 13 Pro')}
- **故障类型**: <font color='#f5222d'>{diagnosis.get('type_label')}</font>
- **耗时**: {run.get('duration')}s
- **发生时间**: {run.get('start_time')}

#### 🤖 AI 根因智能诊断
> {diagnosis.get('root_cause')}

#### 💡 运维与排查建议
> {diagnosis.get('actionable_suggestion')}

[👉 点击进入手机端流程控制台查看现场截屏与堆栈](http://localhost:8000)
"""
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": "手机端自动化异常告警",
                "text": text
            }
        }
        try:
            resp = requests.post(webhook_url, json=payload, timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def _send_wecom_markdown(self, webhook_url: str, run: Dict[str, Any], diagnosis: Dict[str, Any]) -> bool:
        """发送企业微信 Markdown 格式告警"""
        text = f"""### 🚨 <font color="warning">手机端流程控制台 · 自动化故障告警</font>
> **任务脚本**: {run.get('script_name')}
> **测试手机**: {run.get('device_name', 'Redmi Note 13 Pro')}
> **异常定性**: <font color="comment">{diagnosis.get('type_label')}</font>
> **耗时**: {run.get('duration')}s

**🤖 智能根因分析**:
{diagnosis.get('root_cause')}

**💡 修复建议**:
{diagnosis.get('actionable_suggestion')}

[查看手机现场截屏与完整堆栈](http://localhost:8000)
"""
        payload = {
            "msgtype": "markdown",
            "markdown": {"content": text}
        }
        try:
            resp = requests.post(webhook_url, json=payload, timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def _send_custom_webhook(self, webhook_url: str, run: Dict[str, Any], diagnosis: Dict[str, Any]) -> bool:
        """通用 Webhook POST JSON 请求"""
        payload = {
            "event": "automation_failed",
            "run_id": run.get("id"),
            "script_name": run.get("script_name"),
            "device": run.get("device_name"),
            "error_type": diagnosis.get("error_type"),
            "diagnosis": diagnosis,
            "timestamp": run.get("start_time")
        }
        try:
            resp = requests.post(webhook_url, json=payload, timeout=5)
            return resp.status_code in [200, 201, 204]
        except Exception:
            return False

    def send_test_message(self, channel: str, webhook_url: str) -> Dict[str, Any]:
        """测试 Webhook 是否畅通"""
        dummy_run = {
            "id": "test_run_001",
            "script_name": "sample_mobile_task.py (测试通道)",
            "device_name": "Redmi Note 13 Pro",
            "duration": 4.5,
            "start_time": "2026-09-12 21:50:00"
        }
        dummy_diag = {
            "type_label": "测试连接通道",
            "severity": "LOW",
            "root_cause": "这是一条来自「手机端流程控制台」的测试告警消息，通道连接正常！",
            "actionable_suggestion": "通道配置有效，后续脚本发生系统权限弹窗或App强制升级时将自动发送告警卡片。"
        }

        if channel == "feishu":
            ok = self._send_feishu_card(webhook_url, dummy_run, dummy_diag)
        elif channel == "dingtalk":
            ok = self._send_dingtalk_markdown(webhook_url, dummy_run, dummy_diag)
        elif channel == "wecom":
            ok = self._send_wecom_markdown(webhook_url, dummy_run, dummy_diag)
        else:
            ok = self._send_custom_webhook(webhook_url, dummy_run, dummy_diag)

        return {"success": ok, "message": "消息推送成功！请检查群聊" if ok else "推送失败，请检查 Webhook 地址是否正确以及是否配置了安全关键词"}

notifier = AlertNotifier()
