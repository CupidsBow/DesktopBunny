import json
import logging
import queue
import re
import threading
import time

import requests
import websocket
from websocket import (
    WebSocketConnectionClosedException,
    WebSocketException,
    WebSocketTimeoutException,
)

from constants import constants
from bot import dispatcher

# QQ 网关 opcode
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
_MENTION_RE = re.compile(r"<@!?\d+>")


def _api_host() -> str:
    return "sandbox.api.sgroup.qq.com" if constants.QQ_IS_SANDBOX else "api.sgroup.qq.com"


class _WorkerPool:
    """固定大小的守护线程池，用于并发处理每条 QQ 消息。

    recv 循环只负责「收 -> 丢进来」，聊天 + 回复在 worker 里并发执行，
    互不阻塞。线程设为 daemon，程序退出时不会卡住等待。
    """

    def __init__(self, size: int):
        self._q = queue.Queue()
        for _ in range(size):
            threading.Thread(target=self._run, daemon=True).start()

    def submit(self, fn, *args, **kwargs):
        self._q.put((fn, args, kwargs))

    def _run(self):
        logger = logging.getLogger(__name__)
        while True:
            fn, args, kwargs = self._q.get()
            try:
                fn(*args, **kwargs)
            except Exception as e:
                logger.error(f"消息处理异常: {e}")


