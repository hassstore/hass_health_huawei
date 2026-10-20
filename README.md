# Huawei Health for Home Assistant（华为运动健康）

> **免责声明**
>
> 本项目为非官方 Home Assistant 集成，未获得华为或相关平台的授权、认可或支持。
> 项目仅用于学习、交流与技术研究，请勿用于商业用途或其他违反适用法律、平台规则
> 和服务条款的行为。

把华为运动健康 App 云端（`com.huawei.health` 自有云）中的数据接入 Home Assistant，
生成步数、睡眠、心率、压力、运动等传感器。读到的数字与 App「活动」页面完全一致。

不需要手机、手表或运动健康 App 常驻运行——数据本来就在云端，集成只需要一个
HUAWEI ID 账号，每 180 天用浏览器登录一次即可。支持多账号，每个账号一条
独立集成条目。

> 本项目基于 [@and7ey](https://github.com/and7ey) 的
> [huawei_health](https://github.com/and7ey/huawei_health) 逆向工程成果二次开发，
> 感谢原作者。

> **从旧版 `huawei_health` 迁移。** 2.0 起集成更名为 `hass_health_huawei`，
> 旧条目**不会自动迁移**。请先删除旧的 `huawei_health` 集成（其传感器会随之移除），
> 再安装本集成重新添加账号。历史数据仍保留在华为云端，重新接入后即可继续读取。

---

## 要求

- Home Assistant **2024.12** 或更新版本
- 一个已同步过数据的 HUAWEI ID 账号（运动健康 App 至少上传过一次）
- 一台带浏览器的电脑（Chrome/Chromium 或 Firefox），用于首次登录
- 无 PyPI 依赖，仅使用 Python 标准库

## 安装

**HACS（推荐）**

1. HACS → ⋮ → **Custom repositories**
2. 类型选 `Integration`，仓库填 `https://github.com/hassstore/hass_health_huawei`
3. 搜索 **Huawei Health** → **Download**
4. 重启 Home Assistant（系统重启，不是"重新加载配置"）

**手动**

从 [Releases](https://github.com/hassstore/hass_health_huawei/releases) 下载 `hass_health_huawei.zip`，解压到配置目录，
使路径为 `custom_components/hass_health_huawei/`，重启 Home Assistant。

## 配置（约 5 分钟）

唯一需要人工的环节是 HUAWEI ID 登录——授权页有华为验证码，必须由真人完成。

### 第 1 步：添加集成并选择区域

**设置 → 设备与服务 → 添加集成 → Huawei Health**，先选择账号区域：

- **中国大陆**：账号在中国区创建或用 `cloud.huawei.com.cn` 登录
- **国际版**：欧洲、俄罗斯、亚太等所有其他账号

选错的表现是登录时报"账号不存在"，返回重选即可。

### 第 2 步：打开登录链接（必须用移动设备模式）

集成会生成专属登录链接，在浏览器新标签页打开并完成登录。

⚠️ **关键**：授权页的"允许/Allow"按钮**只响应触摸，不响应鼠标点击**。必须先
把浏览器切到移动设备模式，否则点了没有任何反应（不报错、不跳转）：

1. **F12** 打开 DevTools
2. **Ctrl+Shift+M**（macOS：`Cmd+Shift+M`）开启设备模拟
3. 顶部下拉框选择一台设备（如 iPhone 12 Pro、Pixel 7）
4. **F5** 重新加载页面——必须重载后华为才下发移动版页面
5. 滚到底部，点"允许"

Firefox：F12 → 手机图标（Ctrl+Shift+M），其余相同。

### 第 3 步：从控制台取回 code

点击允许后浏览器尝试跳转 `hms://redirect_url?code=...`，电脑上无此协议处理器，
跳转失败，但 Chrome 会把这行写进控制台：

```text
Failed to launch 'hms://redirect_url?code=...' because the scheme does not have a registered handler.
```

**F12 → Console**，找到这行（筛选框输入 `Failed to launch` 可快速定位），右键
**Copy text** 或直接整行复制，粘贴回集成输入框提交。

- 整行粘贴即可，集成会自行解析出 code 并做 URL 解码
- code **一次性、几分钟内有效**：被拒绝就重新登录拿一个新 code
- Firefox 也可以在 **Network → 302 请求 → 响应头 `Location`** 里取同一链接

### 第 4 步：完成

集成用 code 换令牌、按 `uid` 识别账号、以 HUAWEI ID 昵称创建设备，传感器在
10–30 秒内出现。entity_id 形如 `sensor.<昵称>_steps`。

- `unknown`：云端没有这类数据（例如没有测 SpO2 的设备），或当天还没同步。
- `unavailable`：整轮轮询失败（云端不可达），等下一次轮询会自动恢复。

## 传感器

按天统计的传感器（活动、心率、睡眠、压力，共 23 个）带 `date` 属性，标注数值
所属日期。

- 活动/心率/压力类只读"今天"，当天还没同步时显示 `unknown`——这是正常的，云端
  要等手表或手机同步后才补齐当天数据。
- 睡眠类读"刚结束的一晚"，归属醒来的那天；当天没有睡眠记录时会自动回退查前
  一天，所以早上看到的通常是昨晚的完整数据。

运动类传感器带 `start`/`end`/`duration_min`/`sport_type`/`device_code` 等属性；
"距需重新登录"带两个 token 的到期时间。

| 分类 | 传感器 |
| --- | --- |
| 日常活动 | 步数、距离、热量、活动时长、步行时长、活动小时数、中高强度分钟数、步数目标 |
| 心率 | 静息、当前、最高、最低 |
| 睡眠 | 总时长、深睡、REM、浅睡、清醒、睡眠得分、效率、HRV、SpO2 |
| 压力 | 压力均值、最近一次压力 |
| 运动 | 最近运动（类型）、时长、距离、热量、周期内次数 |
| 账号 | 距需重新登录天数（refresh token 剩余有效期，可用于提醒自动化） |

默认禁用的传感器（在设备页"被禁用的实体"里可手动开启）：步数目标、
最高/最低心率、浅睡、睡眠中清醒、睡眠效率、最近一次压力。

## 账号设置

**设置 → 集成 → Huawei Health → ⋮ → 配置**：

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| 轮询间隔 | 15 分钟 | 更频繁无意义，云端只在手表/手机同步时更新 |
| 日数据天数 | 3 | 只影响日传感器，始终单次请求 |
| 运动记录天数 | 7 | 云端单窗口上限 10 天，超过自动拆分为多次请求（30 天 = 3 次） |
| 轮询运动记录 | 开 | 关掉可省掉最慢的一次请求 |
| 轮询心率/睡眠/压力 | 开 | 无心率设备可关 |
| deviceCode | 0 | 0=整个账号。填入手表 id 后，活动类统计按该设备过滤；心率/睡眠/压力始终按整个账号读取 |

默认参数下每个账号每 15 分钟发 3 次 POST（日汇总、健康、运动记录各一次）。
运动记录天数超过 10 天时会按 10 天窗口拆分，请求数随之增加。
修改设置只重载该条目。

## 工作原理（简述）

- 协议还原自 App 16.1.6.320：`/dataQuery/*` 控制器，云端主机按区域路由
  （中国区 `*.dbankcloud.cn`，国际区 `*.dbankcloud.ru/com`）
- `authorizationCode` → `accessToken`（几十分钟，自动轮换并写回条目）+
  `refreshToken`（180 天，到期触发重新登录卡片）
- 运动记录由分钟级片段按类型分组、每分钟取最优、间隔 ≤15 分钟合并成会话——
  这是与 App 数值一致的关键（手表和手机会各发一份同一运动）
- 某族数据失败不影响其他族；全部失败才标记过期；认证类错误直接触发 reauth

> **中国区支持。** 中国大陆区域登录走 `oauth-login.cloud.huawei.com.cn`，数据走
> `healthdata.dbankcloud.cn`，会话由 `healthcommon-drcn.dbankcloud.cn` 签发——
> 这套端点已由中国区账号实际测试可用。若仍遇到问题，请到
> [Issues](https://github.com/hassstore/hass_health_huawei/issues) 反馈日志里的
> `resultCode`。

## 常见问题

| 问题 | 处理 |
| --- | --- |
| "允许"按钮点不动 | 移动模式没生效：Ctrl+Shift+M 选设备后**必须 F5 重载** |
| 找不到 `Failed to launch` 行 | 用 Network → 302 → `Location` 响应头取链接 |
| code 被拒绝（`20020001`） | code 过期/已用，重新登录拿新 code |
| 提示重新登录（`20020003`） | refresh token 满 180 天，点"重新配置"重走登录 |
| `already_configured` | 账号已存在，走 reauth 更新令牌即可 |
| `30005 Invalid device` | deviceCode 属于别的账号，重置为 0 |
| 所有传感器 `unknown` | 打开调试日志看原因：`homeassistant.components.hass_health_huawei: debug` |

## 安全须知

- **非官方 API**，无华为文档背书，云端响应可能随 App 版本变化
- App 与集成**共用同一 refresh token**，谁后轮换谁掌权；若手机上 App 频繁
  抢权，请调大轮询间隔
- 令牌存放在 `.storage/core.config_entries`，请像保护密码一样保护其备份
- 关于授权与使用范围的说明见文首**免责声明**

## 开发与发布

- `client.py` 零 HA 依赖，可直接 `python3` 驱动调试
- 推送 `vX.Y.Z` tag（与 `manifest.json` 版本一致）由
  [release.yml](https://github.com/hassstore/hass_health_huawei/blob/main/.github/workflows/release.yml) 自动打包 `hass_health_huawei.zip`
- 品牌图标用 `python3 tools/make_icon.py` 重新生成

## 许可证

MIT，见 [LICENSE](https://github.com/hassstore/hass_health_huawei/blob/main/LICENSE)。原始作者 [@and7ey](https://github.com/and7ey)，
本 fork 由 [@hassstore](https://github.com/hassstore) 维护。
