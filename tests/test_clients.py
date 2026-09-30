"""Failure injection for client paths that must survive DNS/network failures."""
import argparse
import contextlib
import io
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"clients"))
import node
import client


class ClientRecovery(unittest.TestCase):
    def test_node_survives_dns_failure(self):
        args = argparse.Namespace(node="test", stable_id=True, seed=1, host="missing.invalid", port=5000)
        with patch.object(node, "Connection", side_effect=socket.gaierror("DNS test")), patch.object(node.time, "sleep", side_effect=KeyboardInterrupt) as sleep, contextlib.redirect_stdout(io.StringIO()) as output:
            node.run(args)
        sleep.assert_called_once_with(5)
        self.assertIn("reintento", output.getvalue())

    def test_client_survives_dns_failure(self):
        with patch.object(sys, "argv", ["client.py", "--email", "test@example.com"]), patch.object(client.getpass, "getpass", return_value="test"), patch.object(client, "Connection", side_effect=socket.gaierror("DNS test")), patch.object(client.time, "sleep", side_effect=KeyboardInterrupt) as sleep, contextlib.redirect_stdout(io.StringIO()) as output:
            client.main()
        sleep.assert_called_once_with(5)
        self.assertIn("Reintento", output.getvalue())