class QQClient:
    """QQ 官方机器人网关客户端（同步 websocket-client，跑在独立线程里）。

    连网关 -> Hello -> Identify -> 收事件；断线自动退避重连。
    收到消息后立即丢进 worker 线程池，由 bot.dispatcher 处理，需要回复时走 REST 发回。
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._running = False
        self._ws = None
        self._session_id = None
        self._seq = None
        self._access_token = None
        self._token_expire_at = 0.0
        self._token_lock = threading.Lock()
        self._heartbeat_interval = 41.25
        self._send_lock = threading.Lock()
        self._pool = _WorkerPool(constants.QQ_WORKER_COUNT)

    def start(self):
        if not constants.QQ_APP_ID or not constants.QQ_APP_SECRET:
            self.logger.warning("未配置 QQ_APP_ID/SECRET，QQ bot 不启动")
            return
        self._running = True
        threading.Thread(target=self._run, daemon=True, name="qq-bot").start()

    def _run(self):
        while self._running:
            try:
                self._connect_and_loop()
            except Exception as e:
                self.logger.error(f"QQ 网关连接异常: {e}")
            if not self._running:
                break
            time.sleep(5)

    def _connect_and_loop(self):
        token = self._ensure_access_token()
        ws_url = self._get_gateway_url()
        self.logger.info(f"连接 QQ 网关: {ws_url}")
        self._ws = websocket.create_connection(ws_url, timeout=90)

        # 连接后第一条必是 Hello，拿到心跳周期
        hello = json.loads(self._ws.recv())
        if hello.get("op") == OP_HELLO:
            self._heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000.0

        self._send({
            "op": OP_IDENTIFY,
            "d": {
                "token": f"QQBot {token}",
                "intents": constants.QQ_INTENTS,
                "shard": [0, 1],
            },
        })

        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

        while self._running:
            try:
                raw = self._ws.recv()
            except WebSocketTimeoutException:
                continue
            except (WebSocketConnectionClosedException, WebSocketException, OSError) as e:
                self.logger.warning(f"连接中断: {e}")
                break
            if not raw:
                continue

            payload = json.loads(raw)
            if payload.get("s") is not None:
                self._seq = payload["s"]
            op = payload.get("op")

            if op == OP_DISPATCH:
                self._on_dispatch(payload)
            elif op == OP_RECONNECT:
                self.logger.info("服务端要求重连")
                break
            elif op == OP_INVALID_SESSION:
                self.logger.error("鉴权失败(op9)：检查 QQ_APP_ID / QQ_APP_SECRET / QQ_INTENTS")
                self._session_id = None
                break
            elif op == OP_HEARTBEAT_ACK:
                pass

        self._close()

    def _heartbeat_loop(self):
        while self._running and self._ws:
            time.sleep(self._heartbeat_interval)
            try:
                self._send({"op": OP_HEARTBEAT, "d": self._seq})
            except Exception:
                break

    def _send(self, obj: dict):
        with self._send_lock:
            if self._ws:
                self._ws.send(json.dumps(obj))

    def _close(self):
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass
        self._ws = None

    # ---- 事件 ----
    def _on_dispatch(self, payload):
        t = payload.get("t")
        d = payload.get("d") or {}
        if t == "READY":
            self._session_id = d.get("session_id")
            self.logger.info("QQ 机器人已就绪")
        elif t == "C2C_MESSAGE_CREATE":
            self._on_c2c(d)
        elif t == "GROUP_AT_MESSAGE_CREATE":
            self._on_group(d)

    def _on_c2c(self, d):
        user_openid = (d.get("author") or {}).get("user_openid")
        content = (d.get("content") or "").strip()
        msg_id = d.get("id")
        if not user_openid or not content:
            return
        self._pool.submit(self._handle_message, "c2c", user_openid, msg_id, content)

    def _on_group(self, d):
        group_openid = d.get("group_openid")
        content = _MENTION_RE.sub("", d.get("content") or "").strip()
        msg_id = d.get("id")
        if not group_openid or not content:
            return
        self._pool.submit(self._handle_message, "group", group_openid, msg_id, content)

    def _handle_message(self, kind, target_id, msg_id, content):
        """在 worker 线程里执行：分发给 dispatcher，拿到回复后发回。"""
        reply = dispatcher.handle(content)
        if not reply:
            return
        if kind == "c2c":
            self.send_c2c_message(target_id, msg_id, reply)
        else:
            self.send_group_message(target_id, msg_id, reply)

    # ---- 发消息（REST） ----
    def send_c2c_message(self, user_openid, msg_id, content):
        self._send_with_fallback(f"/v2/users/{user_openid}/messages", content, msg_id)

    def send_group_message(self, group_openid, msg_id, content):
        self._send_with_fallback(f"/v2/groups/{group_openid}/messages", content, msg_id)

    def _send_with_fallback(self, path, content, msg_id):
        # 1) 被动回复：带 msg_id，受回复时间窗口限制
        if msg_id and self._post_message(path, content, msg_id=msg_id):
            return
        # 2) 主动回复兜底：不带 msg_id，不受时间窗口限制
        if msg_id:
            self.logger.warning("被动回复失败，改用主动消息发送")
        self._post_message(path, content)

    def _post_message(self, path, content, msg_id=None) -> bool:
        token = self._ensure_access_token()
        url = f"https://{_api_host()}{path}"
        body = {"content": content, "msg_type": 0}
        if msg_id:
            body["msg_id"] = msg_id
        try:
            r = requests.post(
                url,
                json=body,
                headers={"Authorization": f"QQBot {token}"},
                timeout=15,
            )
            r.raise_for_status()
            return True
        except requests.HTTPError as e:
            self.logger.error(f"发送消息失败(HTTP {r.status_code}): {e}")
        except requests.RequestException as e:
            self.logger.error(f"发送消息失败(网络): {e}")
        return False

    # ---- access_token ----
    def _ensure_access_token(self) -> str:
        with self._token_lock:
            if self._access_token and time.time() < self._token_expire_at - 60:
                return self._access_token
            r = requests.post(
                _TOKEN_URL,
                json={"appId": constants.QQ_APP_ID, "clientSecret": constants.QQ_APP_SECRET},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            self._access_token = data["access_token"]
            self._token_expire_at = time.time() + int(data.get("expires_in", 7200))
            return self._access_token

    def _get_gateway_url(self) -> str:
        token = self._ensure_access_token()
        r = requests.get(
            f"https://{_api_host()}/gateway",
            headers={"Authorization": f"QQBot {token}"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()["url"]
