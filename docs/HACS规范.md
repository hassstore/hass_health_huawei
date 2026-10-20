# HACS 规范

> HACS（Home Assistant Community Store）对**仓库**的硬性要求，以及发布集成的完整流程。
>
> 整理日期：2026-10。其中「发布包结构」一节直接对照 HACS 源码
> `custom_components/hacs/repositories/base.py` 验证过，是与实际行为一致的结论。

---

## 一、HACS 校验什么、不校验什么

**校验**：仓库结构、`hacs.json`、`manifest.json` 必需键、品牌资源、许可证、
git 元数据（description / topics）、是否有 release。→ 这些由 `hacs/action` 自动检查。

**不校验**：代码质量、是否真的能跑、翻译是否完整。→ HACS 不是审稿人，
装完能不能用是开发者自己的责任。

---

## 二、仓库结构（硬性）

### 2.1 目录必须是 `ROOT/custom_components/<integration>/`

```
仓库根/
├── custom_components/
│   └── hass_health_huawei/       ← 只能有一个集成
│       ├── __init__.py
│       ├── manifest.json
│       └── ...
├── hacs.json
├── README.md
└── LICENSE
```

- 仓库里若有多个 `custom_components/*` 子目录，HACS **只管理第一个**
- 集成运行所需文件必须**全部**在 `custom_components/<integration>/` 内
- 例外：`hacs.json` 里设 `"content_in_root": true` 时，内容可以直接放仓库根

### 2.2 manifest.json 必需键

HACS 至少要求这 6 个：

```
domain · documentation · issue_tracker · codeowners · name · version
```

缺任何一个，校验直接失败。

### 2.3 品牌资源

