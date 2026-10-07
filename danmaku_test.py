"""Offline live-chat regressions. Run: python -X utf8 -B danmaku_test.py."""

import base64
import hashlib
import io
import json
import socket
import struct
from pathlib import Path
from unittest.mock import Mock, patch

import app


class CapturedFile(io.StringIO):
    def close(self):
        self.saved_text = self.getvalue()
        super().close()


def collector(client=None):
    result = app.DanmakuCollector(
        client or app.BilibiliClient(lambda: ""), "123", Path("unused.jsonl"),
        0, 0, 3, Mock(),
    )
    result._file = CapturedFile()
    return result


def fails(call, message):
    try:
        call()
    except (ConnectionError, RuntimeError, ValueError) as exc:
        assert message in str(exc), str(exc)
    else:
        raise AssertionError(f"Expected failure: {message}")


def check_identity():
    cookies = iter(("SESSDATA=first; DedeUserID=42; buvid3=device", "SESSDATA=second; DedeUserID=99"))
    client = app.BilibiliClient(lambda: next(cookies))
    response = {"code": 0, "data": {"token": "private-token", "host_server_list": [{"host": "chat.example", "wss_port": 443}]}}

    def request(req, **kwargs):
        assert req.get_header("Cookie") == "SESSDATA=first; DedeUserID=42; buvid3=device"
        assert req.get_header("User-agent") == app.USER_AGENT
        assert "room_id=123" in req.full_url
        return io.BytesIO(json.dumps(response).encode())

    with patch.object(app.urllib.request, "urlopen", side_effect=request):
        info = client.danmaku_info("123")
    assert info["uid"] == 42 and info["buvid"] == "device" and info["token"] == "private-token"
    assert info["hosts"] == response["data"]["host_server_list"]
    for cookie in ("", "DedeUserID=42"):
        with patch.object(client, "cookie_getter", return_value=cookie), patch.object(client, "json_request", return_value=response):
            assert client.danmaku_info("123")["uid"] == 0
    for cookie in ("SESSDATA=x", "SESSDATA=x; DedeUserID=invalid", "SESSDATA=x; DedeUserID=-1"):
        with patch.object(client, "cookie_getter", return_value=cookie), patch.object(client, "json_request") as request:
            fails(lambda: client.danmaku_info("123"), "UID")
            request.assert_not_called()


def check_transport():
    value = collector()
    key = base64.b64encode(b"x" * 16).decode()
    accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\nConnection: keep-alive, Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    ).encode()
    sock = Mock()
    sock.recv.side_effect = [response[:8], response[8:] + b"\x82\x03one\x89\x01p"]
    with patch.object(app.socket, "create_connection", return_value=sock), patch.object(app.os, "urandom", return_value=b"x" * 16):
        assert value._connect_ws("chat.example", 2244, False) is sock
    sent = sock.sendall.call_args.args[0]
    assert f"User-Agent: {app.USER_AGENT}\r\n".encode() in sent
    assert b"Origin: https://live.bilibili.com\r\n" in sent
    assert value._recv_ws_frame(sock) == (True, 2, b"one")
    assert value._recv_ws_frame(sock) == (True, 9, b"p")
    assert sock.recv.call_count == 2
    assert sock.settimeout.call_args.args == (1.0,)

    for chunks, message in (
        ([b""], "握手时连接已关闭"),
        ([response.replace(accept.encode(), b"invalid")], "握手校验失败"),
        ([response.replace(b"101 Switching Protocols", b"403 Forbidden")], "握手校验失败"),
        ([response.replace(b"Upgrade: websocket", b"Upgrade: other")], "握手校验失败"),
        ([socket.timeout()], "timed out"),
    ):
        sock = Mock()
        sock.recv.side_effect = chunks
        with patch.object(app.socket, "create_connection", return_value=sock), patch.object(app.os, "urandom", return_value=b"x" * 16):
            if message == "timed out":
                try:
                    value._connect_ws("chat.example", 443, False)
                except socket.timeout:
                    pass
                else:
                    raise AssertionError("Handshake timeout was ignored")
            else:
                fails(lambda: value._connect_ws("chat.example", 443, False), message)
        sock.close.assert_called_once()
        assert value._socket is None

    sock = Mock()
    with patch.object(app.socket, "create_connection", return_value=sock), patch.object(app.ssl, "create_default_context") as context:
        context.return_value.wrap_socket.side_effect = OSError("TLS failed")
        try:
            value._connect_ws("chat.example", 443, True)
        except OSError:
            pass
        else:
            raise AssertionError("TLS failure was ignored")
    sock.close.assert_called_once()

    # Timeouts at every possible boundary must leave the partial frame intact.
    for size in (0, 3, 126, 65536):
        payload = b"a" * size
        for masked in (False, True):
            if masked:
                frame = app.DanmakuCollector._ws_frame(payload)
            elif size < 126:
                frame = b"\x82" + bytes([size]) + payload
            else:
                frame = b"\x82" + (b"\x7e" + struct.pack(">H", size) if size < 65536 else b"\x7f" + struct.pack(">Q", size)) + payload
            for boundary in sorted({0, 1, 2, min(len(frame), 3), min(len(frame), 9), len(frame) - 1}):
                if boundary >= len(frame):
                    continue
                value = collector()
                chunks = ([frame[:boundary]] if boundary else []) + [socket.timeout(), frame[boundary:]]
                sock = Mock()
                sock.recv.side_effect = chunks
                assert value._recv_ws_frame(sock) is None
                assert value._recv_ws_frame(sock) == (True, 2, payload)
    for frame, message in (
        (b"\x82\x7f" + struct.pack(">Q", 16 * 1024 * 1024 + 1), "帧过大"),
        (b"\xc2\x00", "帧头无效"),
        (b"\x83\x00", "帧头无效"),
        (b"\x09\x00", "控制帧无效"),
        (b"", "连接已关闭"),
    ):
        sock = Mock()
        sock.recv.return_value = frame
        fails(lambda: collector()._recv_ws_frame(sock), message)
    assert list(app.iter_bili_packets(struct.pack(">IHHII", 0, 0, 1, 8, 1))) == []


