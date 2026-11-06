# dida365-tasks

独立、可公开分发的 Hermes Agent skill，用官方 Open API + OAuth2 管理滴答清单（国内版）与 TickTick（国际版）任务。随附一个仅使用 Python 标准库的 CLI，支持项目与未完成任务查询、新增、修改、完成、删除和收件箱发现。

本项目不使用 MCP、私有 API 或 Cookie，也不要求把凭据复制到仓库。

## 功能与原则

- CN / Intl 两套官方端点，OAuth scope 为 `tasks:write tasks:read`。
- 日期无时刻：全天任务，`isAllDay: true`；带时刻：定时任务。
- 默认路由到收件箱，保持官方 Open API 的既有行为；也可配置为“工作”“科研”或“个人”，绝不擅自创建清单。
- 保留用户标题与备注原文，写入后回读验证。
- 收件箱任务不直接 update；必须经用户确认后删除并重建。
- 本地覆盖配置可选择既有生产客户端，公开文件不含本机绝对路径或真实账户 ID。

## 环境要求

- Hermes Agent
- Python 3.10+
- 滴答清单或 TickTick 开发者应用
- 已验证平台：Windows 11、Python 3.14.9；其他平台尚未实测，因此 frontmatter 仅声明 `windows`。

客户端没有第三方 Python 依赖。

## 安装

### 从 GitHub 获取完整项目

```bash
git clone https://github.com/scw2015bnu-png/dida365-tasks.git
cd dida365-tasks
```

完整检出包含配置示例、测试、README 和许可证。

### 安装到 Hermes

将整个项目目录复制到当前 Hermes profile 的 `skills/productivity/dida365-tasks/`。不要只复制 `SKILL.md`，否则随附客户端和测试会缺失。安装后新开会话，让 Hermes 重新加载技能索引。

也可以从 `https://github.com/scw2015bnu-png/dida365-tasks` 下载完整源码压缩包后按同样目录结构安装。

## 可选本地覆盖配置

复制 `config.local.json.example` 为 `config.local.json`：

```json
{
  "client_path": "scripts/dida.py",
  "default_project": "收件箱",
  "timezone": "Asia/Shanghai"
}
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `client_path` | 客户端路径；相对路径基于 skill 根目录。可改为本机既有 `dida.py` 的绝对路径，以沿用其生产行为和凭据。为空或未配置时使用随附的 `scripts/dida.py`。 |
| `default_project` | 用户未指定清单时的默认目标，例如“工作”“科研”“个人”。 |
| `timezone` | agent 解析自然语言日期和定时任务时使用的 IANA 时区。 |

`config.local.json` 是 **agent 层覆盖配置**，不会被 `scripts/dida.py` 自动读取，且已被 `.gitignore` 排除。SKILL.md 要求优先使用其中的 `client_path`，未配置时才回退到随附客户端。

纯日期默认规范化为 `+0800` 午夜，以兼容既有生产行为。其他时区应在 add/update 时显式传 `--timezone <IANA名称>`；客户端会计算当地午夜的 UTC offset 并写入 `timeZone`。定时任务请传带正确 UTC offset 的完整 ISO 时间。

## OAuth 配置

### 1. 创建开发者应用

- 国内版：[Dida365 Developer Center](https://developer.dida365.com/manage)
- 国际版：[TickTick Developer Center](https://developer.ticktick.com/manage)
- 默认回调地址：`http://127.0.0.1:8765/callback`

开发者控制台中的 redirect URI 必须与客户端参数完全一致。CN 与 Intl 账号和端点不要混用。

### 2. 保存应用凭据并授权

以下以随附客户端为例；若 `client_path` 指向既有客户端，请用该路径替换。请在本机受信终端亲自执行，不要把 secret、授权 URL 回跳 code 或 token 发到聊天中。

```bash
# 国内版；Intl 使用 --region intl
python scripts/dida.py setapp --client-id YOUR_CLIENT_ID --client-secret YOUR_CLIENT_SECRET --region cn
python scripts/dida.py auth
python scripts/dida.py status
```

无本地浏览器回调的环境：

```bash
python scripts/dida.py auth --manual
```

`setapp` 的 secret 参数可能进入 shell 历史；请按所用 shell 的方式避免或清理历史记录。授权 code 通常只能使用一次，失败时需重新授权。

