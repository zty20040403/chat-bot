"""Set only the configured reverse-WebSocket client's runtime credential."""
import json
import os
from pathlib import Path
import sys
import tempfile


def configure(config_path: Path, token_path: Path, url: str) -> None:
    token = token_path.read_text().strip()
    if len(token) < 32 or any(ch.isspace() for ch in token):
        raise ValueError("OneBot credential is invalid")
    settings = json.loads(config_path.read_text())
    clients = settings.get("network", {}).get("websocketClients", [])
    targets = [item for item in clients if item.get("enable") and item.get("url") == url]
    if len(targets) != 1:
        raise ValueError("Expected exactly one configured reverse-WebSocket target")
    if targets[0].get("token") == token:
        return
    targets[0]["token"] = token
    descriptor, temporary = tempfile.mkstemp(dir=config_path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(settings, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        previous = config_path.stat()
        os.chown(temporary, previous.st_uid, previous.st_gid)
        os.replace(temporary, config_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    try:
        configure(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    except Exception:
        # Never include configuration or credentials in the service journal.
        raise SystemExit("Could not configure the NapCat reverse-WebSocket credential") from None
