# 自用 GLaDOS 自动签到

<div align="center">


---


### 📱 签到成功预览

![签到成功示例](images/success.jpg)

 **每天签到累积可兑换会员时长！**

---


**⭐ 觉得有用？点个 Star 支持一下！**

</div>

---
## 💡 重要说明
GitHub Actions 的自带定时功能很不稳定，实测大概率不会自动触发，使用 **cron-job.org**（免费服务）来触发签到。

> **签到分怎么算的**：签到分 = 基础分（服务端决定）+ 套餐加成（Pro +1 / Team +1.5 / Enterprise +2）+ 连签加成（>7 天 +1 / >14 天 +2）+ 线缆质量加成（按 IQI 分数 +1~+8）。
> 所以 **+1** 通常代表 challenge/proof 没通过、只发了基础分；**+2** 多半是「基础分 1 + 线缆 1」（免费套餐、连签不足 8 天、IQI 分数偏高），不一定是异常。设 `GLADOS_DEBUG=1` 可以看到 `🧮 加分构成: 套餐 … · 连签 … · 线缆 …` 和近 14 天趋势，对比历史就知道是否被降权。
>
> **2026-11 签到降权说明**：GLaDOS 新增签到风控，必须先 `GET /api/user/checkin/challenge` 拿 `nonce`，距签发 ≥2500ms 后用固定盐 `glados-observe-v1` 计算 `SHA-256(nonce + ".glados-observe-v1")` 作为 `proof` 一并提交。缺 `nonce`/`proof` 时接口仍返回成功，但只发 **1 积分**。本仓库已在 `checkin.py` 中实现完整流程，日志里会打印 `🔐 challenge nonce=…`。
>
>
>
---

## ✨ 功能特点

| 功能            | 说明                            |
| --------------- | ------------------------------- |
| 🎯 **精准积分** | 获取真实积分数据 + 每日变化量   |
| 🎁 **兑换提示** | 显示当前可兑换选项及差额        |
| 📱 **tg推送**   | Markdown 富文本报告：每个账号一个可折叠 `<details>` 卡片，点开展开详情（`sendRichMessage` 免费，失败自动降级旧版格式） |
| 🔐 **防降权签到** | 自动完成 `checkin/challenge` + 随机等待 + SHA-256 proof，避免签到被降权成 1 分 |
| 🍪 **JSON 直接可用** | 支持 Cookie-Editor 完整导出（含 `gld:accts`），并按 Cookie 所属域名优先请求 |
| 🚦 **三态结果** | 折叠摘要一眼可辨：✅ 正常签到 / ⚠️ 检测到异常（仅 +1 分疑似降权、未拿到 challenge 等）/ ❌ 签到失败 |

---

## 🛠 配置参数 (环境变量)

本项目支持以下环境变量配置：

| 变量名               | 必填  | 说明                                                                       |
| -------------------- | ----- | -------------------------------------------------------------------------- |
| `GLADOS_COOKIE`      | ✅ 是 | GLaDOS 的 Cookie。多个账号**一行一个**，每行末尾可用 `#策略` 单独设置兑换（见下文） |
| `TELEGRAM_BOT_TOKEN` | ❌ 否 | Telegram 机器人的 Token（例如 `123456:ABC-DEF1234...`）                    |
| `TELEGRAM_CHAT_ID`   | ❌ 否 | 接收推送的 Telegram Chat ID                                                |
| `GLADOS_CHALLENGE_WAIT_MIN` / `_MAX` | ❌ 否 | 每个账号拿到 challenge 后的**随机**等待区间（秒），默认 `2.8`~`9.0`，下限被强制钳到 2.5s |
| `GLADOS_ACCOUNT_GAP_MIN` / `_MAX`    | ❌ 否 | 相邻账号之间的随机间隔（秒），默认 `8`~`35`，多账号靠它拉开时间 |
| `GLADOS_UA_ROTATE`   | ❌ 否 | `1`（默认）按账号固定分配不同 UA；`0` 全部退回同一 UA |
| `GLADOS_USER_AGENT_<行号>` | ❌ 否 | 强制指定某一行账号的 UA，如 `GLADOS_USER_AGENT_2`，用于修 `device-mismatch` |
| `GLADOS_DEBUG`       | ❌ 否 | `1` 开启调试：打印原始接口响应、points 历史、近 14 天趋势、加分构成（套餐/连签/线缆） |

> 多账号反关联策略：**执行顺序每次随机打乱**（推送仍按配置行号排序）+ 账号间随机间隔 + 每账号固定不同 UA（按 Cookie 哈希决定，同账号跨运行不变，避免 device-mismatch）。
> 若日志出现 `code=4` / `device-mismatch`，说明该账号登录时的设备指纹和脚本不一致，用 `GLADOS_USER_AGENT_<行号>` 填浏览器里真实的 UA 即可，或临时 `GLADOS_UA_ROTATE=0`。

