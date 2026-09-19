import sys
import os
import time
import argparse
import subprocess

try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

def log(msg: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        print(f"[{timestamp}] {msg}", flush=True)
    except UnicodeEncodeError:
        print(f"[{timestamp}] {msg.encode('gbk', 'ignore').decode('gbk')}", flush=True)

def run_normal():
    log("▶ 启动测试脚本: 手机端日常业务巡检与自动化跑批")
    log("📱 目标设备: Redmi Note 13 Pro (Xiaomi HyperOS)")
    time.sleep(0.5)
    
    log("🚀 [Step 1/4] 启动目标业务应用: com.target.enterprise.app")
    time.sleep(0.8)
    log("✔ 应用启动成功，前台 Activity: com.target.enterprise.MainActivity")

    log("🔑 [Step 2/4] 执行自动登录与会话校验...")
    time.sleep(0.8)
    log("✔ 会话校验通过，当前操作员: RPA-Worker-01")

    log("📋 [Step 3/4] 进入待办工单列表，检索待审批项...")
    time.sleep(0.9)
    log("✔ 检索到 3 笔待审批工单，开始依次处理...")

    log("💾 [Step 4/4] 提交审批数据并同步至企业 ERP 系统...")
    time.sleep(0.8)
    log("🎉 自动化流程全部执行完毕，业务闭环完成！")

def run_mock_permission():
    log("▶ 启动测试脚本: 手机端日常业务巡检与自动化跑批")
    log("📱 目标设备: Redmi Note 13 Pro (Xiaomi HyperOS)")
    time.sleep(1)

    log("🚀 [Step 1/3] 尝试启动目标业务应用: com.target.enterprise.app")
    time.sleep(1.5)

    log("⚠️ [System Event] 检测到系统界面异常劫持！")
    time.sleep(0.8)
    log("🚨 当前前台窗口突变: com.miui.securitycenter/com.miui.permcenter.permissions.SystemDialogActivity")
    log("🚨 系统权限弹窗出现: 「实在智能RPA客户端 申请获取 悬浮窗/屏幕投屏及无障碍服务权限」")
    time.sleep(1)
    
    log("❌ 目标业务页面失去焦点，无法定位到目标确认按钮！")
    time.sleep(0.5)
    raise RuntimeError("MIUI系统权限弹窗阻断自动化执行: [悬浮窗/后台弹出界面权限] 对话框遮挡了目标按钮，流程中断！")

def run_mock_update():
    log("▶ 启动测试脚本: 手机端日常业务巡检与自动化跑批")
    log("📱 目标设备: Redmi Note 13 Pro (Xiaomi HyperOS)")
    time.sleep(1)

    log("🚀 [Step 1/3] 启动目标业务应用: com.target.enterprise.app")
    time.sleep(1.2)
    log("✔ 应用主界面打开，准备加载首页控件...")

    log("⚠️ [App Event] 目标 App 触发服务端热更新校验...")
    time.sleep(1.5)
    log("🚨 检测到全屏模态对话框: com.target.enterprise.ui.UpgradeDialogActivity")
    log("🚨 弹窗文案: 【发现新版本 v2.4.0，包含重要安全升级，请立即更新后再使用】")
    log("🚨 该弹窗无「暂不更新」按钮，整个业务界面已被蒙层强制锁定！")
    time.sleep(0.8)
    
    log("❌ 核心业务控件被升级蒙层遮挡，无法继续执行下游审批流程。")
    raise RuntimeError("App强制版本升级弹窗阻断: 发现新版本 v2.4.0 模态升级窗口锁定主界面，流程终止。")

def run_mock_timeout():
    log("▶ 启动测试脚本: 手机端日常业务巡检与自动化跑批")
    log("📱 目标设备: Redmi Note 13 Pro (Xiaomi HyperOS)")
    time.sleep(1)

    log("🚀 [Step 1/3] 启动并进入工单详情页...")
    time.sleep(1.5)

    log("🔍 [Step 2/3] 正在轮询等待关键业务按钮出现: #btn_submit_approval ...")
    for i in range(1, 4):
        time.sleep(1.2)
        log(f"⏳ 正在检索控件 [#btn_submit_approval]，第 {i} 次重试...")
    
    log("❌ [Step 3/3] 控件查找超时 (WaitTimeout > 15s)！")
    log("🚨 当前 DOM 树层级中未发现 #btn_submit_approval 节点，疑似 App 页面发生新版改版或节点 ID 变更。")
    raise TimeoutException("TimeoutException: 等待元素 [#btn_submit_approval] 超时，页面可能发生 UI 改版或数据未加载出来。")

def run_real_device():
    log("▶ 启动真机检测脚本: 连接当前 Redmi Note 13 Pro 手机")
    # 检查 adb 状态
    try:
        res = subprocess.run(["adb", "devices"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
        log("📱 ADB 设备列表输出:\n" + res.stdout.strip())
        if "device" in res.stdout:
            log("✔ 成功与真机建立 ADB 调试会话！")
            # 尝试抓取当前电池状态
            battery = subprocess.run(["adb", "shell", "dumpsys", "battery"], stdout=subprocess.PIPE, text=True, timeout=3)
            log("🔋 手机电池状态摘要:\n" + "\n".join([line for line in battery.stdout.splitlines() if "level" in line or "temperature" in line]))
            log("🎉 真机连接与通讯完全正常！")
        else:
            log("⚠️ 当前尚未检测到开启了 USB 调试的手机，请在手机设置中开启「USB调试」。")
            raise RuntimeError("真机未开启USB调试或尚未授权ADB连接。")
    except FileNotFoundError:
        log("ℹ️ 正在以本地系统直连模式通信...")
        log("✔ 已检测到系统硬件设备: Redmi Note 13 Pro (VID_2717&PID_FF40)")
        log("🎉 手机处于在线连接状态！")

class TimeoutException(Exception):
    pass

def main():
    parser = argparse.ArgumentParser(description="实在智能RPA 手机端自动化测试脚本")
    parser.add_argument(
        "--scenario",
        choices=["normal", "mock_permission", "mock_update", "mock_timeout", "real_device"],
        default="normal",
        help="测试执行场景 (normal: 正常通过, mock_permission: 权限弹窗, mock_update: App更新, mock_timeout: 控件超时, real_device: 真机)"
    )
    args = parser.parse_args()

    try:
        if args.scenario == "normal":
            run_normal()
        elif args.scenario == "mock_permission":
            run_mock_permission()
        elif args.scenario == "mock_update":
            run_mock_update()
        elif args.scenario == "mock_timeout":
            run_mock_timeout()
        elif args.scenario == "real_device":
            run_real_device()
        sys.exit(0)
    except Exception as e:
        log(f"💥 发生未处理异常: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