必须有 `custom_components/<integration>/brand/icon.png`（至少 icon.png）。
或在 [home-assistant/brands](https://github.com/home-assistant/brands) 里登记域名。

> 缺了会在校验里报 `<Validation brands> failed`。
> 图标建议 512×512 透明背景 PNG，且**不要**把图标塞到别处再由代码引用。

---

## 三、hacs.json 字段

放在**仓库根目录**，文件名固定 `hacs.json`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | string | ✅ | HACS UI 里显示的名字 |
| `content_in_root` | bool | | 内容在仓库根而非 `custom_components/` 下 |
| `zip_release` | bool | | 发布附件是 zip 包；**仅集成支持**，必须同时给 `filename` |
| `filename` | string | | 要下载的附件文件名；仅对单一文件类型和 `zip_release` 有效 |
| `hide_default_branch` | bool | | 不在下载选项里提供默认分支 |
| `country` | string/array | | ISO 3166-1 alpha-2 国家码，用于按地区筛选 |
| `homeassistant` | string | | 最低 HA 版本；阻止低版本用户安装 |
| `hacs` | string | | 最低 HACS 版本 |
| `persistent_directory` | string | | 集成目录内升级时保留的相对路径（**仅集成**） |
| `render_readme` | bool | | HACS UI 里渲染 README |

示例：

```json
{
  "name": "Huawei Health",
  "homeassistant": "2024.12.0",
  "zip_release": true,
  "filename": "hass_health_huawei.zip",
  "render_readme": true
}
```

### 3.1 render_readme 的连带影响

开了 `render_readme: true`，README 会渲染在 **HACS 界面**里。此时：

- 相对链接（`../../releases`、`[LICENSE](LICENSE)`）**不会**按 GitHub 的规则解析，
  大概率 404
- **结论：README 里所有链接都写成绝对 URL**

```markdown
<!-- ❌ -->
从 [Releases](../../releases) 下载
<!-- ✅ -->
从 [Releases](https://github.com/OWNER/REPO/releases) 下载
```

### 3.2 persistent_directory

升级时会被保留的目录，适合放用户自己配置的文件。例如 `"persistent_directory": "userdata"`，
升级时 `custom_components/<integration>/userdata/` 不被覆盖。

---

## 四、发布包结构（**最容易踩坑**）

这一节对照 HACS 源码验证过，请按结论执行。

### 4.1 HACS 有两条完全不同的解压路径

`base.py` 里 `async_install` 会这样分派：

```python
if self.repository_manifest.zip_release and self.repository_manifest.filename:
    await self.download_zip_files(self.validate)     # ① 走 release 附件
else:
    await self.download_content(version_to_install)  # ② 走仓库归档
```

| 场景 | 函数 | 解压行为 |
| --- | --- | --- |
| **① `zip_release: true`**（用 release 附件） | `async_download_zip_file()` | **原样** `extractall()` 到 `custom_components/<domain>/`，**不剥任何路径** |
| ② 用仓库 zipball（无 release / 无 zip_release） | `download_repository_zip()` | 会 `split("/")[1:]` **剥掉第一层**（因为 GitHub zipball 带 `repo-ref/` 外壳） |

**剥离第一层只发生在 ②，不会发生在 ①。** 这两个行为混用是最常见的错误来源。

### 4.2 结论：zip_release 的包内必须**根级**放文件

```text
hass_health_huawei.zip
├── manifest.json              ← 必须在根
├── __init__.py
├── client.py
├── config_flow.py
├── const.py
├── sensor.py
├── strings.json
├── translations/
│   ├── en.json
│   ├── ru.json
│   └── zh-Hans.json
└── brand/
    └── icon.png
```

**错误写法**：把仓库里的 `custom_components/hass_health_huawei/` 整层打进包

```text
hass_health_huawei.zip
└── custom_components/hass_health_huawei/manifest.json   ← ✗ 嵌套了一层
```

这样会解到 `custom_components/hass_health_huawei/custom_components/hass_health_huawei/manifest.json`，
HA 找不到清单，装完等于没装。

### 4.3 打包命令

```bash
cd custom_components/hass_health_huawei
zip -r ../../hass_health_huawei.zip . \
    -x '*/__pycache__/*' '*/.DS_Store'
```

要点：
- `cd` 进集成目录再打包，天然得到根级结构
- 排除 `__pycache__`（会被打进去，白白增大体积）
- 不要在包里放 `.git`、测试文件、开发脚本

### 4.4 打包后自检（强烈建议加进 CI）

```bash
# manifest.json 必须在根
unzip -p hass_health_huawei.zip manifest.json > /dev/null \
  && echo "OK: manifest at root" || echo "FAIL: nested layout"
```

或用 Python 完整校验：

```python
import zipfile, json
z = zipfile.ZipFile("hass_health_huawei.zip")
names = z.namelist()
assert "manifest.json" in names, "manifest.json 不在根目录"
assert not any(n.startswith("custom_components/") for n in names), "包内多了嵌套目录"
assert not any("__pycache__" in n for n in names), "包含 __pycache__"
m = json.loads(z.read("manifest.json"))
print("OK:", m["domain"], m["version"])
```

---

## 五、GitHub 仓库元数据要求

这些**不是** `hacs/action` 里的必过项就是上架商店的前提，漏了会卡住。

| 项 | 要求 |
| --- | --- |
| **Description** | 必须填，一句话说明用途；HACS 直接用它当简介 |
| **Topics** | 必须有有效 topics，至少 `home-assistant` + `integration` 或 `custom-component` |
| **Releases** | 建议发布 release（只有 tag 不算）；HACS 用 release 的 tag 作为版本号 |
| **默认分支** | 通常是 `main` |
| **Issues** | 建议开启，`manifest.issue_tracker` 指向它 |

### 5.1 topics 是首次上架最常见的失败点

未设 topics 时校验会报：

```
<Validation topics> failed: The repository has no valid topics
```

推荐 topics 组合：

```
home-assistant  integration  hacs  <品牌/领域相关若干>
```

命令行设置（需要有仓库 admin 权限的 token）：

```bash
curl -X PUT -H "Authorization: token $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/OWNER/REPO/topics \
  -d '{"names":["home-assistant","integration","hacs","huawei","health"]}'
```

---

## 六、hacs/action 的 10 项校验

CI 里跑 `hacs/action@main` 时实际执行的检查（名称取自其输出）：

| # | 检查 | 关注点 |
| --- | --- | --- |
| 1 | `license` | 仓库有 LICENSE 文件 |
| 2 | `integration_categories` | manifest 的 categories（仅提示，不是硬失败） |
| 3 | `information` | 仓库基本信息（description 等） |
| 4 | `description` | description 非空 |
| 5 | `brands` | `brand/icon.png` 或 brands 仓库登记 |
| 6 | `issues` | issue tracker 有效 |
| 7 | `integration_manifest` | manifest 必需键齐全 |
| 8 | `archived` | 仓库未归档 |
| 9 | `topics` | topics 有效 |
| 10 | `hacsjson` | `hacs.json` 合法 |

全过时输出：

```
::INFO:: <Integration OWNER/REPO> All (10) checks passed
```

### 6.1 建议的 workflow

```yaml
name: HACS

on:
  push:
    branches: [main]
  pull_request:
  schedule:
    - cron: "0 0 * * 0"
  workflow_dispatch:          # 改完 topics/描述后可手动重跑，别等一周

jobs:
  validate:
    name: Validate the repository for HACS
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run HACS validation
        uses: hacs/action@main
        with:
          category: integration
```

> `workflow_dispatch` 很容易被忘。没有它，改完 topics 只能等定时任务或造一次空提交
> 才能重新校验。

---

## 七、发布流程 checklist

### 7.1 发版前

- [ ] `manifest.json` 的 `version` 改成新版本号
- [ ] `hacs.json` 的 `homeassistant` 与实际用到的最低版本一致
- [ ] 三处版本同步：`manifest.version` == git tag（去掉 `v`）== release 名
- [ ] `strings.json` 与 `translations/en.json` 键结构一致
- [ ] 跑一遍 `py_compile` / 单元测试
- [ ] `unzip` 自检包结构（见 4.4）

### 7.2 发布

```bash
git add -A
git commit -m "release: 2.0.0"
git tag -a v2.0.0 -m "2.0.0"
git push origin main
git push origin v2.0.0
```

Workflow 里**必须先校验 tag 与 manifest 一致**，否则版本号会错乱：

```yaml
- name: The tag must match manifest.json
  run: |
    version=$(python3 -c "import json;print(json.load(open('custom_components/<domain>/manifest.json'))['version'])")
    tag="${GITHUB_REF_NAME#v}"
    [ "$version" = "$tag" ] || { echo "manifest=$version tag=$tag"; exit 1; }
```

### 7.3 打包并附加到 release

```yaml
- name: Create HACS-compatible archive
  run: |
    find custom_components/<domain> -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
    cd custom_components/<domain>
    zip -r ../../<domain>.zip . -x '*/__pycache__/*' '*/.DS_Store'

- name: Verify layout
  run: unzip -p <domain>.zip manifest.json > /dev/null

- uses: softprops/action-gh-release@v2
  with:
    files: <domain>.zip
    generate_release_notes: true
```

### 7.4 发布后验证

```bash
# 下载附件并确认结构
curl -L -o pkg.zip https://github.com/OWNER/REPO/releases/download/v2.0.0/<domain>.zip
unzip -l pkg.zip | head
unzip -p pkg.zip manifest.json
```

**务必真的下载一次验证**——CI 绿了不代表附件对。

---

## 八、常见失败原因速查

| 报错 / 现象 | 原因 | 修法 |
| --- | --- | --- |
| `Validation topics failed` | 没设 topics | 加 `home-assistant` + `integration` |
| `Validation brands failed` | 缺 `brand/icon.png` | 补图标 |
| `Validation description failed` | 仓库描述为空 | 填写 description |
| `Repository structure ... is not compliant` | 不是 `custom_components/<x>/` 布局 | 调整目录 |
| `No manifest.json file found` | manifest 不在预期位置 | 放到集成目录根 |
| `Missing expected key 'x' in manifest.json` | 缺 domain/name/version/... | 补齐必需键 |
| HACS 里搜不到 | 仓库私有 / 未加为 custom repository / topics 缺失 | 逐项检查 |
| 装完 HA 里没有集成 | 发布包嵌套了一层目录 | 见第四节，重打包 |
| 装完版本号显示不对 | tag 与 `manifest.version` 不一致 | 对齐后重发 |
| 升级后配置丢失 | 用了自定义路径且未设 `persistent_directory` | 加该字段 |
| README 里链接 404 | `render_readme` 下用了相对链接 | 改绝对 URL |

---

## 九、和 HA 规范的关系

HACS 只负责**分发**，不负责**正确性**。通过 HACS 校验 ≠ 集成能用。

两份文档的关系：

```
HACS规范.md      → 怎么把集成包装、发布、让别人装上
HomeAssistant集成开发规范.md → 装上去之后能不能跑、符不符合 HA 规范
```

两个都要过，用户才真的能用。

---

## 参考

- HACS 发布要求：<https://hacs.xyz/docs/publish/start>（通用）
- HACS 集成要求：<https://hacs.xyz/docs/publish/integration>
- HACS 自定义仓库：<https://hacs.xyz/docs/faq/custom_repositories>
- HACS 官方校验 action：<https://github.com/hacs/action>
- HACS 源码（解压行为依据）：`custom_components/hacs/repositories/base.py` 中
  `async_download_zip_file()` 与 `download_repository_zip()`