---

### 🎛 多账号分别设置兑换策略

`GLADOS_COOKIE` 中**一行一个账号**，每行末尾可以用 `#` 追加该账号的兑换策略：

```text
# 以 # 开头的行是注释，可用来标记账号，会被忽略
gld:sess=账号A的长串; gld:sess.sig=短串A#plan200
gld:sess=账号B的长串; gld:sess.sig=短串B#off
gld:sess=账号C的长串; gld:sess.sig=短串C
```

- 账号 A：积分够 200 时自动兑换 plan200
- 账号 B：**只签到，永不兑换**
- 账号 C：行尾未设置策略，只签到不兑换

支持的策略值：`plan100` / `plan200` / `plan500` / `off`（`off` 也可写成 `false`、`0`、`no`、`关闭`）。以 `#` 开头的行视为注释，写错策略名会告警并跳过兑换，不影响签到。

---



## 🚀 快速部署

### 第一步：Fork 本仓库

点击页面右上角的 **Fork** 按钮，将项目复制到你的账号下。

---

### 第二步：获取 Cookie 🍪

打开浏览器，登录：https://glados.cloud
按 F12 打开开发者工具
找到：
Chrome：Application → Cookies
Firefox：存储 → Cookies
选择 glados.cloud
复制完整 Cookie 内容

gld:sess=xxxxxx; gld:sess.sig=yyyyyy
⚠️ 必须是完整的一整段，**`gld:sess` 和 `gld:sess.sig` 两个都要**，只填 `gld:sess` 会返回 `No permission`



####  组合 Cookie（重要！）

**推荐：直接用 Cookie-Editor 导出完整 JSON**（4 个 Cookie 一键导出，不用手拼）：

```json
[
  {"domain": "glados.network", "name": "gld:sess", "value": "gld_xxx"},
  {"domain": "glados.network", "name": "gld:sess.sig", "value": "xxx"},
  {"domain": "glados.network", "name": "gld:accts", "value": "gld_xxx"},
  {"domain": "glados.network", "name": "gld:accts.sig", "value": "xxx"}
]
```

直接把整段 JSON 粘进 `GLADOS_COOKIE` 即可（多行也没问题），脚本会：

- 自动拆成 `name=value; name=value; ...` 请求头；
- 从 `domain` 字段识别 Cookie 是哪个站点的，**优先访问该域名**（比如 `glados.network` 的 Cookie 就先打 `glados.network`，不再强行先走 `glados.cloud`）；
- 多账号时用 `[[...], [...]]`（数组套数组），每个内层数组是一个账号，末尾同样可跟 `#plan200`。

> 新版登录（邮箱 / 验证码）会同时下发 **`gld:accts` / `gld:accts.sig`**；旧的 2 个 Cookie（`gld:sess` + `.sig`）仍然能鉴权，但建议整段照抄，少一个少一份可信度。

也仍然支持请求头格式：

```text
gld:sess=你的长字符串; gld:sess.sig=你的短字符串
```

**正确示例**：

```text
gld:sess=eyJ1c2VySWQiOjEyMzQ1Njc4OTB9; gld:sess.sig=abcdef123456
```

> ⚠️ 站点签发 Cookie 已从 `koa:sess` 改为 **`gld:sess`**，且**服务端已废弃旧的 koa 格式**：
> 填 `koa:sess=...; koa:sess.sig=...` 会在所有域名返回 `code:-2 没有权限`，无法签到。
> 两者不是同一种凭证（koa 是 JWT 明文，gld 是 `gld_<40位hex>` 的库内查询串），**无法本地转换**。
> 请重新登录 glados.cloud，从 DevTools → Application → Cookies 复制新的 `gld:sess` + `gld:sess.sig`。
> 脚本检测到旧格式时会打印告警，报错信息里也会提示 `鉴权失败`，方便定位。

**常见错误**：

- ❌ 缺少分号 `;`
- ❌ 缺少空格（分号后需要一个空格）
- ❌ 值两边多了引号
- ❌ 复制了多余的空格或换行


### 第三步：配置 GitHub Secrets 🔐

1. 进入你 Fork 的仓库
2. 点击 **Settings**（设置）
3. 左侧菜单找到 **Secrets and variables** → **Actions**
4. 点击右上角 **New repository secret**


添加以下三个 Secret：

| Name             | Value                    | 必需  |
| ---------------- | ------------------------ | ----- |
| `GLADOS_COOKIE`  | 第二步组合的 Cookie      | ✅ 是 |
| `TELEGRAM_BOT_TOKEN` | Telegram 机器人的 Token（例如 `123456:ABC-DEF1234...`） | ❌ 否 |
| `TELEGRAM_CHAT_ID` | 接收推送的 Telegram Chat ID | ❌ 否 |
---


