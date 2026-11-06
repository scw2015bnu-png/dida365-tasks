#!/usr/bin/env python3
"""滴答清单 / TickTick 命令行客户端（官方 Open API，OAuth2）。

为什么存在：Hermes 没有内置滴答清单连接器。这个脚本用官方 Open API
（tasks:read / tasks:write）做双向同步，供 Hermes 通过 terminal 调用。

区域：
  cn   = 国内版 滴答清单  (dida365.com  / api.dida365.com)
  intl = 国际版 TickTick   (ticktick.com  / api.ticktick.com)

用法：
  python dida.py setapp --client-id ID --client-secret SECRET [--region cn]
  python dida.py auth                     # 打印授权链接，等本地回调，换 token
  python dida.py auth --manual            # 不用本地回调，粘贴浏览器回跳的 code/URL
  python dida.py status
  python dida.py projects
  python dida.py tasks [--project <id|名字>] [--all] [--completed]
  python dida.py add "标题" [--project <id|名字>] [--due YYYY-MM-DD] [--priority 0-5] [--content 备注] [--tags a,b]
  python dida.py complete --project <id|名字> --task <任务id>
  python dida.py update --task <id> [--title ...] [--due ...] [--priority ...]

凭证落盘：本目录 dida_creds.json（含 client_secret 与 access_token）。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

HOSTS = {
    "cn": {"web": "https://dida365.com", "api": "https://api.dida365.com/open/v1", "label": "滴答清单（国内版）"},
    "intl": {"web": "https://ticktick.com", "api": "https://api.ticktick.com/open/v1", "label": "TickTick（国际版）"},
}
SCOPES = "tasks:write tasks:read"
CREDS_PATH = Path(__file__).with_name("dida_creds.json")
DEFAULT_REDIRECT = "http://127.0.0.1:8765/callback"


def die(msg: str, code: int = 1):
    print(f"错误：{msg}", file=sys.stderr)
    sys.exit(code)


def load_creds() -> dict:
    if not CREDS_PATH.exists():
        die(f"还没有配置应用凭据。先运行：python {Path(__file__).name} setapp --client-id ... --client-secret ...")
    return json.loads(CREDS_PATH.read_text(encoding="utf-8"))


def save_creds(c: dict) -> None:
    CREDS_PATH.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(CREDS_PATH, 0o600)
    except OSError:
        pass


def hosts(creds: dict) -> dict:
    return HOSTS[creds.get("region", "cn")]


def http_json(url: str, *, method: str = "GET", data: bytes | None = None,
              headers: dict | None = None, form: dict | None = None):
    hdrs = dict(headers or {})
    body = data
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {detail}") from exc


def _code_from(text: str) -> str:
    """从整条回跳 URL 或裸 code 里取 code，保留 '+' 不转成空格。"""
    text = text.strip()
    if "code=" not in text:
        return text
    q = urllib.parse.urlsplit(text).query if "://" in text else text.partition("?")[2]
    for kv in q.split("&"):
        if kv.startswith("code="):
            return urllib.parse.unquote(kv[5:])
    return ""


def refresh_token(creds: dict) -> dict:
    rt = creds.get("refresh_token")
    if not rt:
        die("access_token 已失效且没有 refresh_token，重新运行 auth 授权。")
    h = hosts(creds)
    form = {"grant_type": "refresh_token", "refresh_token": rt}
    headers = {}
    if creds.get("region", "cn") == "cn":
        form.update({"client_id": creds["client_id"], "client_secret": creds["client_secret"]})
    else:
        basic = base64.b64encode(f"{creds['client_id']}:{creds['client_secret']}".encode()).decode()
        headers = {"Authorization": f"Basic {basic}"}
    try:
        tok = http_json(f"{h['web']}/oauth/token", method="POST", form=form, headers=headers)
    except RuntimeError as exc:
        die(f"refresh 失败（{exc}）；重新运行 auth 授权。")
    creds.update({k: tok[k] for k in ("access_token", "refresh_token") if tok.get(k)})
    save_creds(creds)
    return creds


def api(creds: dict, path: str, *, method: str = "GET", payload: dict | None = None):
    h = hosts(creds)
    for attempt in (1, 2):
        headers = {"Authorization": f"Bearer {creds['access_token']}"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        try:
            return http_json(f"{h['api']}{path}", method=method, data=data, headers=headers)
        except RuntimeError as exc:
            if "HTTP 401" in str(exc) and attempt == 1:
                refresh_token(creds)
                continue
            raise


# ── commands ────────────────────────────────────────────────────────────────
def cmd_setapp(args):
    creds = {"client_id": args.client_id, "client_secret": args.client_secret,
             "region": args.region, "redirect_uri": args.redirect_uri}
    if CREDS_PATH.exists():
        previous = load_creds()
        app_keys = ("client_id", "client_secret", "region", "redirect_uri")
        if all(previous.get(k) == creds[k] for k in app_keys):
            creds.update({k: v for k, v in previous.items()
                          if k in ("access_token", "refresh_token")})
    save_creds(creds)
    h = hosts(creds)
    print(f"已保存应用凭据（{h['label']}，redirect_uri={args.redirect_uri}）。"
          f"\n把同样的 redirect_uri 填进开发者控制台，然后运行 auth。")


def cmd_auth(args):
    creds = load_creds()
    h = hosts(creds)
    redirect = args.redirect_uri or creds.get("redirect_uri") or DEFAULT_REDIRECT
    params = {"client_id": creds["client_id"], "scope": SCOPES, "response_type": "code",
              "redirect_uri": redirect, "state": os.urandom(8).hex()}
    url = f"{h['web']}/oauth/authorize?" + urllib.parse.urlencode(params)

    if args.code:
        code = _code_from(args.code)
        if not code:
            die("没拿到 code。")
        _exchange(creds, h, code, redirect)
        return

    if args.manual:
        print("1) 在浏览器打开这个链接并同意授权：\n")
        print(url)
        print("\n2) 授权后浏览器会跳到 redirect_uri（页面可能打不开），把地址栏完整 URL 或 code 粘贴回来：")
        raw = input("> ").strip()
        code = _code_from(raw)
        if not code:
            die("没拿到 code。")
        _exchange(creds, h, code, redirect)
        return

    result: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed_url = urllib.parse.urlsplit(self.path)
            expected_path = urllib.parse.urlsplit(redirect).path or "/"
            if parsed_url.path != expected_path:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            raw_q = parsed_url.query
            # 手工解析，不用 parse_qs：parse_qs 会把 code 里的 '+' 变成空格，
            # 那样换 token 必然 401（滴答的 code 可能含 '+'）。
            parts = {}
            for kv in raw_q.split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    parts[k] = urllib.parse.unquote(v)
            code = parts.get("code", "")
            err = parts.get("error", "")
            print(f"[callback] path={urllib.parse.urlparse(self.path).path} "
                  f"has_code={bool(code)} code_len={len(code)} "
                  f"code_prefix={code[:2]!r} code_suffix={code[-2:]!r} "
                  f"code_alnum={code.isalnum()} code_has_plus={'+' in code} "
                  f"code_has_space={' ' in code}", flush=True)
            try:
                with open(Path(__file__).with_name("auth_debug.log"), "a", encoding="utf-8") as fh:
                    fh.write(f"{datetime.now().isoformat(timespec='seconds')} path={parsed_url.path} "
                             f"has_code={bool(code)} has_error={bool(err)} code_len={len(code)}\n")
            except Exception:
                pass
            if parts.get("state", "") != params["state"]:
                self.send_response(400)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not code and not err:
                # favicon / probes must not end the wait
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            result["code"] = code
            result["error"] = err
            ok = bool(code)
            body = ("<h2>授权完成，可以关掉这个页面回终端了。</h2>" if ok
                    else f"<h2>授权失败：{err}</h2>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *a):  # silence
            pass

    port = int(urllib.parse.urlparse(redirect).port or 8765)
    listen_host = urllib.parse.urlparse(redirect).hostname or "127.0.0.1"
    server = HTTPServer((listen_host, port), Handler)
    print(f"等待浏览器回调 {redirect} （{args.wait} 秒）…\n如果浏览器没弹出，手动打开：\n{url}\n")
    threading.Thread(target=lambda: _open(url), daemon=True).start()
    deadline = time.monotonic() + max(0, args.wait)
    while not result and time.monotonic() < deadline:
        server.timeout = max(0, deadline - time.monotonic())
        server.handle_request()
    if not result.get("code"):
        die("没收到授权 code（超时或被拒绝）。可改用 --manual 粘贴。")
    _exchange(creds, h, result["code"], redirect)


def _open(url: str) -> None:
    try:
        os.startfile(url)  # Windows
    except Exception:
        print(url)


def _exchange(creds: dict, h: dict, code: str, redirect: str) -> None:
    """换 token。认证方式按区域固定：国内版 dida365 用 POST body 传客户端凭据，
    国际版 ticktick 用 HTTP Basic。一次失败的尝试会废掉 code，所以不能乱试。"""
    form = {"code": code, "grant_type": "authorization_code", "scope": SCOPES,
            "redirect_uri": redirect}
    region = creds.get("region", "cn")
    if region == "cn":
        form = dict(form, client_id=creds["client_id"], client_secret=creds["client_secret"])
        headers = {}
    else:
        basic = base64.b64encode(f"{creds['client_id']}:{creds['client_secret']}".encode()).decode()
        headers = {"Authorization": f"Basic {basic}"}
    try:
        tok = http_json(f"{h['web']}/oauth/token", method="POST", form=form, headers=headers)
    except RuntimeError as exc:
        die(f"换 token 失败（{exc}）。这次的 code 已作废，需要重新授权一次。")
    if not tok.get("access_token"):
        die(f"换 token 失败：{json.dumps(tok)[:300]}")
    creds["access_token"] = tok["access_token"]
    if tok.get("refresh_token"):
        creds["refresh_token"] = tok["refresh_token"]
    creds["redirect_uri"] = redirect
    creds["authed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_creds(creds)
    print("授权成功，access_token 已保存。")


def cmd_status(_args):
    creds = load_creds()
    h = hosts(creds)
    print(f"区域: {h['label']}")
    print(f"client_id: {creds['client_id'][:6]}…")
    print(f"授权时间: {creds.get('authed_at', '未授权')}")
    print(f"有 refresh_token: {bool(creds.get('refresh_token'))}")
    if creds.get("access_token"):
        try:
            projects = api(creds, "/project")
            print(f"API 可用，项目数: {len(projects)}")
        except RuntimeError as exc:
            print(f"API 调用失败: {exc}")


def cmd_projects(_args):
    creds = load_creds()
    rows = api(creds, "/project") or []
    for p in rows:
        print(f"{p.get('id')}  {p.get('name')}")


def _resolve_project(creds: dict, ref: str | None) -> str:
    if ref in ("inbox", "收件箱", "收集箱"):
        inbox = creds.get("inbox_id")
        if not inbox:
            die("还不知道收件箱 id，先运行：python dida.py discover --save-inbox")
        return inbox
    rows = api(creds, "/project") or []
    if not ref:
        if len(rows) == 1:
            return rows[0]["id"]
        inbox = creds.get("inbox_id")
        if inbox and not rows:
            return inbox
        names = ", ".join(p.get("name", "?") for p in rows) or "（无项目）"
        die(f"有多个项目，请用 --project 指定（可选：{names}）")
    for p in rows:
        if ref in (p.get("id"), p.get("name")):
            return p["id"]
    die(f"找不到项目 '{ref}'。先跑 projects 看列表。")


def cmd_discover(args):
    """建一个临时任务来拿到收件箱 id（Open API 不列出 Inbox），然后删掉它。"""
    creds = load_creds()
    tmp = api(creds, "/task", method="POST",
              payload={"title": "__hermes_inbox_probe__"})
    pid = tmp.get("projectId")
    tid = tmp.get("id")
    if pid and args.save_inbox:
        creds["inbox_id"] = pid
        save_creds(creds)
    if tid and pid:
        try:
            api(creds, f"/project/{pid}/task/{tid}", method="DELETE")
        except RuntimeError as exc:
            print(f"（临时任务 {tid} 删除失败，请手动删：{exc}）")
    print(f"收件箱 projectId = {pid}")
    if args.save_inbox:
        print("已保存到凭据文件，之后 tasks/add 不带 --project 就默认收件箱。")


def cmd_tasks(args):
    creds = load_creds()
    if args.project:
        pid = _resolve_project(creds, args.project)
        targets = [(pid, None)]
    else:
        targets = [(p["id"], p.get("name")) for p in (api(creds, "/project") or [])]
        inbox = creds.get("inbox_id")
        if inbox:
            targets.insert(0, (inbox, "收件箱"))
    for pid, name in targets:
        data = api(creds, f"/project/{pid}/data") or {}
        proj_name = name or (data.get("project") or {}).get("name", pid)
        for t in data.get("tasks", []):
            if t.get("status") == 2 and not args.completed:
                continue
            if t.get("status") != 2 and args.completed:
                continue
            mark = "x" if t.get("status") == 2 else " "
            due_raw = t.get("dueDate") or ""
            due = ""
            if due_raw:
                try:
                    from datetime import datetime as _dt
                    _d = _dt.fromisoformat(due_raw).astimezone()
                    due = _d.strftime("%Y-%m-%d") if t.get("isAllDay") else _d.strftime("%Y-%m-%d %H:%M")
                except ValueError:
                    due = due_raw[:16]
            pri = t.get("priority", 0)
            print(f"[{mark}] {proj_name} | {due:16} | p{pri} | {t.get('id')} | {t.get('title')}")


def _norm_due(due: str | None, timezone_name: str | None = None) -> str | None:
    if not due:
        return None
    d = due.strip()
    if len(d) == 10:
        # Date-only means an all-day task at local midnight. Keep Asia/Shanghai
        # as the backwards-compatible default, but allow explicit IANA zones.
        zone = ZoneInfo(timezone_name or "Asia/Shanghai")
        return datetime.fromisoformat(d).replace(tzinfo=zone).strftime("%Y-%m-%dT%H:%M:%S%z")
    return d


def cmd_add(args):
    creds = load_creds()
    payload = {"title": args.title, "priority": args.priority}
    if args.project:
        payload["projectId"] = _resolve_project(creds, args.project)
    if args.content:
        payload["content"] = args.content
    if args.due:
        timezone_name = getattr(args, "timezone", None)
        payload["dueDate"] = _norm_due(args.due, timezone_name)
        if len(args.due.strip()) == 10:
            payload["isAllDay"] = True   # 只给日期 = 那天结束前完成（全天任务）
        if timezone_name:
            payload["timeZone"] = timezone_name
    if args.tags:
        payload["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
    task = api(creds, "/task", method="POST", payload=payload)
    print(f"已创建: {task.get('id')}  {task.get('title')}")


def cmd_complete(args):
    creds = load_creds()
    pid = _resolve_project(creds, args.project)
    api(creds, f"/project/{pid}/task/{args.task}/complete", method="POST")
    print(f"已完成: {args.task}")


def cmd_delete(args):
    creds = load_creds()
    pid = _resolve_project(creds, args.project or "inbox")
    api(creds, f"/project/{pid}/task/{args.task}", method="DELETE")
    print(f"已删除: {args.task}")


def cmd_update(args):
    creds = load_creds()
    payload = {"id": args.task}
    if args.title:
        payload["title"] = args.title
    if args.content:
        payload["content"] = args.content
    if args.due:
        timezone_name = getattr(args, "timezone", None)
        payload["dueDate"] = _norm_due(args.due, timezone_name)
        payload["isAllDay"] = len(args.due.strip()) == 10
        if timezone_name:
            payload["timeZone"] = timezone_name
    if args.priority is not None:
        payload["priority"] = args.priority
    if args.project:
        payload["projectId"] = _resolve_project(creds, args.project)
    api(creds, f"/task/{args.task}", method="POST", payload=payload)
    print(f"已更新: {args.task}")


def main() -> int:
    ap = argparse.ArgumentParser(description="滴答清单 / TickTick Open API 客户端")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("setapp", help="保存 client_id / client_secret")
    p.add_argument("--client-id", required=True)
    p.add_argument("--client-secret", required=True)
    p.add_argument("--region", choices=["cn", "intl"], default="cn")
    p.add_argument("--redirect-uri", default=DEFAULT_REDIRECT)
    p.set_defaults(func=cmd_setapp)

    p = sub.add_parser("auth", help="OAuth 授权，换 access_token")
    p.add_argument("--redirect-uri", default=None)
    p.add_argument("--manual", action="store_true", help="粘贴回跳 URL 而不是本地回调")
    p.add_argument("--code", default=None, help="直接给 code 或整条回跳 URL，跳过监听")
    p.add_argument("--wait", type=int, default=600, help="监听等待秒数（默认 600）")
    p.set_defaults(func=cmd_auth)

    p = sub.add_parser("status", help="查看授权与连通状态")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("projects", help="列出项目/清单")
    p.set_defaults(func=cmd_projects)

    p = sub.add_parser("tasks", help="列出任务")
    p.add_argument("--project", default=None, help="项目 id 或名称")
    p.add_argument("--completed", action="store_true", help="只看已完成")
    p.add_argument("--all", action="store_true", help="（默认就是全部未完成）")
    p.set_defaults(func=cmd_tasks)

    p = sub.add_parser("add", help="新建任务")
    p.add_argument("title")
    p.add_argument("--project", default=None)
    p.add_argument("--content", default=None)
    p.add_argument("--due", default=None, help="YYYY-MM-DD 或完整 ISO 时间")
    p.add_argument("--timezone", default=None, help="IANA 时区，例如 Asia/Shanghai")
    p.add_argument("--priority", type=int, default=0, help="0=无 1=低 3=中 5=高")
    p.add_argument("--tags", default=None, help="逗号分隔")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("complete", help="完成任务")
    p.add_argument("--project", required=True)
    p.add_argument("--task", required=True)
    p.set_defaults(func=cmd_complete)

    p = sub.add_parser("discover", help="探测收件箱 id 并保存（Inbox 不在 /project 列表里）")
    p.add_argument("--save-inbox", action="store_true")
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("delete", help="删除任务")
    p.add_argument("--task", required=True)
    p.add_argument("--project", default=None, help="默认收件箱")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("update", help="修改任务")
    p.add_argument("--task", required=True)
    p.add_argument("--project", default=None)
    p.add_argument("--title", default=None)
    p.add_argument("--content", default=None)
    p.add_argument("--due", default=None)
    p.add_argument("--timezone", default=None, help="IANA 时区，例如 Asia/Shanghai")
    p.add_argument("--priority", type=int, default=None)
    p.set_defaults(func=cmd_update)

    args = ap.parse_args()
    try:
        args.func(args)
    except RuntimeError as exc:
        die(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
