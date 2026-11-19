import tempfile
import unittest
from pathlib import Path
from unittest import mock

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from piglet.server.security import (
    LocalOnlyMiddleware,
    allowed_hosts,
    allowed_origins,
    same_user_peer,
    socket_owner,
)

# /proc/net/tcp as Linux prints it on little-endian machines: the server listening on
# 127.0.0.1:8761 (0x2239), its end of a connection, and the client's end on port 50000
# (0xC350), owned by uid 1001.
TABLE = """\
  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:2239 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 11 1
   1: 0100007F:2239 0100007F:C350 01 00000000:00000000 00:00000000 00000000     0        0 12 1
   2: 0100007F:C350 0100007F:2239 01 00000000:00000000 00:00000000 00000000  1001        0 13 1
"""
CLIENT = ("127.0.0.1", 50000)
SERVER = ("127.0.0.1", 8761)
HOST = {"host": "127.0.0.1:8761"}


class SocketOwnerTest(unittest.TestCase):
    def test_finds_the_client_end_of_the_connection(self):
        self.assertEqual(socket_owner(TABLE, CLIENT, SERVER), 1001)
        self.assertEqual(socket_owner(TABLE, SERVER, CLIENT), 0)
        self.assertIsNone(socket_owner(TABLE, ("127.0.0.1", 50001), SERVER))


class SameUserPeerTest(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.table = Path(tmp) / "tcp"
        self.table.write_text(TABLE)
        self.scope = {"type": "websocket", "client": CLIENT, "server": SERVER}

    def allowed(self, uid, scope=None, table=None):
        with mock.patch("piglet.server.security.os.getuid", return_value=uid, create=True):
            return same_user_peer(scope or self.scope, table or self.table)

    def test_refuses_another_users_socket(self):
        self.assertTrue(self.allowed(1001))
        self.assertFalse(self.allowed(1000))

    def test_falls_back_to_the_token_when_the_owner_is_unknown(self):
        self.assertTrue(self.allowed(1000, table=self.table.with_name("missing")))
        self.assertTrue(self.allowed(1000, scope={**self.scope, "client": ("127.0.0.1", 1)}))
        self.assertTrue(self.allowed(1000, scope={**self.scope, "client": ("testclient", 1)}))
        self.assertTrue(self.allowed(1000, scope={"type": "websocket"}))


class MiddlewarePeerTest(unittest.TestCase):
    def client(self, peer_ok):
        async def echo(websocket):
            await websocket.accept()
            await websocket.send_text("hi")
            await websocket.close()

        async def page(_request):
            return PlainTextResponse("page")

        app = Starlette(routes=[WebSocketRoute("/ws", echo), Route("/", page)])
        guarded = LocalOnlyMiddleware(
            app, lambda: allowed_hosts(8761), lambda: allowed_origins(8761), peer_ok
        )
        return self.enterContext(TestClient(guarded, base_url="http://127.0.0.1:8761"))

    def test_websocket_from_another_user_is_closed(self):
        client = self.client(lambda scope: False)
        with (
            self.assertRaises(WebSocketDisconnect) as caught,
            client.websocket_connect("/ws", headers=HOST),
        ):
            pass
        self.assertEqual(caught.exception.code, 4403)
        self.assertEqual(client.get("/").text, "page")

    def test_websocket_from_the_same_user_is_served(self):
        with self.client(lambda scope: True).websocket_connect("/ws", headers=HOST) as websocket:
            self.assertEqual(websocket.receive_text(), "hi")


if __name__ == "__main__":
    unittest.main()
