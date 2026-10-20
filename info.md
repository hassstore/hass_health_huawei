# Huawei Health for Home Assistant（华为运动健康）

直接读取**华为运动健康 App 自有云端**（`com.huawei.health`）的数据——就是手表
同步统计所去的那个云。集成运行期间不需要手机、手环，也不需要 App。

- **只需要一台浏览器**——每 180 天一次：集成给出登录链接，授权 code 粘贴回
  配置对话框。不绕过验证码。
- **必须开启浏览器移动设备模式**：华为授权页上的“允许”按钮只响应触摸，不响应
  鼠标点击（Chrome：F12 → Ctrl+Shift+M → 选设备 → Reload）。
- **code 从浏览器控制台取**——形如
  `Failed to launch 'hms://redirect_url?code=…' because the scheme does not have a registered handler.` 的那一行。

## 传感器

步数、距离、热量、活动时长、步行时长、活动小时数、中高强度分钟数、步数目标；
心率（静息/当前/最高/最低）；睡眠（时长、深睡、REM、浅睡、清醒、得分、效率、
HRV、SpO2）；压力；最近运动（类型、时长、距离、热量）及周期内次数；
距需重新登录的天数。

## 特性

- **多账号**——每条集成条目拥有独立令牌与设置。
- **账号区域选择**——中国大陆（`*.dbankcloud.cn`）与国际版（`*.dbankcloud.ru`
  / `*.dbankcloud.com`），登录与数据主机随之切换。
- 令牌自动轮换并立即写回配置条目。
- 日汇总与 App 完全一致；运动记录由分钟级片段去重合并（否则手表和手机会
  重复计数）。
- 界面支持简体中文 / English / Русский；仅使用 Python 标准库，无 PyPI 依赖。

> 感谢原作者 [@and7ey](https://github.com/and7ey) 的
> [huawei_health](https://github.com/and7ey/huawei_health)，本项目的协议
> 逆向工作基于其成果。

完整使用说明、工作原理与错误码见
[README](https://github.com/hassstore/hass_health_huawei/blob/main/README.md)。
