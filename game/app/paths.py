import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(
    getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])
)


def user_data_root():
    if not getattr(sys, "frozen", False):
        return PROJECT_ROOT
    local_app_data = os.environ.get("LOCALAPPDATA") or str(
        Path.home() / "AppData" / "Local"
    )
    return Path(local_app_data) / ".minecraftpy"


def server_data_root():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


def server_saves_root():
    return server_data_root() / "server_saves"
