import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import mock_open, patch


ROOT = Path(__file__).resolve().parents[1]
CLIENT_PATH = ROOT / "scripts" / "dida.py"
SPEC = importlib.util.spec_from_file_location("dida365_client", CLIENT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load client from {CLIENT_PATH}")
dida = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dida
SPEC.loader.exec_module(dida)


class OAuthCallbackTests(unittest.TestCase):
    expected_state = "01" * 8

    def run_auth(self, callback_paths, log_open=None):
        log_open = log_open or mock_open()
        clock = [0.0]
        creds = {
            "client_id": "example-client",
            "client_secret": "example-secret",
            "region": "cn",
            "redirect_uri": dida.DEFAULT_REDIRECT,
        }
        args = SimpleNamespace(
            redirect_uri=None,
            code=None,
            manual=False,
            wait=5,
        )
        requests = []

        class FakeServer:
            def __init__(self, _address, handler_class):
                self.handler_class = handler_class
                self.timeout = None

            def handle_request(self):
                if not callback_paths:
                    clock[0] += 5
                    return
                path = callback_paths.pop(0)
                handler = object.__new__(self.handler_class)
                handler.path = path
                handler.server = self
                handler.wfile = io.BytesIO()
                handler.response_status = None
                handler.send_response = lambda status: setattr(
                    handler, "response_status", status
                )
                handler.send_header = lambda *_args: None
                handler.end_headers = lambda: None
                requests.append(handler)
                handler.do_GET()

            def shutdown(self):
                pass

        class ImmediateThread:
            def __init__(self, target, daemon=None):
                self.target = target

            def start(self):
                self.target()

        with (
            patch.object(dida, "load_creds", return_value=creds),
            patch.object(dida, "HTTPServer", FakeServer),
            patch.object(dida.os, "urandom", return_value=b"\x01" * 8),
            patch.object(dida, "_open"),
            patch.object(dida, "_exchange") as exchange_mock,
            patch.object(dida.threading, "Thread", ImmediateThread),
            patch.object(
                dida,
                "time",
                SimpleNamespace(monotonic=lambda: clock[0]),
                create=True,
            ),
            patch("builtins.open", log_open),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            exit_error = None
            try:
                dida.cmd_auth(args)
            except SystemExit as exc:
                exit_error = exc
        return requests, exchange_mock, exit_error

    def test_callback_with_wrong_state_is_rejected(self):
        requests, exchange_mock, exit_error = self.run_auth(
            ["/callback?state=wrong&code=must-not-be-used"]
        )
        self.assertIsNotNone(exit_error)
        self.assertEqual(requests[0].response_status, 400)
        exchange_mock.assert_not_called()

    def test_wrong_callback_path_is_rejected(self):
        requests, exchange_mock, exit_error = self.run_auth(
            [f"/not-callback?state={self.expected_state}&code=must-not-be-used"]
        )
        self.assertIsNotNone(exit_error)
        self.assertEqual(requests[0].response_status, 404)
        exchange_mock.assert_not_called()

    def test_auth_log_excludes_raw_query_and_percent_encoded_code(self):
        log_open = mock_open()
        requests, exchange_mock, exit_error = self.run_auth(
            [f"/callback?state={self.expected_state}&code=ab%2Bcd&scope=tasks"],
            log_open=log_open,
        )
        self.assertIsNone(exit_error)
        exchange_mock.assert_called_once()
        self.assertEqual(exchange_mock.call_args.args[2], "ab+cd")
        log_text = "".join(
            call.args[0] for call in log_open.return_value.write.call_args_list
        )
        self.assertIn("path=/callback", log_text)
        self.assertIn("has_code=True", log_text)
        self.assertNotIn("query=", log_text)
        self.assertNotIn("state=", log_text)
        self.assertNotIn("ab%2Bcd", log_text)
        self.assertNotIn("ab+cd", log_text)
        self.assertEqual(requests[0].response_status, 200)

    def test_waits_through_probe_until_valid_callback(self):
        requests, exchange_mock, exit_error = self.run_auth(
            [
                "/favicon.ico",
                f"/callback?state={self.expected_state}&code=valid-code",
            ]
        )
        self.assertIsNone(exit_error)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].response_status, 404)
        self.assertEqual(requests[1].response_status, 200)
        self.assertEqual(exchange_mock.call_args.args[2], "valid-code")


