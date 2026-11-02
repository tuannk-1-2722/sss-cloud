"""Local review gate. Optional toolchains are reported as NOT RUN, never as PASS."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from security_app.config import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http", action="store_true", help="Run disposable loopback integration")
    args = parser.parse_args()
    commands = [
        [sys.executable, "-m", "ruff", "check", "backend", "tools", "tests"],
        [sys.executable, "-m", "ruff", "format", "--check", "backend", "tools", "tests"],
        [sys.executable, "-m", "pytest", "-q"],
    ]
    if shutil.which("node"):
        commands.append(["node", "--test", "tests/javascript/sw_push_test.cjs"])
        for name in ("app.js", "camera-stream.js", "sw.js"):
            commands.append(["node", "--check", f"backend/security_app/static/{name}"])
    else:
        print("NOT RUN: JavaScript syntax (node not installed)", flush=True)
    with tempfile.TemporaryDirectory(prefix="sss-check-") as folder:
        if shutil.which("g++"):
            for path in sorted((ROOT / "tests/firmware").glob("*_test.cpp")):
                binary = str(
                    Path(folder) / (path.stem + (".exe" if sys.platform == "win32" else ""))
                )
                commands.extend(
                    [
                        [
                            "g++",
                            "-std=c++17",
                            "-Wall",
                            "-Wextra",
                            "-Werror",
                            str(path),
                            "-o",
                            binary,
                        ],
                        [binary],
                    ]
                )
        else:
            print("NOT RUN: firmware host logic (g++ not installed)", flush=True)
        if args.http:
            commands.append([sys.executable, "-m", "tools.smoke_integration"])
        for command in commands:
            subprocess.run(command, cwd=ROOT, check=True)
    print(
        "Software checks finished. Full ESP32 build, HIL and real AI evaluation are separate gates."
    )


if __name__ == "__main__":
    main()