def check_sessions():
    host = {"host": "chat.example", "wss_port": 443, "ws_port": 2244}
    client = app.BilibiliClient(lambda: "SESSDATA=offline; DedeUserID=99")
    info = {"uid": 42, "buvid": "device", "token": "private-token", "hosts": [host, host]}
    auth_reply = app._bili_packet(b'{"code":0}', 8)
    event = app._bili_packet(b'{"cmd":"DANMU_MSG","info":[[],"live chat",[7,"user"]]}', 5)
    compressed = app._bili_packet(app.zlib.compress(event), 5, 2)
    value = collector(client)
    sock = Mock()
    frames = iter([
        (True, 2, auth_reply + app._bili_packet(b"\x00\x00\x00\x01", 3)),
        (False, 2, compressed[:7]),
        (True, 9, b"ping"),
        (True, 10, b"pong"),
        (True, 0, compressed[7:]),
    ])

    def receive(_sock):
        frame = next(frames, None)
        if frame is None:
            value.stop_event.set()
        return frame

    with patch.object(client, "danmaku_info", return_value=info), patch.object(value, "_connect_ws", return_value=sock) as connect, patch.object(value, "_recv_ws_frame", side_effect=receive), patch.object(app, "_brotli", None):
        value._run_websocket()
    connect.assert_called_once_with("chat.example", 443, True)
    assert json.loads(value._file.getvalue())["text"] == "live chat"
    assert any("鉴权成功" in call.args[0] for call in value.emit.call_args_list)
    decoder = collector()
    wire = Mock()
    wire.recv.return_value = b"".join(call.args[0] for call in sock.sendall.call_args_list)
    first = decoder._recv_ws_frame(wire)
    operation, body = list(app.iter_bili_packets(first[2]))[0][1:]
    assert operation == 7
    assert json.loads(body) == {"uid": 42, "roomid": 123, "protover": 2, "platform": "web", "type": 2, "key": "private-token", "buvid": "device"}
    assert list(app.iter_bili_packets(decoder._recv_ws_frame(wire)[2]))[0][1] == 2
    assert decoder._recv_ws_frame(wire) == (True, 10, b"ping")
    sock.close.assert_called_once()
    assert value._socket is None

    for reply, expected in (
        ((True, 2, app._bili_packet(b'{"code":-101,"message":"private-token"}', 8)), "code=-101"),
        ((True, 2, app._bili_packet(b"{}", 8)), "有效状态码"),
        ((True, 2, app._bili_packet(b'{"code":false}', 8)), "有效状态码"),
        ((True, 2, app._bili_packet(b"invalid", 8)), "鉴权响应无效"),
        ((True, 2, event), "尚未确认鉴权"),
        ((True, 8, struct.pack(">H", 1008) + b"private-token"), "code=1008"),
        ((True, 0, event), "无起始帧"),
    ):
        value = collector(client)
        sock = Mock()
        with patch.object(client, "danmaku_info", return_value=info), patch.object(value, "_connect_ws", return_value=sock) as connect, patch.object(value, "_recv_ws_frame", return_value=reply):
            fails(value._run_websocket, expected)
        connect.assert_called_once()
        assert "private-token" not in str(value.emit.call_args_list)
        assert not value._file.getvalue()
        sock.close.assert_called_once()

    for authenticated, expected in ((False, "鉴权响应超时"), (True, "心跳响应超时")):
        value = collector(client)
        clock = [0]
        replies = [auth_reply] if authenticated else []

        def idle(_sock):
            if replies:
                return True, 2, replies.pop()
            clock[0] += 31
            return None

        sock = Mock()
        with patch.object(client, "danmaku_info", return_value=info), patch.object(value, "_connect_ws", return_value=sock), patch.object(value, "_recv_ws_frame", side_effect=idle), patch.object(app.time, "monotonic", side_effect=lambda: clock[0]):
            fails(value._run_websocket, expected)
        if authenticated:
            assert sock.sendall.call_count == 3  # Auth, initial heartbeat, scheduled heartbeat.


