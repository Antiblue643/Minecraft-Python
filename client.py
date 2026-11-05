import os
from pathlib import Path
import sys


def main():
    if getattr(sys, "frozen", False):
        os.chdir(Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)))
    from game.app.launcher import main as launch

    launch()


if __name__ == "__main__":
    main()
