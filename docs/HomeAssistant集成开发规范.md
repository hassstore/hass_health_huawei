# Home Assistant 集成开发规范

> 面向**自定义集成（custom integration）**的实践规范。内容基于 HA 官方开发者文档，
> 并在 `hass_health_huawei` 上逐条验证过。
>
> 整理日期：2026-10。HA 迭代很快，**标注「稳定」的条目可放心依赖；标注版本号的
> 条目请对照你目标的最低 HA 版本**。

---

## 〇、先确定目标版本

自定义集成的兼容性由两个地方约束：

| 位置 | 作用 |
| --- | --- |
| `hacs.json` → `homeassistant` | HACS 用它阻止用户在过低版本上安装 |
| `manifest.json` → `version` | 自定义集成**必须**有，否则 HACS/加载器报警 |

写法建议：`"homeassistant": "2024.12.0"` 表示 2024.12 起可用。允许 beta 版本则写
`"2024.12.0b0"`。

**决定最低版本前先想清楚**你用到的 API 从哪个版本开始存在，否则用户装了也跑不起来。

---

## 一、仓库与目录结构（稳定）

```
仓库根/
├── custom_components/
│   └── <domain>/
│       ├── __init__.py           # 必需：setup / unload
│       ├── manifest.json         # 必需
│       ├── config_flow.py        # 有 UI 配置则必需
│       ├── const.py              # 惯例，非必需
│       ├── coordinator.py        # 惯例，非必需
│       ├── entity.py             # 惯例，非必需
│       ├── sensor.py 等平台文件
│       ├── strings.json          # 翻译源（英文）
│       ├── translations/
│       │   ├── en.json
│       │   └── zh-Hans.json
│       └── brand/
│           └── icon.png          # 品牌资源
├── hacs.json                     # 走 HACS 才需要
├── README.md
└── LICENSE
```

### domain 命名规则

- 全小写，只允许 `a-z0-9_`
- **不能含连字符**（Python 包名约束）
- 不能与 HA 内置集成重名；先查 <https://www.home-assistant.io/integrations/>
- 目录名、`manifest.domain`、`strings.json` 的目录、`hass_health_huawei` 这类前缀**必须完全一致**

> 常见事故：改了 domain 却忘了改目录名。加载器按目录名找集成，两者不一致会直接
> 「Integration not found」，且日志提示很不直观。

### 单集成仓库

一个仓库只放**一个**集成。若要放多个，HACS 只认 `custom_components/` 下第一个，
其余不会被打包分发。

---

## 二、manifest.json（稳定）

```json
{
  "domain": "hass_health_huawei",
  "name": "Huawei Health",
  "version": "2.0.0",
  "documentation": "https://github.com/hassstore/hass_health_huawei",
  "issue_tracker": "https://github.com/hassstore/hass_health_huawei/issues",
  "codeowners": ["@hassstore"],
  "config_flow": true,
  "iot_class": "cloud_polling",
  "integration_type": "service",
  "requirements": [],
  "loggers": []
}
```

### 字段表

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `domain` | ✅ | 必须与目录名一致 |
| `name` | ✅ | 展示名，通常与 UI 中的集成名一致 |
| `version` | ✅（自定义集成） | HA 内置集成不用写，自定义**必须**写 |
| `documentation` | ✅（HACS） | 文档 URL |
| `issue_tracker` | ✅（HACS） | issue URL |
| `codeowners` | ✅（HACS） | `@` 开头的 GitHub 用户名数组 |
| `config_flow` | 有 UI 配置时 | `true` |
| `iot_class` | 建议 | 见下表 |
| `integration_type` | 建议 | 见下表 |
| `requirements` | 按需 | pip 包列表；**纯标准库就写 `[]`** |
| `dependencies` | 按需 | 依赖的其他 HA 集成（如 `http`、`frontend`） |
| `loggers` | 按需 | 集成会打日志的第三方 logger 名，用于在 UI 里开关 |

### iot_class 取值

`assumed_state` / `cloud_polling` / `cloud_push` / `local_polling` / `local_push` /
`calculated`

### integration_type 取值

`device` / `entity` / `hub` / `service` / `system` / `helper` / `virtual`

> 判断口诀：**一个账号/服务对应多个设备 → `hub`；一个云端服务本身、不对应具体硬件
> → `service`；一个集成只有一个设备 → `device`。**

### requirements 的纪律

- 能不引第三方包就不引。纯标准库（`urllib`/`json`/`ssl`/`gzip`）能搞定就别加依赖，
  用户升级 HA 时少一层破坏面。
- 真需要时**必须 pin 版本**：`"pkg==1.2.3"`，不要写 `>=`。

---

## 三、config_flow 规范

### 3.1 ConfigFlow 基本骨架