class SetAppTests(unittest.TestCase):
    def test_tokens_are_retained_only_for_identical_app_config(self):
        original = {
            "client_id": "example-client",
            "client_secret": "example-secret",
            "region": "cn",
            "redirect_uri": dida.DEFAULT_REDIRECT,
            "access_token": "old-access-placeholder",
            "refresh_token": "old-refresh-placeholder",
        }
        changes = {
            "client_id": "different-client",
            "client_secret": "different-secret",
            "region": "intl",
            "redirect_uri": "http://127.0.0.1:9876/other-callback",
        }
        with tempfile.TemporaryDirectory() as tmp:
            creds_path = Path(tmp) / "test-credentials.json"
            with (
                patch.object(dida, "CREDS_PATH", creds_path),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                creds_path.write_text(json.dumps(original), encoding="utf-8")
                dida.cmd_setapp(SimpleNamespace(**{k: original[k] for k in changes}))
                retained = json.loads(creds_path.read_text(encoding="utf-8"))
                self.assertEqual(retained["access_token"], original["access_token"])
                self.assertEqual(retained["refresh_token"], original["refresh_token"])

                for field, value in changes.items():
                    with self.subTest(changed_field=field):
                        creds_path.write_text(json.dumps(original), encoding="utf-8")
                        app = {k: original[k] for k in changes}
                        app[field] = value
                        dida.cmd_setapp(SimpleNamespace(**app))
                        replaced = json.loads(creds_path.read_text(encoding="utf-8"))
                        self.assertNotIn("access_token", replaced)
                        self.assertNotIn("refresh_token", replaced)


class NormDueTests(unittest.TestCase):
    def test_empty_due_is_none(self):
        self.assertIsNone(dida._norm_due(None))
        self.assertIsNone(dida._norm_due(""))

    def test_date_only_is_normalized_to_all_day_boundary(self):
        self.assertEqual(
            dida._norm_due("  2026-11-05  "),
            "2026-11-05T00:00:00+0800",
        )

    def test_full_datetime_is_preserved(self):
        due = "2026-11-05T15:30:00+0800"
        self.assertEqual(dida._norm_due(due), due)


class CodeFromTests(unittest.TestCase):
    def test_bare_code_is_preserved(self):
        self.assertEqual(dida._code_from("  abc+123  "), "abc+123")

    def test_callback_url_preserves_plus_and_decodes_percent_escapes(self):
        url = "http://127.0.0.1:8765/callback?state=x&code=ab+cd%2Bef&scope=tasks"
        self.assertEqual(dida._code_from(url), "ab+cd+ef")

    def test_query_after_question_mark_is_supported(self):
        self.assertEqual(dida._code_from("callback?code=xyz%2B123&state=x"), "xyz+123")


class PayloadTests(unittest.TestCase):
    def test_add_date_only_uses_requested_timezone(self):
        args = SimpleNamespace(
            title="跨时区全天任务",
            project=None,
            content=None,
            due="2026-07-01",
            priority=0,
            tags=None,
            timezone="America/New_York",
        )
        creds = {"region": "cn"}
        with (
            patch.object(dida, "load_creds", return_value=creds),
            patch.object(
                dida,
                "api",
                return_value={"id": "example-task", "title": args.title},
            ) as api_mock,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            dida.cmd_add(args)

        self.assertEqual(
            api_mock.call_args.kwargs["payload"],
            {
                "title": args.title,
                "priority": 0,
                "dueDate": "2026-07-01T00:00:00-0400",
                "isAllDay": True,
                "timeZone": "America/New_York",
            },
        )

    def test_add_date_only_builds_all_day_payload_and_preserves_title(self):
        title = "原文：提交《季度报告》 + 复核"
        args = SimpleNamespace(
            title=title,
            project=None,
            content=None,
            due="2026-11-05",
            priority=0,
            tags=None,
        )
        creds = {"region": "cn"}
        with (
            patch.object(dida, "load_creds", return_value=creds),
            patch.object(
                dida,
                "api",
                return_value={"id": "example-task", "title": title},
            ) as api_mock,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            dida.cmd_add(args)

        api_mock.assert_called_once_with(
            creds,
            "/task",
            method="POST",
            payload={
                "title": title,
                "priority": 0,
                "dueDate": "2026-11-05T00:00:00+0800",
                "isAllDay": True,
            },
        )

    def test_add_datetime_does_not_mark_task_all_day(self):
        args = SimpleNamespace(
            title="定时会议",
            project=None,
            content=None,
            due="2026-11-05T15:30:00+0800",
            priority=3,
            tags=None,
        )
        creds = {"region": "cn"}
        with (
            patch.object(dida, "load_creds", return_value=creds),
            patch.object(
                dida,
                "api",
                return_value={"id": "example-task", "title": "定时会议"},
            ) as api_mock,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            dida.cmd_add(args)

        payload = api_mock.call_args.kwargs["payload"]
        self.assertEqual(payload["dueDate"], "2026-11-05T15:30:00+0800")
        self.assertNotIn("isAllDay", payload)

    def test_update_datetime_builds_timed_payload(self):
        args = SimpleNamespace(
            task="example-task",
            title=None,
            content=None,
            due="2026-11-05T15:30:00+0800",
            priority=None,
            project=None,
        )
        creds = {"region": "cn"}
        with (
            patch.object(dida, "load_creds", return_value=creds),
            patch.object(dida, "api", return_value={}) as api_mock,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            dida.cmd_update(args)

        api_mock.assert_called_once_with(
            creds,
            "/task/example-task",
            method="POST",
            payload={
                "id": "example-task",
                "dueDate": "2026-11-05T15:30:00+0800",
                "isAllDay": False,
            },
        )

    def test_update_date_only_builds_all_day_payload(self):
        args = SimpleNamespace(
            task="example-task",
            title=None,
            content=None,
            due="2026-11-05",
            priority=None,
            project=None,
        )
        creds = {"region": "cn"}
        with (
            patch.object(dida, "load_creds", return_value=creds),
            patch.object(dida, "api", return_value={}) as api_mock,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            dida.cmd_update(args)

        self.assertEqual(
            api_mock.call_args.kwargs["payload"],
            {
                "id": "example-task",
                "dueDate": "2026-11-05T00:00:00+0800",
                "isAllDay": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