def check_recovery():
    client = app.BilibiliClient(lambda: "")
    value = collector(client)
    clock = [0]
    attempts = []
    tokens = iter(("expired-token", "fresh-token"))
    host = {"host": "chat.example", "wss_port": 443}

    def info(_room):
        token = next(tokens)
        attempts.append(token)
        return {"token": token, "uid": 0, "hosts": [host, host]}

    def receive(_sock):
        if len(attempts) == 1:
            raise ConnectionError("disconnected")
        if clock[0] < 31:
            clock[0] = 31
            return True, 2, app._bili_packet(b'{"code":0}', 8)
        value.stop_event.set()
        return None

    def wait(seconds):
        clock[0] += seconds
        return False

    history = [{"text": "history chat", "timestamp": 10, "uid": 7}]
    output = value._file
    with patch.object(client, "danmaku_info", side_effect=info), patch.object(client, "danmaku_history", return_value=history) as poll, patch.object(value, "_connect_ws", return_value=Mock()), patch.object(value, "_recv_ws_frame", side_effect=receive), patch.object(app.time, "monotonic", side_effect=lambda: clock[0]), patch.object(value.stop_event, "wait", side_effect=wait):
        value._run()
    assert attempts == ["expired-token", "fresh-token"]
    assert poll.call_count == 10
    assert output.closed and value._file is None
    assert len(output.saved_text.splitlines()) == 1, "Repeated history was not deduplicated"
    assert any("鉴权成功" in call.args[0] for call in value.emit.call_args_list)
    assert value._socket is None

    value = collector(client)
    value.stop_event.set()
    with patch.object(value, "_run_websocket") as connect, patch.object(value, "_run_polling") as poll:
        value._run()
    connect.assert_not_called()
    poll.assert_not_called()
    value.emit.assert_not_called()

    value = collector(client)

    def cancelled(_sock):
        value.stop_event.set()
        raise ConnectionError("socket closed by stop")

    with patch.object(client, "danmaku_info", return_value={"hosts": [host]}), patch.object(value, "_connect_ws", return_value=Mock()), patch.object(value, "_recv_ws_frame", side_effect=cancelled), patch.object(value, "_run_polling") as poll:
        value._run()
    poll.assert_not_called()
    value.emit.assert_not_called()


def check_room_resolution():
    with app.tempfile.TemporaryDirectory(prefix="danmaku-room-check-") as folder:
        settings = app.Settings(base_dir=folder, auto_slice=False, auto_recover_recording=False)
        settings.ensure_dirs()
        db = app.Database(Path(folder) / "app.db")
        service = app.RecorderService(settings, db, app.queue.Queue())
        state = {"stop": app.threading.Event()}

        def connected():
            state["stop"].set()

        try:
            with patch.object(service.ffmpeg, "ensure_tools"), patch.object(app.BilibiliClient, "room_info", return_value={"room_id": "123456", "live_status": True, "title": "offline"}), patch.object(app, "DanmakuCollector") as collect, patch.object(service, "emit") as emit:
                collect.return_value.start.side_effect = connected
                service._record_worker("123", state)
                collect.assert_called_once()
                assert collect.call_args.args[1] == "123456"
                collect.call_args.args[-1]("弹幕实时连接已建立（鉴权成功）")
                assert emit.call_args.args[0] == "info"
                collect.return_value.stop.assert_called_once()
        finally:
            service.executor.shutdown(wait=True)


def run():
    check_identity()
    check_transport()
    check_sessions()
    check_recovery()
    check_room_resolution()
    print("Danmaku checks passed: account-bound auth, upgrade, partial frames, heartbeat, polling and reconnect")


if __name__ == "__main__":
    run()
