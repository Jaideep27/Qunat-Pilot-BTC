"""Start one background service with no console window and print its PID: start_hidden.py LOG -- COMMAND [ARGS...].

Used by scripts/start.cjs. Node's detached start leaves a service without a console, so Windows opened a
visible terminal for every console program the service started (each PostgreSQL backend, for one).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))

from quantpilot.monitoring.process import start_hidden  # noqa: E402


def main(argv):
    if len(argv) < 3 or argv[1] != '--':
        raise SystemExit('usage: start_hidden.py LOG -- COMMAND [ARGS...]')
    print(start_hidden(argv[2:], argv[0], ROOT).pid, flush=True)


if __name__ == '__main__':
    main(sys.argv[1:])
