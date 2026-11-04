"""Install and run Aegis from a fresh checkout. Uses only Python's standard library."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import venv
import webbrowser

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STAMP = VENV / ".aegis-ready.json"
INPUTS = ("requirements.txt", "web/package.json", "web/package-lock.json", "scripts/fetch_weights.py")


class SetupError(Exception):
    pass


def inputs() -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in INPUTS}


def environment() -> dict[str, str]:
    env = os.environ.copy()
    env.update(PYTHONNOUSERSITE="1", PYTHONUTF8="1", YOLO_AUTOINSTALL="false",
               PYTHONPATH=os.pathsep.join(str(ROOT / p) for p in ("backend", "edge")))
    for key, folder in (("TEMP", ".tmp"), ("TMP", ".tmp"), ("TMPDIR", ".tmp"),
                        ("YOLO_CONFIG_DIR", ".ultralytics"), ("PIP_CACHE_DIR", ".pip-cache")):
        (ROOT / folder).mkdir(exist_ok=True)
        env[key] = str(ROOT / folder)
    return env


def run(command: list, *, cwd: Path = ROOT) -> None:
    print(f"\n> {subprocess.list2cmdline([str(arg) for arg in command])}", flush=True)
    subprocess.run(command, cwd=cwd, env=environment(), check=True)


def prerequisites() -> dict[str, str]:
    if sys.version_info[:2] != (3, 13) or sys.implementation.name != "cpython" or sys.maxsize <= 2**32:
        raise SetupError("Use 64-bit CPython 3.13: 'py -3.13 run.py setup' on Windows, "
                         "or 'python3.13 run.py setup' on Linux/macOS. See docs/SETUP.md.")
    tools = {}
    for name in ("node", "npm", "ffmpeg", "ffprobe"):
        path = shutil.which(name)
        if not path:
            raise SetupError(f"{name} is missing from PATH. Install the prerequisites in docs/SETUP.md, "
                             "then open a new terminal.")
        tools[name] = path
    version = subprocess.check_output([tools["node"], "--version"], text=True).strip()
    if int(version.lstrip("v").split(".")[0]) not in (22, 24):
        raise SetupError(f"Found Node {version}; use Node 22 LTS or 24 LTS.")
    encoders = subprocess.check_output([tools["ffmpeg"], "-hide_banner", "-encoders"],
                                       text=True, stderr=subprocess.STDOUT)
    if "libx264" not in encoders:
        raise SetupError("Your ffmpeg build has no libx264 encoder. Install a full build from ffmpeg.org.")
    subprocess.run([tools["ffprobe"], "-version"], check=True, stdout=subprocess.DEVNULL)
    print(f"Python {sys.version.split()[0]} | Node {version} | ffmpeg and ffprobe found", flush=True)
    return tools


def check_python() -> None:
    if not PYTHON.is_file():
        raise SetupError("The project environment is missing. Run 'python run.py setup' first.")
    version = subprocess.check_output([str(PYTHON), "-c", "import sys; print('%s.%s' % sys.version_info[:2])"],
                                      text=True).strip()
    if version != "3.13":
        raise SetupError(f".venv uses Python {version}. Move that environment aside and rerun setup with Python 3.13.")


def setup(tools: dict[str, str], *, demo: bool) -> None:
    if not VENV.exists():
        print("Creating .venv (all Python packages stay in this checkout).", flush=True)
        venv.EnvBuilder(with_pip=True).create(VENV)
    check_python()
    STAMP.unlink(missing_ok=True)
    env_file = ROOT / ".env"
    if not env_file.exists():
        with env_file.open("xb") as output:
            output.write((ROOT / ".env.example").read_bytes())
        print("Created .env. Add META_API_KEY there for the full model workflow.")
    else:
        print("Using your existing .env.")
    run([PYTHON, "-m", "pip", "install", "-r", ROOT / "requirements.txt"])
    run([PYTHON, "-m", "pip", "check"])
    run([PYTHON, ROOT / "scripts/fetch_weights.py"])
    run([tools["npm"], "ci"], cwd=WEB)
    run([PYTHON, "-c", "from app.scene import warmup; warmup(); print('Both YOLO models loaded and ran on CPU.')"])
    if demo:
        run([PYTHON, ROOT / "scripts/fetch_demo.py"])
    STAMP.write_text(json.dumps({"inputs": inputs()}, indent=2), encoding="utf-8")
    print("\nSetup complete. Edit .env to add your Meta key, then run: python run.py\n"
          "No environment activation is needed. Use 'python run.py check' to check this installation.")


def check_installation() -> None:
    check_python()
    try:
        stamp = json.loads(STAMP.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        raise SetupError("Setup has not finished. Run 'python run.py setup'.") from None
    if stamp.get("inputs") != inputs():
        raise SetupError("Dependencies changed since setup. Stop Aegis and rerun 'python run.py setup'.")
    if not (ROOT / ".env").is_file() or not (WEB / "node_modules/next/dist/bin/next").is_file():
        raise SetupError(".env or web dependencies are missing. Rerun 'python run.py setup'.")
    from scripts.fetch_weights import WEIGHTS, digest
    for name, expected in WEIGHTS.items():
        path = ROOT / name
        if not path.is_file() or digest(path) != expected:
            raise SetupError(f"{name} is missing or differs from the pinned model. Run 'python scripts/fetch_weights.py'.")
    print("Project environment, setup record, web installation and both model checksums: OK.")


def check_ports(ports: tuple[int, int]) -> None:
    if ports[0] == ports[1]:
        raise SetupError("The API and console need different ports.")
    for port in ports:
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                raise SetupError(f"Port {port} is in use. Stop the existing server, or use "
                                 "--api-port 8001 --web-port 3001. No process was stopped.") from None


def stop(process: subprocess.Popen) -> None:
    if os.name == "nt":
        if process.poll() is None:
            # Only the process tree created by this runner; never kill by image name or port.
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=15)


def wait_ready(url: str, processes: list[subprocess.Popen], timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    # Local readiness checks must not be sent through a configured corporate proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process in processes):
            raise SetupError("A service stopped during startup. See .tmp/api.log and .tmp/web.log.")
        try:
            with opener.open(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(.25)
    raise SetupError(f"Startup timed out at {url}. See .tmp/api.log and .tmp/web.log.")


def start(tools: dict[str, str], args: argparse.Namespace) -> None:
    check_ports((args.api_port, args.web_port))
    from scripts.serve import load_env
    load_env(ROOT / ".env")
    env = environment()
    env["PORT"] = str(args.api_port)
    env["AEGIS_API"] = f"http://127.0.0.1:{args.api_port}"
    origins = env.get("DASHBOARD_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    env["DASHBOARD_ORIGINS"] = ",".join(dict.fromkeys(origins + [
        f"http://127.0.0.1:{args.web_port}", f"http://localhost:{args.web_port}",
    ]))
    if not env.get("META_API_KEY", "").strip():
        print("META_API_KEY is empty: local detection works; SAM, Muse and transcription are unavailable.")
    url = f"http://127.0.0.1:{args.web_port}/setup"
    commands = [([str(PYTHON), str(ROOT / "scripts/serve.py")], ROOT, "api"),
                ([tools["node"], "node_modules/next/dist/bin/next", "dev", "-H", "127.0.0.1",
                  "-p", str(args.web_port)], WEB, "web")]
    processes, logs = [], []
    try:
        for command, cwd, name in commands:
            log = (ROOT / f".tmp/{name}.log").open("w", encoding="utf-8")
            logs.append(log)
            options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
            processes.append(subprocess.Popen(command, cwd=cwd, env=env, stdout=log,
                                              stderr=subprocess.STDOUT, **options))
        print("Starting API and console. Logs: .tmp/api.log and .tmp/web.log", flush=True)
        wait_ready(f"{env['AEGIS_API']}/health", processes)
        wait_ready(url, processes)
        print(f"\nAegis is ready: {url}\nKeep this terminal open. Ctrl+C stops both services.", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        while all(process.poll() is None for process in processes):
            time.sleep(.5)
        raise SetupError("A service stopped. See .tmp/api.log and .tmp/web.log.")
    finally:
        for process in reversed(processes):
            stop(process)
        for log in logs:
            log.close()


def tcp_port(value: str) -> int:
    try:
        port = int(value)
        if 1 <= port <= 65535:
            return port
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("port must be an integer between 1 and 65535")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("setup", "start", "check", "demo", "test"), default="start")
    parser.add_argument("--demo", action="store_true", help="also download the six public URFD samples during setup")
    parser.add_argument("--no-browser", action="store_true", help="print the console URL without opening a browser")
    parser.add_argument("--api-port", type=tcp_port, metavar="PORT", default=8000)
    parser.add_argument("--web-port", type=tcp_port, metavar="PORT", default=3000)
    args = parser.parse_args()
    try:
        tools = prerequisites()
        if args.command == "setup":
            setup(tools, demo=args.demo)
        elif args.command == "demo":
            run([sys.executable, ROOT / "scripts/fetch_demo.py"])
        else:
            check_installation()
            if args.command == "start":
                start(tools, args)
            elif args.command == "check":
                run([PYTHON, "-m", "pip", "check"])
                run([PYTHON, "-c", "from app.scene import warmup; warmup(); print('Local model inference: OK.')"])
                from scripts.serve import load_env
                load_env(ROOT / ".env")
                print("Meta key: " + ("configured (not contacted)" if os.environ.get("META_API_KEY", "").strip() else "not set; local-only workflow"))
            else:
                check_ports((args.api_port, args.web_port))
                run([PYTHON, ROOT / "scripts/verify_submission.py", "--out", ROOT / ".tmp/verification.json"])
                run([tools["node"], "--test", "tests/inspection.test.cjs"], cwd=WEB)
                run([tools["node"], "node_modules/next/dist/bin/next", "build"], cwd=WEB)
    except KeyboardInterrupt:
        print("\nAegis stopped.")
        return 0
    except (SetupError, OSError, subprocess.SubprocessError) as exc:
        print(f"\nAegis: {exc}\nHelp: docs/SETUP.md", file=sys.stderr)
        return 1
    return 0


def interrupt(_signum, _frame) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, interrupt)
    raise SystemExit(main())