凭据保存在**所选客户端旁边的** `dida_creds.json`。使用本地 `client_path` 时凭据也留在该客户端旁，不会复制到项目中。

## 使用示例

先查看既有清单，不要自行创建：

```bash
python scripts/dida.py projects
python scripts/dida.py tasks --project "工作"
```

全天任务与定时任务：

```bash
python scripts/dida.py add "提交季度报告" --project "工作" --due "2026-11-05"
python scripts/dida.py add "季度评审会议" --project "科研" --due "2026-11-05T15:30:00+0800" --priority 3
```

修改、完成和删除（以下 ID 都是占位符）：

```bash
python scripts/dida.py update --task TASK_ID --due "2026-11-05T16:00:00+0800"
python scripts/dida.py complete --project "工作" --task TASK_ID
python scripts/dida.py delete --project "工作" --task TASK_ID
```

优先级仅使用 `0`（无）、`1`（低）、`3`（中）、`5`（高）。每次写入后用 `tasks --project` 回读标题、清单和日期，不要只凭命令成功就汇报完成。

### 收件箱发现

官方 `/project` 列表不包含 Inbox。确有需要时：

```bash
python scripts/dida.py discover --save-inbox
```

该命令会真实创建一个临时探测任务、保存其 project ID，然后尝试删除；不是只读操作。运行前应告知用户，删除失败时需要手动清理。

## Open API 限制

- `/project/{id}/data` 只含未完成任务，不能作为完整任务历史。
- 在本项目已验证环境中，`/completed` 返回 404；不要把它当作可用的已完成任务接口。
- `tasks --completed` 只是过滤当前 `/data` 响应，因此不能恢复或列出已完成历史；`tasks --all` 也不改变这一限制。
- 收件箱不在 `/project` 返回中，只能按上述方式发现。
- 收件箱任务不能直接 update，只能记录字段、经确认后删除重建并回读。
- 官方 Open API 的能力不等于网页版或私有 API。本项目不实现私有同步、Cookie 登录或已完成历史查询。

## 安全

- `dida_creds.json` 含 client secret、access token，可能还含 refresh token 和 inbox ID；绝不要读取到聊天、复制进仓库或上传。
- 随附客户端的 `scripts/dida_creds.json`、根目录 `dida_creds.json`、`config.local.json` 和 OAuth 调试日志均已 gitignore。
- `auth_debug.log` 可能含回调元数据，只保留在本机，不要发布。
- CLI 的 projects/tasks 输出可能包含真实 project/task ID。公开 issue、日志、截图和测试结果中只使用占位符。
- 生产命令会修改真实账户。删除、收件箱删后重建及临时探测都需要明确告知；测试一律离线并 mock 网络边界。
- 发布前同时检查跟踪文件和未跟踪文件，确认没有凭据、本机路径或真实 ID；`.gitignore` 不能替代人工审查。

## 测试与静态检查

在项目根目录运行：

```bash
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
```

测试覆盖 `_norm_due`、`_code_from`、全天 add payload、定时 update payload 等既有行为。所有 API 边界均被 mock，不读取凭据、不访问网络。

`scripts/dida.py` 以既有生产客户端为基线，并增加 OAuth state/回调路径校验、日志脱敏与显式时区支持。任何后续行为修改或新增都必须先写失败测试，确认失败原因正确后再实现。

## 项目结构

```text
dida365-tasks/
├── SKILL.md
├── README.md
├── LICENSE
├── .gitignore
├── config.local.json.example
├── scripts/
│   └── dida.py
└── tests/
    └── test_dida.py
```

## 参考来源

- [Dida365 Open API](https://developer.dida365.com/docs/openapi.md) 与 [TickTick Open API](https://developer.ticktick.com/docs#/openapi)：官方端点和 OAuth2 流程。
- [Hermes Agent Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)：SKILL.md、安装与支持文件规则。
- [StoneLL1/dida-task-skill](https://github.com/StoneLL1/dida-task-skill)：参考其 MCP 任务路由、去重等工作流思路；本项目未照搬其实现，也不依赖 MCP。
- [oymy/dida365-ai-tools](https://github.com/oymy/dida365-ai-tools)：仅作方案对比；其私有 API + Cookie 路线未被采用。

## License

[MIT](LICENSE) © 2026 Chenwei Shang
