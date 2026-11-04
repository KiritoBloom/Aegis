"""Checkout setup must preserve configuration and clean up only its own services."""
from __future__ import annotations

from argparse import Namespace
import importlib.util
import io
import os
from pathlib import Path
import socket
import subprocess
import sys

import psutil
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load("aegis_runner", "run.py")
demo = load("aegis_demo_assets", "scripts/fetch_demo.py")


def test_failed_install_preserves_existing_config_and_clears_ready_stamp(tmp_path, monkeypatch):
    env = tmp_path / ".venv"
    env.mkdir()
    stamp = env / ".aegis-ready.json"
    stamp.write_text('{"old": true}')
    secret = b"META_API_KEY=keep-this-local-value\n"
    (tmp_path / ".env").write_bytes(secret)
    for name, value in (("ROOT", tmp_path), ("VENV", env), ("STAMP", stamp)):
        monkeypatch.setattr(runner, name, value)
    monkeypatch.setattr(runner, "check_python", lambda: None)

    def broken_install(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "pip install")

    monkeypatch.setattr(runner, "run", broken_install)
    with pytest.raises(subprocess.CalledProcessError):
        runner.setup({"npm": "npm"}, demo=False)
    assert (tmp_path / ".env").read_bytes() == secret
    assert not stamp.exists()


def test_occupied_port_does_not_start_or_stop_any_process(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("A busy port must be rejected before creating or stopping processes")

    monkeypatch.setattr(runner.subprocess, "Popen", unexpected)
    monkeypatch.setattr(runner, "stop", unexpected)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(runner.SetupError, match="in use"):
            runner.start({}, Namespace(api_port=port, web_port=port - 1, no_browser=True))
        assert listener.getsockname()[1] == port


def test_stop_removes_owned_child_tree():
    child = "import time; print('ready', flush=True); time.sleep(60)"
    parent = ("import subprocess,sys; "
              f"p=subprocess.Popen([sys.executable,'-c',{child!r}],stdout=subprocess.PIPE,text=True); "
              "p.stdout.readline(); print(p.pid,flush=True); p.wait()")
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen([sys.executable, "-c", parent], stdout=subprocess.PIPE,
                               text=True, **options)
    try:
        child_pid = int(process.stdout.readline().strip())
        assert psutil.pid_exists(child_pid)
        runner.stop(process)
        assert process.poll() is not None
        assert not psutil.pid_exists(child_pid) or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
    finally:
        if process.poll() is None:
            runner.stop(process)
        process.stdout.close()


def test_existing_different_demo_is_not_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "CLIPS", {"fall-06": demo.CLIPS["fall-06"]})
    target = tmp_path / "fall-06.mp4"
    target.write_bytes(b"my own recording")
    monkeypatch.setattr(demo.urllib.request, "urlopen", lambda *a, **k: pytest.fail("must not download over existing media"))
    with pytest.raises(SystemExit, match="differs"):
        demo.fetch(tmp_path)
    assert target.read_bytes() == b"my own recording"


def test_bad_demo_download_never_becomes_playable_file(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "CLIPS", {"fall-06": demo.CLIPS["fall-06"]})
    monkeypatch.setattr(demo.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"not the expected video"))
    with pytest.raises(SystemExit, match="Checksum mismatch"):
        demo.fetch(tmp_path)
    assert not list(tmp_path.iterdir())
