# 📱 手机端自动化运维监控控制台系统 (Mobile Console System)

本系统为移动端 RPA 与自动化采数的一体化运维调度平台，提供可视化的 Web 监控看板、多渠道告警、DeepSeek 智能盲审诊断与真机物理探针调度中枢。

##  环境要求
- **操作系统**: Windows 10 / 11
- **Python**: 3.9 ~ 3.12
- **硬件连接**: Android 手机一台（开启开发者模式与 USB 调试）

##  快速启动
1. **安装依赖**：
   ```bash
   pip install -r requirements.txt
   ```
2. **配置环境变量（可选）**：
   复制 `.env.example` 为 `.env`，填入您的 DeepSeek API Key（亦可在网页设置界面中直接配置）：
   ```bash
   copy .env.example .env
   ```
3. **一键运行**：
   - 双击运行 `一键启动控制台.bat`；
   - 或在终端执行：`python launch_web.py`；
   - 浏览器自动打开：`http://127.0.0.1:8000`。

##  安全与脱敏说明
- 本工程代码与内置数据库已彻底移除所有硬编码 API Key 与商业敏感配置，符合 GitHub 开源与公共仓库提交标准；
- `.gitignore` 已配置，默认自动忽略 `.env` 私密配置与本地调试运行数据。
