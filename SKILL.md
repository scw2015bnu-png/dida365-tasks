---
name: dida365-tasks
description: Create, read, update, complete, and delete tasks via OAuth2.
version: 0.1.0
author: Chenwei Shang, Hermes Agent
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [Dida365, TickTick, Tasks, OAuth2]
    related_skills: []
---

# Dida365 Tasks Skill

通过官方 Open API 和 OAuth2 管理滴答清单（CN）与 TickTick（Intl）任务。使用随附的 Python 标准库客户端，不依赖 MCP、私有 API 或 Cookie；写入后必须回读确认。

## When to Use

- 用户要求查看、新增、修改、完成或删除滴答清单 / TickTick 任务。
- 用户把通知或自然语言整理成任务，并指定日期、时刻或清单。
- 用户需要在“工作”“科研”“个人”或既有收件箱之间路由任务。

Don't use for:

- 需要已完成历史、清单创建、批量同步或其他私有 API 能力的请求。
- 用户尚未授权且要求 agent 索取 client secret、token 或 Cookie 的场景。

## Prerequisites

- Windows；Python 3.10+。本版本只在 Windows 11 和 Python 3.14 上实测。
- 用户已在本机受信终端完成 OAuth 配置；不要在聊天中索取或复述密钥。
- 所选客户端旁的 `dida_creds.json` 由客户端自行读写。不要用 `read_file` 查看、复制或打印它。
- 需要写操作时，通过 `terminal` 调用客户端；不要自行构造 curl 请求替代 OAuth 流程。

## How to Run

1. 以当前 `SKILL.md` 所在目录为 `SKILL_ROOT`。用 `search_files(target="files")` 检查根目录是否存在 `config.local.json`；存在时用 `read_file` 读取。
2. 客户端选择顺序固定：非空 `config.local.json.client_path` 优先，否则使用 `scripts/dida.py`。相对路径基于 `SKILL_ROOT` 解析。该优先级让本机继续沿用既有生产客户端和凭据，公开仓库不保存本机路径。
3. 将选定路径记为 `<CLIENT>`。先用 `search_files(target="files")` 或 `terminal` 的只读检查确认文件存在，再执行命令。
4. `default_project` 是用户未指定清单时的默认值，缺省为“收件箱”，以保持官方 Open API 的默认写入行为；`timezone` 缺省为 `Asia/Shanghai`。配置由 agent 读取，`scripts/dida.py` 本身不解析该文件；执行 add/update 时将 `timezone` 显式传为 `--timezone`。
5. 所有调用都通过 `terminal(command="python ...", timeout=60)`，并正确引用参数。OAuth 命令超时按授权等待时间调整。

## Quick Reference

以下 `<CLIENT>` 必须替换为已解析的客户端路径；示例 ID 均为占位符。

```python
terminal(command='python "<CLIENT>" projects', timeout=60)
terminal(command='python "<CLIENT>" tasks --project "工作"', timeout=60)
terminal(command='python "<CLIENT>" add "原始标题" --project "工作" --due "2026-11-05"', timeout=60)
terminal(command='python "<CLIENT>" add "原始标题" --project "科研" --due "2026-11-05T15:30:00+0800"', timeout=60)
terminal(command='python "<CLIENT>" update --task "TASK_ID" --due "2026-11-05T15:30:00+0800"', timeout=60)
terminal(command='python "<CLIENT>" complete --project "工作" --task "TASK_ID"', timeout=60)
terminal(command='python "<CLIENT>" delete --project "工作" --task "TASK_ID"', timeout=60)
```

## Procedure

1. **提取但不改写。** 保留用户给出的标题、备注、标点和链接原文；把日期、时刻、清单和优先级放入对应参数，不把解析结果拼回标题。完成标准：待写入字段可与用户原文逐项对应。
2. **解析时间。** 相对日期先通过 `terminal` 获取当前日期，再结合 `timezone` 计算，不凭记忆换算。只有 `YYYY-MM-DD` 时按全天任务处理；有具体时刻时生成带 UTC offset 的完整 ISO 8601 时间。完成标准：日期与时刻没有混用。
3. **路由既有清单。** 用户未指定时使用 `default_project`。约定清单为“工作”“科研”“个人”，也可按用户明确要求使用既有收件箱；不得擅自创建新清单。目标不存在时停止并询问，不改成相似名称。完成标准：写入前已确定真实目标。
4. **写前读取。** 用 `tasks --project` 读取目标清单的未完成任务，确认新增是否已有同名任务，或确认 update/complete/delete 的唯一对象。匹配不唯一时先询问。收件箱尚未发现时，说明 `discover --save-inbox` 会创建并删除一个临时探测任务，获得同意后再运行。
5. **新增。** 日期无时刻时传 `--due YYYY-MM-DD`，客户端会写入 `isAllDay: true`；带时刻时传完整 ISO 时间，不设置全天标记。未明确提供日期的任务不要编造 `--due`。
6. **修改。** 非收件箱任务使用 `update`；带时刻的日期更新会写入 `isAllDay: false`，纯日期更新会写入 `isAllDay: true`。不要覆盖用户未要求修改的字段。
7. **处理收件箱修改。** 收件箱任务不能调用 `update`。先回读并记录可保留字段，向用户说明“删除后重建”并获得确认，再 delete、add 和回读。无法完整保留必要字段时停止，不冒险丢数据。
8. **完成或删除。** 仅对用户确认的唯一任务执行；删除前明确其不可逆。完成标准：命令成功且下一步回读符合预期。

## Pitfalls

- `/project` 不列出收件箱；`discover --save-inbox` 的临时任务探测有真实写入副作用。
- Open API 的 `/project/{id}/data` 只返回未完成任务；已验证环境中 `/completed` 返回 404。`tasks --completed` 只是过滤当前响应，不能提供已完成历史。
- 收件箱任务不能 update，只能经确认后删后重建。
- `_norm_due` 默认把纯日期规范化为 `Asia/Shanghai` 午夜，保持既有生产行为。显式传入 `--timezone` 时按对应 IANA 时区计算当地午夜并写入 `timeZone`；其他时区尤其要回读全天任务。定时任务应传带自身 offset 的完整 ISO 时间。
- `tasks --all` 不代表已完成历史；当前客户端默认就是列出可获取的未完成任务。
- 命令输出可能包含真实 project/task ID。对用户只汇报名称、标题和日期，不在公开日志、issue 或截图中泄露 token、密钥或 ID。
- `auth_debug.log` 和 `dida_creds.json` 都是本地文件；不要读取、上传或纳入提交。

## Verification

- 每次 add/update/删后重建后，立即用 `terminal` 执行对应的 `tasks --project` 回读，核对标题、清单和日期显示；纯日期应只显示日期，定时任务应显示日期和时刻。
- complete/delete 后确认目标任务不再出现在未完成任务响应中；命令返回成功本身不足以证明结果。
- 不向用户展示原始凭据文件、token 或不必要的数据库 ID。回读失败时如实报告，不把写入成功说成已验证。
- 修改客户端行为前，先添加会失败的离线测试并确认失败，再实现；不得用联网测试代替单元测试。

## Development Checks

```python
terminal(command="python -m unittest discover -s tests -v", timeout=120)
terminal(command="python -m compileall -q scripts tests", timeout=120)
```

完成标准：全部离线测试通过、语法检查无输出且退出码为 0，并确认 `config.local.json`、凭据、调试日志和真实 ID 未进入公开文件。