```python
class MyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                info = await self._validate(user_input)
            except MyAuthError:
                errors["base"] = "invalid_auth"
            except MyConnectError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(info.unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info.title, data=user_input)
        return self.async_show_form(step_id="user", data_schema=..., errors=errors)
```

要点：

- **错误键**：字段级错误用字段名作 key（`errors["code"] = "invalid_code"`），
  整体错误用 `"base"`。
- **unique_id**：几乎一定要设。用账号 ID / 序列号 / MAC 等稳定值，**不要用 IP**。
- `_abort_if_unique_id_configured()` 防止重复添加。需要「添加时顺带更新已有条目」
  就用 `updates=data`。

### 3.2 reauth（稳定）

令牌失效必须走 reauth，不要静默失败：

```python
async def async_step_reauth(self, entry_data):
    self._entry_id = self.context.get("entry_id")
    return await self.async_step_reauth_confirm()

# 成功后不要手工改 data：
return self.async_update_reload_and_abort(
    entry, data_updates=new_data, unique_id=new_unique_id)
```

配套在 `abort` 段提供 `reauth_successful` 文案。

### 3.3 OptionsFlow：**不要再传 config_entry**（HA 2024.11+）

```python
# ✅ 正确
@staticmethod
@callback
def async_get_options_flow(config_entry):
    return MyOptionsFlow()

class MyOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        current = self.config_entry.options        # 直接用属性
        ...

# ❌ 已废弃
class MyOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry):
        self._entry = entry

@staticmethod
def async_get_options_flow(entry):
    return MyOptionsFlow(entry)
```

### 3.4 选择器与 translation_key（稳定）

UI 里的枚举选项不要硬编码中文/英文，走 `SelectSelector` + `translation_key`：

```python
vol.Required(CONF_REGION, default=DEFAULT_REGION): SelectSelector(
    SelectSelectorConfig(
        options=list(REGIONS),                 # ["cn", "global"]
        mode=SelectSelectorMode.DROPDOWN,
        translation_key="region",
    )
)
```

对应 `strings.json`：

```json
{
  "selector": {
    "region": { "options": { "cn": "China (mainland)", "global": "Global" } }
  }
}
```

### 3.5 一个流程多步时

步骤函数返回 `await self.async_step_xxx()` 即可串联。注意：**表单里没有输入框的
步骤**（纯说明页）用 `async_show_form(step_id=..., description_placeholders=...)`，
`data_schema` 省略，用户点「提交」时 HA 会传空 dict，靠 `user_input is not None`
判断即可前进。

---

## 四、实体与设备规范

### 4.1 has_entity_name 是强制的（新集成）

```python
class MyEntity(CoordinatorEntity[...]):
    _attr_has_entity_name = True
```

- 实体名只描述**这个数据点**，不带设备名（"Steps today"，不是 "Watch Steps today"）
- `entity_id` 会自动拼成 `<设备名slug>_<translation_key>`
- 若实体就是设备的**主要功能**（比如一盏灯的开关），设 `_attr_name = None`，
  `friendly_name` 直接等于设备名

### 4.2 unique_id 规则

```python
self._attr_unique_id = f"{entry.entry_id}_{description.key}"
```

- 一旦发布就**不能再改**，改了用户实体变孤儿
- 不要用可能变化的字段（名称、IP、位置）

### 4.3 entity_description 模式（推荐）

```python
@dataclass(kw_only=True)
class MySensorDescription(SensorEntityDescription):
    value_fn: Callable[[MyData], Any] = lambda d: None

DESCRIPTIONS = (
    MySensorDescription(key="steps", translation_key="steps",
                        native_unit_of_measurement="steps",
                        state_class=SensorStateClass.MEASUREMENT,
                        value_fn=lambda d: d.day("steps")),
)
```

好处：一堆传感器变成**声明式数据**，重复代码归零。

### 4.4 device_class / state_class / unit 要配套

| 有 device_class | 单位必须匹配 |
| --- | --- |
| `DISTANCE` | `km` / `mi` … |
| `DURATION` | `min` / `h` / `s` … |
| `ENERGY` | `kWh` / `kcal` … |
| `TEMPERATURE` | `°C` / `°F` … |

长期统计要 `state_class=MEASUREMENT`（或 `TOTAL_INCREASING`）。
`suggested_display_precision` 控制 UI 小数位，别用字符串格式化硬截。

### 4.5 unknown 与 unavailable 语义别搞反（稳定）

| 状态 | 含义 | 来源 |
| --- | --- | --- |
| `unknown` | 能连上，但这个数据点没值 | `native_value` 返回 `None` |
| `unavailable` | 连不上 / 整轮更新失败 | `available` 返回 `False` |

```python
@property
def available(self) -> bool:
    return super().available and self._data.coordinator.data is not None
```

