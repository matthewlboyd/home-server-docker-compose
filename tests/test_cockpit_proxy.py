import base64
from contextlib import redirect_stderr, redirect_stdout
from email.message import Message
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import ssl
import struct
import unittest
from unittest.mock import Mock, patch


PATH = Path(__file__).resolve().parents[1] / "scripts/check-cockpit-proxy"
LOADER = importlib.machinery.SourceFileLoader("check_cockpit_proxy", str(PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
CHECK = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(CHECK)

HOST = "cockpit.example.com"
ADDRESS = "192.0.2.10"
PASSWORD = "PRIVATE-password-with-punctuation:$"
COOKIE = "PRIVATE-session-cookie"
CSRF = "PRIVATE-csrf-token"


def server_frame(payload, opcode=1, final=True):
    first = (0x80 if final else 0) | opcode
    if len(payload) < 126:
        return bytes([first, len(payload)]) + payload
    return bytes([first, 126]) + struct.pack("!H", len(payload)) + payload


def response(status, headers=None, body=b""):
    lines = [f"HTTP/1.1 {status} Test"]
    lines.extend(f"{key}: {value}" for key, value in (headers or {}).items())
    if status != 101:
        lines.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body


class FakeSocket:
    def __init__(self, fixture, port):
        self.fixture = fixture
        self.port = port
        self.payload = b""
        self.closed = False

    def sendall(self, data):
        if data.startswith(b"GET "):
            lines = data.decode("ascii").split("\r\n")
            path = lines[0].split()[1]
            headers = dict(line.split(": ", 1) for line in lines[1:] if line)
            self.fixture.requests.append((self.port, path, headers))
            self.payload = self.fixture.reply(self.port, path, headers)
            return
        # Validate actual client masking, then inspect only our synthetic messages.
        assert data[1] & 0x80
        length = data[1] & 0x7F
        offset = 2
        if length == 126:
            length = struct.unpack("!H", data[2:4])[0]
            offset = 4
        mask = data[offset:offset + 4]
        raw = data[offset + 4:offset + 4 + length]
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(raw))
        opcode = data[0] & 15
        self.fixture.frames.append((opcode, payload))
        if opcode == 1:
            control = json.loads(payload[1:])
            if control["command"] == "logout":
                self.fixture.logged_in = False

    def makefile(self, mode):
        assert mode == "rb"
        return io.BytesIO(self.payload)

    def close(self):
        self.closed = True


class Fixture:
    def __init__(self):
        self.requests = []
        self.frames = []
        self.sockets = []
        self.logged_in = False
        self.bad_origin_allowed = False
        self.bad_forwarded = False
        self.unauthenticated_status = 101
        self.invalid_init = False
        self.insecure_cookie = False

    def connect(self, target, timeout):
        assert target[0] == ADDRESS
        assert timeout == CHECK.TIMEOUT
        sock = FakeSocket(self, target[1])
        self.sockets.append(sock)
        return sock

    def reply(self, port, path, headers):
        assert headers["Host"] == HOST
        if port == 80:
            assert "Authorization" not in headers and "Cookie" not in headers
            return response(301, {"Location": "https://" + HOST + "/"})
        if path == "/ping":
            return response(200, body=b'{"service":"cockpit"}')
        if path == "/":
            return response(200, {"Content-Type": "text/html"}, b"<html>Cockpit login</html>")
        if path == "/cockpit/login":
            if "Authorization" in headers:
                expected = "Basic " + base64.b64encode(("matthew:" + PASSWORD).encode()).decode()
                assert headers["Authorization"] == expected
                assert headers["X-Forwarded-Proto"] == "http"
                assert headers["X-Forwarded-Scheme"] == "http"
                self.logged_in = True
                secure = "" if self.insecure_cookie else "; Secure"
                return response(200, {"Set-Cookie": f"cockpit={COOKIE}; Path=/{secure}; HttpOnly"}, json.dumps({"csrf-token": CSRF}).encode())
            return response(401 if not self.logged_in else 200)
        assert path == "/cockpit/socket"
        if headers["Origin"] != "https://" + HOST and not self.bad_origin_allowed:
            return response(403, body=b"PRIVATE-error-body")
        if self.bad_forwarded and headers.get("X-Forwarded-Proto") == "http":
            return response(302)
        if "Cookie" not in headers and self.unauthenticated_status != 101:
            return response(self.unauthenticated_status)
        accept = base64.b64encode(hashlib.sha1((headers["Sec-WebSocket-Key"] + CHECK.WS_GUID).encode()).digest()).decode()
        init = {"command": "init", "version": 1, "csrf-token": CSRF}
        if "Cookie" in headers:
            assert headers["Cookie"] == "cockpit=" + COOKIE and self.logged_in
            if self.invalid_init:
                init["problem"] = "PRIVATE-problem-message"
        else:
            init["problem"] = "no-session"
        frames = server_frame(b"\n" + json.dumps(init).encode()) + server_frame(struct.pack("!H", 1000), 8)
        return response(101, {"Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Accept": accept, "Sec-WebSocket-Protocol": "cockpit1"}, frames)


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.context = Mock()
        self.context.wrap_socket.side_effect = lambda connection, server_hostname: connection
        self.ssl_patch = patch.object(CHECK.ssl, "create_default_context", return_value=self.context)
        self.connect_patch = patch.object(CHECK.socket, "create_connection", side_effect=self.fixture.connect)
        self.ssl_patch.start()
        self.connect_patch.start()
        self.addCleanup(self.ssl_patch.stop)
        self.addCleanup(self.connect_patch.stop)

    def client(self):
        return CHECK.Client(HOST, ADDRESS)

    def test_anonymous_wire_checks_use_verified_sni_and_do_not_claim_login(self):
        result = CHECK.check(self.client())
        self.assertFalse(result["authenticated_session"])
        self.assertFalse(result["logout_verified"])
        self.assertTrue(result["unlisted_origin_rejected"])
        self.assertTrue(result["spoofed_header_websocket_ok"])
        self.assertNotIn("forwarded_headers_sanitized", result)
        self.context.set_alpn_protocols.assert_called_once_with(["http/1.1"])
        self.assertEqual(len(self.context.wrap_socket.call_args_list), 5)
        self.assertTrue(all(call.kwargs == {"server_hostname": HOST} for call in self.context.wrap_socket.call_args_list))
        self.assertTrue(all(sock.closed for sock in self.fixture.sockets))
        self.assertTrue(all("Authorization" not in headers and "Cookie" not in headers for _, _, headers in self.fixture.requests))

    def test_authentication_uses_cookie_then_only_init_logout_and_checks_revocation(self):
        result = CHECK.check(self.client(), "matthew", PASSWORD)
        self.assertTrue(result["authenticated_session"])
        self.assertTrue(result["logout_verified"])
        controls = [json.loads(payload[1:]) for opcode, payload in self.fixture.frames if opcode == 1]
        self.assertEqual(controls, [{"command": "init", "version": 1}, {"command": "logout"}])
        self.assertFalse(self.fixture.logged_in)
        auth_requests = [request for request in self.fixture.requests if "Authorization" in request[2]]
        self.assertEqual(len(auth_requests), 1)
        self.assertEqual(auth_requests[0][:2], (443, "/cockpit/login"))
        self.assertEqual(self.fixture.requests[-1][2]["Cookie"], "cockpit=" + COOKIE)

    def test_unlisted_origin_must_be_rejected(self):
        self.fixture.bad_origin_allowed = True
        with self.assertRaisesRegex(CHECK.CheckError, "unlisted WebSocket origin"):
            CHECK.check(self.client())

    def test_spoofed_forwarded_headers_must_not_break_upgrade(self):
        self.fixture.bad_forwarded = True
        with self.assertRaisesRegex(CHECK.CheckError, "valid HTTP 101"):
            CHECK.check(self.client())

    def test_anonymous_auth_challenge_is_not_misreported_as_origin_success(self):
        self.fixture.unauthenticated_status = 401
        with self.assertRaisesRegex(CHECK.CheckError, "anonymous origin checks remain unverified"):
            CHECK.check(self.client())

    def test_authenticated_init_problem_fails_and_still_sends_logout(self):
        self.fixture.invalid_init = True
        with self.assertRaisesRegex(CHECK.CheckError, "initialization failed"):
            CHECK.check(self.client(), "matthew", PASSWORD)
        self.assertFalse(self.fixture.logged_in)

    def test_authentication_rejects_a_cookie_without_secure_after_spoofed_headers(self):
        self.fixture.insecure_cookie = True
        with self.assertRaisesRegex(CHECK.CheckError, "Secure session cookie"):
            CHECK.check(self.client(), "matthew", PASSWORD)

    def test_certificate_failure_sends_no_http_requests_and_has_sanitized_output(self):
        self.context.wrap_socket.side_effect = ssl.SSLCertVerificationError("PRIVATE-certificate-error")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = CHECK.main(["--domain", HOST, "--address", ADDRESS])
        self.assertEqual(result, 1)
        self.assertIn("not trusted", stderr.getvalue())
        self.assertNotIn("PRIVATE", stderr.getvalue())
        self.assertEqual(self.fixture.requests, [])
        self.assertTrue(self.fixture.sockets[0].closed)

    def test_password_env_output_never_contains_password_cookie_or_csrf(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"CHECK_COCKPIT_TEST_PASSWORD": PASSWORD}), redirect_stdout(stdout), redirect_stderr(stderr):
            result = CHECK.main(["--domain", HOST, "--address", ADDRESS, "--username", "matthew", "--password-env", "CHECK_COCKPIT_TEST_PASSWORD"])
            self.assertNotIn("CHECK_COCKPIT_TEST_PASSWORD", os.environ)
        self.assertEqual(result, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertTrue(json.loads(stdout.getvalue())["logout_verified"])
        for private in (PASSWORD, COOKIE, CSRF):
            self.assertNotIn(private, stdout.getvalue())

    def test_cookie_parse_errors_are_sanitized(self):
        stderr = io.StringIO()
        with patch.dict(os.environ, {"CHECK_COCKPIT_TEST_PASSWORD": PASSWORD}), patch.object(CHECK.SimpleCookie, "load", side_effect=CHECK.CookieError(COOKIE)), redirect_stderr(stderr):
            result = CHECK.main(["--domain", HOST, "--address", ADDRESS, "--username", "matthew", "--password-env", "CHECK_COCKPIT_TEST_PASSWORD"])
        self.assertEqual(result, 1)
        self.assertNotIn(COOKIE, stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_invalid_target_and_cleartext_credentials_fail_before_network(self):
        for domain, address in [("https://" + HOST, ADDRESS), (HOST + "\r\nX-Test: true", ADDRESS), (HOST, "example.com"), (HOST, "8.8.8.8")]:
            with self.assertRaises(CHECK.CheckError):
                CHECK.Client(domain, address)
        with self.assertRaisesRegex(CHECK.CheckError, "verified HTTPS"):
            self.client().get("/", {"Authorization": "PRIVATE"}, secure=False)
        self.assertEqual(self.fixture.requests, [])

    def test_bad_accept_or_wrong_protocol_is_rejected(self):
        headers = Message()
        headers["Upgrade"] = "websocket"
        headers["Connection"] = "Upgrade"
        headers["Sec-WebSocket-Accept"] = "wrong"
        headers["Sec-WebSocket-Protocol"] = "cockpit1"
        with self.assertRaisesRegex(CHECK.CheckError, "valid HTTP 101"):
            CHECK.validate_upgrade(101, headers, "key")
        headers.replace_header("Sec-WebSocket-Accept", "not-ascii-\u00e9")
        with self.assertRaisesRegex(CHECK.CheckError, "valid HTTP 101"):
            CHECK.validate_upgrade(101, headers, "key")
        headers.replace_header("Sec-WebSocket-Accept", base64.b64encode(hashlib.sha1(("key" + CHECK.WS_GUID).encode()).digest()).decode())
        headers.replace_header("Sec-WebSocket-Protocol", "other")
        with self.assertRaisesRegex(CHECK.CheckError, "cockpit1"):
            CHECK.validate_upgrade(101, headers, "key")

    def test_fragmented_init_with_ping_is_handled_without_exposing_payload(self):
        payload = b'\n{"command":"init","version":1}'
        stream = io.BytesIO(server_frame(payload[:8], final=False) + server_frame(b"keepalive", 9) + server_frame(payload[8:], 0))
        sock = FakeSocket(self.fixture, 443)
        self.assertEqual(CHECK.read_message(sock, stream), payload)
        self.assertEqual(self.fixture.frames, [(10, b"keepalive")])


if __name__ == "__main__":
    unittest.main()
