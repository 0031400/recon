"""Drive the stdio MCP client as a real subprocess and verify MCP end-to-end."""

import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def send(proc, obj):
    proc.stdin.write((json.dumps(obj) + "\n").encode())
    proc.stdin.flush()


def read_msg(proc, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("client closed stdout")
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise TimeoutError("no response")


def main():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(ROOT, "src")
    cfg_path = os.path.join(ROOT, "data", "_test_client.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"server": "tcp://127.0.0.1:40123", "psk": "stdio-test-psk",
                   "name": "e2e-win", "debug": False}, f)
    proc = subprocess.Popen(
        [PY, "-m", "recon", "-c", cfg_path],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=ROOT, env=env,
    )
    try:
        # initialize
        send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "drv", "version": "0"}}})
        init = read_msg(proc)
        assert "result" in init, init
        print("INIT OK:", init["result"]["serverInfo"])

        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        # tools/list
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = read_msg(proc)
        names = [t["name"] for t in tools["result"]["tools"]]
        print("TOOLS:", names)
        assert "exec" in names and "upload" in names and "download" in names

        # tools/call exec
        send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "exec", "arguments": {"command": "echo stdio-e2e-ok"}}})
        res = read_msg(proc)
        text = res["result"]["content"][0]["text"]
        print("EXEC RESULT:", text)
        payload = json.loads(text)
        assert payload["exit_code"] == 0
        assert "stdio-e2e-ok" in payload["stdout"]

        print("\nSTDIO MCP E2E PASSED")
    finally:
        proc.terminate()
        try:
            err = proc.stderr.read().decode(errors="replace")
            if err.strip():
                print("--- client stderr ---")
                print(err[-800:])
        except Exception:
            pass


if __name__ == "__main__":
    main()