> 把「没测到」写成 unavailable，或在云端宕机时把值写成 0，都是常见错误——
> 前者让用户以为集成坏了，后者会污染历史统计。

### 4.6 extra_state_attributes 要克制

官方明确建议：**变化的属性会写进数据库，频率高会撑爆 recorder。**

- 属性只放「解释当前状态」的信息，不放固件版本这类静态信息
- 时间戳、窗口天数这类低变化属性可以放
- 高频变化的量 → 拆成独立 sensor

### 4.7 entity_registry_enabled_default

默认禁用适合：诊断类、很少看、高频变化、实验性的实体。

```python
_attr_entity_registry_enabled_default = False
```

用户可在设备页「被禁用的实体」里手动开启。**记得在 README 里列出哪些默认禁用**，
否则用户会以为功能缺失。

---

## 五、运行时数据与生命周期

### 5.1 entry.runtime_data（HA 2024.x+，强烈推荐）

```python
async def async_setup_entry(hass, entry) -> bool:
    data = MyRuntimeData(...)
    await data.coordinator.async_config_entry_first_refresh()
    entry.runtime_data = data
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True

async def async_unload_entry(hass, entry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    entry.runtime_data = None
    return True
```

不要再往 `hass.data[DOMAIN][entry.entry_id]` 塞——那是老写法。

### 5.2 options 变更时只重载这一条

```python
@callback
def _async_entry_updated(hass, entry):
    data = entry.runtime_data
    if data is None or dict(entry.options) == data.options:
        return                      # 不是 options 变化（比如只是存令牌），忽略
    hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))
```

> 这个「比较快照」的技巧很重要：`async_update_entry` 也会触发 update listener，
> 而存令牌是高频操作。不比较就会陷入无限重载。

### 5.3 令牌轮换必须立刻持久化

轮换通常会使旧 token 失效。若写回失败，下次启动就登不进去了。

- 同步线程里轮换 → `hass.loop.call_soon_threadsafe(...)` 调度回事件循环
- 写回用 `hass.config_entries.async_update_entry(entry, data={...})`

---

## 六、轮询：DataUpdateCoordinator（稳定）

```python
class MyCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass, entry, client):
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN,
                         update_interval=timedelta(minutes=15))
        self.client = client

    async def _async_update_data(self):
        try:
            return await self.hass.async_add_executor_job(self._gather)
        except MyAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err    # → reauth 卡片
        except MyConnectError as err:
            raise UpdateFailed(f"cannot connect: {err}") from err
```

规则：

- **`ConfigEntryAuthFailed`** = 只能重新登录才能救 → HA 弹 reauth 卡片
- **`UpdateFailed`** = 暂时性失败 → 保留旧值，标记过期
- 同步客户端用 `async_add_executor_job` 包一层，不要阻塞事件循环
- **部分失败不要整体失败**：一族数据挂了，其余照常更新；全部失败才 `UpdateFailed`

```python
if not days and failures:
    raise UpdateFailed("; ".join(failures))
```

- 首次配置时用 `async_config_entry_first_refresh()`，失败会直接让配置项 setup 失败，
  用户能立刻看到问题。

---

## 七、翻译与本地化

### 7.1 strings.json 是源，translations/ 是产物

- `strings.json` = **英文原文**，供 hassfest 校验与翻译平台抽取
- `translations/<lang>.json` = 各语言实际文件
- 两者在键结构上**必须完全一致**；提交前建议 `diff` 或脚本校验

> 改完 `en.json` 一定要同步 `strings.json`，否则 hassfest 报键不一致。

### 7.2 完整键结构

```json
{
  "config": {
    "step": {
      "user": {
        "title": "...",
        "description": "...",
        "data": { "region": "..." },
        "data_description": { "region": "..." }
      }
    },
    "abort": { "already_configured": "...", "reauth_successful": "..." },
    "error": { "invalid_code": "...", "cannot_connect": "..." }
  },
  "options": { "step": { "init": { "title": "...", "data": {}, "data_description": {} } } },
  "selector": { "region": { "options": { "cn": "...", "global": "..." } } },
  "entity": {
    "sensor": {
      "steps": { "name": "Steps today" },
      "last_activity": {
        "name": "Last activity",
        "state": { "running": "Running", "cycling": "Cycling" }
      }
    }
  }
}
```

### 7.3 占位符：**每一个 `{x}` 都必须由代码提供**

```python
self.async_show_form(
    step_id="login",
    description_placeholders={"authorize_url": url, "docs": DOCS_URL},
)
```

翻译里写了 `{something}` 而代码没给 → 渲染时抛错，表单打不开。
反之代码给了翻译没用 → 无害。

> 这是最容易漏的一项。建议写个脚本正则抽出 `{...}` 与代码的
> `description_placeholders` 比对。

### 7.4 语言代码用 BCP 47 正确写法