### 第四步：启用 Actions ⚡

1. 进入你 Fork 仓库的 **Actions** 标签页
2. 如果看到黄色提示，点击 **I understand my workflows, go ahead and enable them**
3. 点击左侧的 **GLaDOS  Checkin**
4. 点击右侧 **Run workflow** 按钮手动测试一次



---
## ⭐ 推荐方案：cron-job.org 配置定时

### 配置步骤

#### 第一步：获取 GitHub Personal Access Token

1. 访问 [https://github.com/settings/tokens](https://github.com/settings/tokens)
2. 点击 **Generate new token** → **Generate new token (classic)**
3. 按如下配置：


| 选项           | 值                              |
| -------------- | ------------------------------- |
| **Name**       | `glados-cron`（任意名称）       |
| **Expiration** | 选择 90 天或更久                |
| **勾选权限**   | ✅ **workflow**（在 repo 下方） |

4. 点击底部 **Generate token**
5. **立即复制生成的 token**（格式类似 `ghp_1234567890abcdef...`，只显示一次！）

> 💡 Token 示例：`ghp_NXLTUqT51BFfilsaZNlaVstacNnkZc4PYCNa`

#### 第二步：注册 cron-job.org

1. 访问 [https://cron-job.org](https://cron-job.org) 注册账号（免费）
2. 注册后登录，点击 **Create Cronjob** 创建任务

#### 第三步：创建签到任务（7:00）

![创建 Cron 任务](images/create_corn_job.png)

按照以下配置填写：

**基本信息**：

| 选项      | 填写                                                                                                   |
| --------- | ------------------------------------------------------------------------------------------------------ |
| **Title** | `GLaDOS 签到`                                                                                        |
| **URL**   | `[https://api.github.com/repos/你的用户名/glados-checkin/actions/workflows/checkin.yml/dispatches`|

> ⚠️ **重要**：把 `你的用户名` 改成你的 GitHub 用户名！`

**执行时间**：选择每天 **07:00**（Asia/Shanghai 时区）

**高级配置**（点击 Advanced 展开）：

![高级配置](images/cron_advanced.png)

| 选项               | 值            |
| ------------------ | ------------- |
| **Request method** | POST          |
| **Time zone**      | Asia/Shanghai |

**请求头（Headers）**：点击 "+ 添加" 添加三行：

| Key             | Value                            |
| --------------- | -------------------------------- |
| `Accept`        | `application/vnd.github.v3+json` |
| `Authorization` | `token 你复制的GitHub_Token`     |
| `Content-Type`  | `application/json`               |

> ⚠️ **注意**：Authorization 的值是 `token ` + **空格** + 你的 Token，例如：`token ghp_NXLTUqT51BFfilsaZNlaVstacNnkZc4PYCNa`

**请求体（Request body）**：选择 Raw Body，填入：

```json
{ "ref": "main" }
```


配置完成后点击 **Save** 保存。



#### 第四步：测试验证

1. 在任务列表点击 **Test run** 测试
2. 成功会显示 **204 No Content** ✅

![测试成功](images/cron_success.png)

3. 到 GitHub 仓库的 **Actions** 页面查看，应该有新的运行记录

---


### 🚨 常见陷阱与错误

| 错误                         | 现象         | 原因                   | 解决方法                                       |
| ---------------------------- | ------------ | ---------------------- | ---------------------------------------------- |
| **401 Unauthorized**         | 认证失败     | Authorization 格式错误 | 必须是 `token ghp_xxx`，注意 `token ` 后有空格 |
| **422 Unprocessable Entity** | 请求无法处理 | Body 缺少 ref 参数     | 改为 `{"ref": "main"}`                         |
| Accept 被截断                | 配置错误     | 输入框显示不全         | 完整值：`application/vnd.github.v3+json`       |
| Token 有空格                 | 认证失败     | Token 被意外截断       | Token 是连续字符串，中间不能有空格             |
| 权限不足                     | 403 错误     | Token 无 workflow 权限 | 重新生成 Token，勾选 workflow 权限             |

> 💡 **小贴士**：遇到 401/422 错误时，先检查上面三行 Headers 是否完全正确！

**🎉 完成！** 以后每天 7:00 会自动签到，注意有可能有延迟。

---


---

---

## 📂 项目文件

| 文件                                                                         | 说明                |
| ---------------------------------------------------------------------------- | ------------------- |
| [checkin.py](file://d:\workplace\2026-glados-checkin\checkin.py)             | 核心签到脚本        |
| `.github/workflows/checkin.yml`                                              | GitHub Actions 配置 |
| [requirements.txt](file://d:\workplace\2026-glados-checkin\requirements.txt) | Python 依赖         |
| `images/`                                                                    | 教程截图            |

---


## 📝 License

MIT

---

<div align="center">


**🔧 本项目经过 2026-05-07 验证可用！**


</div>
