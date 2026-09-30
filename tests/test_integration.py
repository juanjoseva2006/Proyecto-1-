"""Tests against the real C binary and real TCP sockets; external auth is mocked."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "clients"))
from protocol import Connection, Rejected  # noqa: E402


class AuthMock(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path != "/auth/v1/token?grant_type=password" or self.headers.get("apikey") != "test-public":
            self.send_error(400)
            return
        email = body["email"]
        if email == "slow@example.com":
            time.sleep(4)
        if body["password"] != 'Lab"Secret123':
            status, result = 400, {"error": "invalid_credentials"}
        elif email == "down@example.com":
            status, result = 503, {}
        elif email == "broken@example.com":
            status, result = 200, {"user": {"app_metadata": {"perfil": "ADMIN"}}}
        else:
            role = "ADMIN" if email == "admin@example.com" else "LECTOR"
            metadata = {} if email == "noprole@example.com" else {"perfil": role}
            status, result = 200, {"user": {"id": "test-user", "app_metadata": metadata, "user_metadata": {"perfil": "ADMIN"}}}
        data = json.dumps(result).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


class AuthTestServer(ThreadingHTTPServer):
    # The default backlog (5 on Python 3.11/3.12) can drop connection bursts
    # from the 20-worker test before their handler threads are started.
    # Keep the mock from becoming the bottleneck; production timeouts stay intact.
    request_queue_size = 128


class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mock = AuthTestServer(("localhost", 0), AuthMock)
        cls.thread = threading.Thread(target=cls.mock.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.mock.shutdown()
        cls.mock.server_close()
        cls.thread.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "server.log"
        with socket.socket() as reserve:
            reserve.bind(("localhost", 0))
            self.port = reserve.getsockname()[1]
        env = dict(os.environ, MONITOR_BIND_HOST="localhost", SUPABASE_URL=f"http://localhost:{self.mock.server_port}", SUPABASE_PUBLISHABLE_KEY="test-public")
        self.process = subprocess.Popen([str(ROOT / "build/server-test"), str(self.port), str(self.log)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.sockets = []
        deadline = time.monotonic() + 5
        while True:
            try:
                self.connect().close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError("Servidor no arranco")
                time.sleep(0.03)

    def tearDown(self):
        for connection in self.sockets:
            connection.close()
        self.process.terminate()
        _, stderr = self.process.communicate(timeout=8)
        self.assertEqual(self.process.returncode, 0, stderr.decode())
        self.tmp.cleanup()

    def connect(self):
        c = Connection("localhost", self.port)
        self.sockets.append(c)
        return c

    def node(self, name="nodo1"):
        c = self.connect()
        c.send("REGISTER", 1, name)
        self.assertEqual(c.ack(1, "REGISTER"), "OK")
        return c

    def admin(self, email="admin@example.com"):
        c = self.connect()
        c.send("AUTH", 1, email, 'Lab"Secret123')
        c.ack(1, "AUTH")
        return c

    def error(self, c, expected):
        row = c.receive()
        self.assertEqual(row[0], "ERROR", row)
        self.assertEqual(row[2], expected, row)

    def barrier(self, c, node="nodo1", request=100):
        c.send("EVENT", request, node, "FALLA", "barrera")
        c.ack(request, "EVENT")

    def test_two_nodes_history_and_roles(self):
        n1, n2 = self.node(), self.node("nodo2")
        for i in range(7):
            n1.send("STATUS", i+2, "nodo1", i, 40, "NORMAL")
            n2.send("STATUS", i+2, "nodo2", 90-i, 80, "ALERTA")
        self.barrier(n1)
        self.barrier(n2, "nodo2")
        admin = self.admin()
        rows = admin.query(2, "HISTORY", "nodo1")
        self.assertEqual([int(r[1]) for r in rows], [2, 3, 4, 5, 6])
        self.assertEqual(admin.query(3, "CURRENT", "nodo2")[0][1:], ["84", "80", "ALERTA", "ACTIVO"])
        self.assertEqual(len(admin.query(4, "EVENTS", "nodo1")), 1)
        reader = self.admin("lector@example.com")
        self.assertEqual(len(reader.query(2, "HISTORY", "nodo1")), 5)
        with self.assertRaises(Rejected) as raised:
            reader.query(3, "EVENTS", "nodo1")
        self.assertEqual(raised.exception.code, "FORBIDDEN")

    def test_fragmentation_and_coalescing(self):
        c = self.connect()
        for fragment in [b"REG", b"ISTER|", b"1|nodo1\nSTATUS|2|nodo1|30|40|NORMAL\nEVENT|3|nodo1|FALLA|test\n"]:
            c.sock.sendall(fragment)
        c.ack(1, "REGISTER")
        c.ack(3, "EVENT")
        self.assertEqual(self.admin().query(2, "CURRENT", "nodo1")[0][1], "30")

    def test_duplicate_event_after_reconnection(self):
        c = self.node()
        c.send("EVENT", 5, "nodo1", "FALLA", "unico")
        c.ack(5, "EVENT")
        c.close()
        time.sleep(0.1)
        c = self.node()
        c.send("EVENT", 5, "nodo1", "FALLA", "unico")
        c.ack(5, "EVENT")
        c.send("EVENT", 4, "nodo1", "FALLA", "viejo")
        c.ack(4, "EVENT")
        rows = self.admin().query(2, "EVENTS", "nodo1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "5")

    def test_invalid_inputs_do_not_change_state(self):
        c = self.node()
        cases = [("STATUS|2|nodo1|101|20|NORMAL", "BAD_VALUE"),
                 ("STATUS|3|nodo1|1|20|OTRO", "BAD_VALUE"),
                 ("STATUS|4|nodo1||20|NORMAL", "BAD_FORMAT"),
                 ("STATUS|5|otro|1|20|NORMAL", "UNKNOWN_NODE"),
                 ("EVENT|6|nodo1|FALLA|bad;detail", "BAD_VALUE"),
                 ("EVENT|7|nodo1|FALLA|x|extra", "BAD_FORMAT"),
                 ("STATUS|8|nodo1|999999999999999999999999|20|NORMAL", "BAD_VALUE"),
                 ("QUERY|9|CURRENT|nodo1", "INVALID_STATE"),
                 ("REGISTER|10|otro", "INVALID_STATE"),
                 ("BOGUS|11|x", "BAD_FORMAT")]
        for message, expected in cases:
            c.sock.sendall((message+"\n").encode())
            self.error(c, expected)
        self.assertEqual(self.admin().query(2, "CURRENT", "nodo1"), [])

    def test_unknown_node_and_unauthenticated(self):
        c = self.connect()
        c.send("QUERY", 1, "CURRENT", "nodo1")
        self.error(c, "INVALID_STATE")
        admin = self.admin()
        with self.assertRaises(Rejected) as raised:
            admin.query(2, "CURRENT", "unknown")
        self.assertEqual(raised.exception.code, "UNKNOWN_NODE")

    def test_duplicate_registration(self):
        self.node()
        other = self.connect()
        other.send("REGISTER", 1, "nodo1")
        self.error(other, "NODE_IN_USE")
        other.send("REGISTER", 2, "nodo2")
        other.ack(2, "REGISTER")

    def test_malformed_id_and_binary(self):
        for data in [b"REGISTER|0|n\n", b"REGISTER|2147483648|n\n", b"REGISTER|+1|n\n", b"REGISTER\n"]:
            c = self.connect()
            c.sock.sendall(data)
            self.error(c, "BAD_FORMAT")
        c = self.connect()
        c.sock.sendall(b"REGISTER|1|n\x00x\n")
        self.error(c, "BAD_FORMAT")
        self.assertEqual(c.sock.recv(1), b"")

    def test_oversized_and_partial_line_timeout(self):
        c = self.connect()
        c.sock.sendall(b"x"*1024)
        self.assertEqual(c.sock.recv(1), b"")
        c = self.connect()
        c.sock.sendall(b"REGISTER|1|")
        c.sock.settimeout(7)
        self.assertEqual(c.sock.recv(1), b"")
        self.node()  # other clients still work

    def test_disconnected_and_stale(self):
        n = self.node()
        n.send("STATUS", 2, "nodo1", 20, 40, "NORMAL")
        self.barrier(n)
        c = self.admin()
        self.assertEqual(c.query(2, "CURRENT", "nodo1")[0][-1], "ACTIVO")
        time.sleep(15.1)
        self.assertEqual(c.query(3, "CURRENT", "nodo1")[0][-1], "SIN_DATOS")
        n.close()
        time.sleep(0.1)
        self.assertEqual(c.query(4, "CURRENT", "nodo1")[0][-1], "DESCONECTADO")

    def test_concurrent_clients(self):
        n = self.node()
        n.send("STATUS", 2, "nodo1", 50, 40, "NORMAL")
        self.barrier(n)
        def query(_):
            c = self.admin()
            try:
                return c.query(2, "CURRENT", "nodo1")[0][1]
            finally:
                c.close()
        with ThreadPoolExecutor(max_workers=20) as pool:
            self.assertEqual(list(pool.map(query, range(40))), ["50"]*40)

    def test_authentication_errors_and_retry(self):
        c = self.connect()
        c.send("AUTH", 1, "admin@example.com", "wrong")
        self.error(c, "AUTH_FAILED")
        c.send("AUTH", 2, "noprole@example.com", 'Lab"Secret123')
        self.error(c, "FORBIDDEN")
        c.send("AUTH", 3, "down@example.com", 'Lab"Secret123')
        self.error(c, "AUTH_UNAVAILABLE")
        c.send("AUTH", 4, "broken@example.com", 'Lab"Secret123')
        self.error(c, "AUTH_UNAVAILABLE")
        c.send("AUTH", 5, "admin@example.com", 'Lab"Secret123')
        self.assertEqual(c.ack(5, "AUTH"), "ADMIN")

    def test_auth_timeout_does_not_block_nodes(self):
        c = self.connect()
        c.send("AUTH", 1, "slow@example.com", 'Lab"Secret123')
        start = time.monotonic()
        self.node()
        self.assertLess(time.monotonic()-start, 1)
        self.error(c, "AUTH_UNAVAILABLE")
        self.assertLess(time.monotonic()-start, 4)

    def test_logs_redact_even_malformed_auth(self):
        c = self.connect()
        c.sock.sendall(b'AUTH|bad|email|supersecret\nAUTH|1|email|othersecret|extra\n')
        self.error(c, "BAD_FORMAT")
        self.error(c, "BAD_FORMAT")
        self.admin()
        log = self.log.read_text()
        for secret in ["supersecret", "othersecret", 'Lab"Secret123']:
            self.assertNotIn(secret, log)
        self.assertIn("TX ACK|1|AUTH|ADMIN", log)
        self.assertIn("CONNECT", log)

    def test_real_node_process(self):
        result = subprocess.run([sys.executable, str(ROOT/"clients/node.py"), "--host", "localhost", "--port", str(self.port), "--node", "demo", "--stable-id", "--interval", "0.02", "--samples", "6", "--event-every", "2", "--seed", "1"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Evento confirmado", result.stdout)
        self.assertEqual(len(self.admin().query(2, "HISTORY", "demo")), 5)

    def test_ack_lost_and_five_recent_events(self):
        n = self.node()
        for i in range(2, 8):
            n.send("EVENT", i, "nodo1", "FALLA", f"evento_{i}")
            n.ack(i, "EVENT")
        n.send("EVENT", 8, "nodo1", "FALLA", "ack_no_leido")
        c = self.admin()
        for request in range(2, 20):
            rows = c.query(request, "EVENTS", "nodo1")
            if rows[-1][1] == "8":
                break
            time.sleep(0.01)
        self.assertEqual(rows[-1][1], "8")
        n.close()  # ACK never read, delivery is uncertain to the node
        time.sleep(0.1)
        n = self.node()
        n.send("EVENT", 8, "nodo1", "FALLA", "ack_no_leido")
        n.ack(8, "EVENT")
        rows = c.query(30, "EVENTS", "nodo1")
        self.assertEqual([r[1] for r in rows], ["4", "5", "6", "7", "8"])

    def test_real_administration_client(self):
        n = self.node()
        n.send("STATUS", 2, "nodo1", 47, 42, "NORMAL")
        self.barrier(n)
        # Detach from the runner's terminal so getpass reads the test input pipe.
        result = subprocess.run([sys.executable, str(ROOT/"clients/client.py"), "--host", "localhost", "--port", str(self.port), "--email", "admin@example.com"], input='Lab"Secret123\nCURRENT nodo1\nHISTORY nodo1\nEVENTS nodo1\nsalir\n', capture_output=True, text=True, timeout=10, start_new_session=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Sesion ADMIN", result.stdout)
        self.assertIn("ACTIVO", result.stdout)
        self.assertIn("barrera", result.stdout)

    def test_production_rejects_test_provider(self):
        with socket.socket() as reserve:
            reserve.bind(("localhost", 0))
            port = reserve.getsockname()[1]
        env = dict(os.environ, MONITOR_BIND_HOST="localhost", SUPABASE_URL=f"http://localhost:{self.mock.server_port}", SUPABASE_PUBLISHABLE_KEY="test-public")
        proc = subprocess.Popen([str(ROOT/"build/server"), str(port), str(Path(self.tmp.name)/"production.log")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic()+5
            while True:
                try:
                    c = Connection("localhost", port)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fail("Servidor normal no inicio")
                    time.sleep(0.03)
            try:
                c.send("AUTH", 1, "admin@example.com", 'Lab"Secret123')
                self.error(c, "AUTH_UNAVAILABLE")
                c.send("REGISTER", 2, "normal")
                c.ack(2, "REGISTER")
            finally:
                c.close()
        finally:
            proc.terminate()
            _, stderr = proc.communicate(timeout=8)
            self.assertEqual(proc.returncode, 0, stderr.decode())


if __name__ == "__main__":
    unittest.main()