| 用途 | 正确 | 常见错误 |
| --- | --- | --- |
| 简体中文 | `zh-Hans` | `zh-CN`、`zh_CN`、`cn` |
| 繁体中文 | `zh-Hant` | `zh-TW` |
| 巴西葡语 | `pt-BR` | `pt_BR` |

文件名、`hass.config.language` 都按这个来。

### 7.5 实体名走 translation_key，不要硬编码

```python
# ❌
SensorEntityDescription(key="steps", name="Steps today")

# ✅
SensorEntityDescription(key="steps", translation_key="steps")
```

`name=` 写死英文会让所有语言用户看到英文，也不符合新集成的 has_entity_name 要求。

---

## 八、日志与安全

```python
_LOGGER = logging.getLogger(__name__)
```

- 用 `__name__`，这样日志里显示 `custom_components.<domain>.xxx`
- 需要的日志级别：正常信息 `info`，调试细节 `debug`
- **绝不打印令牌、密码、完整响应体**。要打就截断：

```python
raise HuaweiAuthError(f"no accessToken in {str(response)[:200]}")
```

- 错误信息里带上 `resultCode` / HTTP 状态，方便用户贴日志求助

---

## 九、校验与测试

### 9.1 提交前必做

```bash
# 1. 语法
python3 -m py_compile custom_components/<domain>/*.py

# 2. JSON 合法
python3 -c "import json,glob;[json.load(open(f)) for f in glob.glob('custom_components/**/*.json',recursive=True)]"

# 3. strings.json 与 en.json 一致
diff strings.json translations/en.json && echo OK

# 4. 无未使用导入（装了 pyflakes 的话）
python3 -m pyflakes custom_components/<domain>/*.py
```

### 9.2 hassfest（官方结构校验）

最可靠的方式是放进 GitHub Actions，官方 action 会用正确的校验器版本：

```yaml
# .github/workflows/hassfest.yml
name: hassfest
on:
  push:
  pull_request:
  schedule:
    - cron: "0 0 * * 0"
  workflow_dispatch:

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master
```

它主要校验：`manifest.json` 字段合法性、`strings.json` 与 `translations/` 的键一致性、
`brand/` 资源、services 描述文件格式等。

> 自定义集成没有「质量分级」强制要求，但 manifest 键写错、翻译键不一致这类**结构性**
> 问题 hassfest 都会报，值得跑。

### 9.3 HACS action

见 `HACS规范.md`，CI 里加一次即可。

### 9.4 反向验证：把纯逻辑抽出来单独跑

把协议/解析逻辑放在**不 import homeassistant** 的模块里（如 `client.py`），
就可以脱离 HA 直接测：

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "custom_components")
from <domain>.client import parse_code, day_windows
assert parse_code("hms://...?code=AB%2B") == "AB+"
assert len(list(day_windows(20260101, 20260130))) == 3
print("OK")
PY
```

这比“部署到 HA 里点一遍”快一个数量级，强烈建议保持这种分层。

---

## 十、常见错误速查

| 症状 | 原因 | 修法 |
| --- | --- | --- |
| `Integration not found` | 目录名与 `manifest.domain` 不一致 | 改名对齐 |
| 集成不出现在「添加集成」 | 缺 `config_flow: true` 或缺 `config_flow.py` | 补上 |
| 表单打不开 / 500 | 翻译里有代码没提供的 `{占位符}` | 补齐 `description_placeholders` |
| 所有实体变 `unavailable` | `available` 逻辑过严，或 coordinator 一直 `UpdateFailed` | 查链路与 `_async_update_data` |
| 重启后实体重复 | `unique_id` 不稳定或改动过 | 固定 `unique_id` |
| 令牌失效后无提示 | 没有把认证失败转成 `ConfigEntryAuthFailed` | 加 except 分支 |
| options 改了不生效 | 存进 `entry.options` 却从 `entry.data` 读 | 两边对齐 |
| options 改一次触发无限重载 | update listener 没比较 options 快照 | 比较后再 reload |
| 历史数据被 0 污染 | 无数据时返回 `0` 而非 `None` | 返回 `None` |
| 中文不显示 | 用了 `zh-CN.json` | 改 `zh-Hans.json` |
| HACS 装完集成缺失 | 发布 zip 里嵌套了多余一层目录 | 见 HACS 规范 |

---

## 参考

- 集成开发文档：<https://developers.home-assistant.io/docs/creating_integration_index>
- manifest 字段：<https://developers.home-assistant.io/docs/creating_integration_manifest>
- 实体规范：<https://developers.home-assistant.io/docs/core/entity>
- 翻译规范：<https://developers.home-assistant.io/docs/internationalization/core>
- config flow：<https://developers.home-assistant.io/docs/config_entries_config_flow_handler>
- 质量分级：<https://developers.home-assistant.io/docs/core/integration-quality-scale>
