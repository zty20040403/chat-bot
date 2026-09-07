"""Exercise the installed image as the same non-root user as task sandboxes."""

import os
import subprocess
import tempfile
from pathlib import Path

import pymupdf


def main() -> None:
    if os.getuid() != 1000:
        raise RuntimeError("Sandbox smoke test must run as uid 1000")
    subprocess.run(["node", "-e", "process.stdout.write('Node OK\\n')"], check=True, timeout=15)
    subprocess.run(["nix", "--version"], check=True, timeout=15)
    with tempfile.TemporaryDirectory(prefix="gaoji-smoke-", dir="/workspace") as directory:
        root = Path(directory)
        source = root / "input.md"
        output = root / "output.pdf"
        title = "\u4e2d\u6587\u6c99\u76d2\u9a8c\u6536"
        source.write_text(f"# {title}\n\nGaoji sandbox ready.\n", encoding="utf-8")
        subprocess.run(["gaoji-pdf", str(source), str(output)], check=True, timeout=30)
        with pymupdf.open(output) as document:
            text = "".join(page.get_text() for page in document)
        if title not in text:
            raise RuntimeError("Sandbox PDF failed CJK text verification")
    print("Sandbox readiness passed: non-root workspace, Node, Nix and CJK PDF")


if __name__ == "__main__":
    main()
