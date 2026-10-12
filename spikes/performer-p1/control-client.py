"""Sends commands to a running perf1's control pipe and prints each reply.

  python -I control-client.py PIPE_NAME COMMAND [COMMAND ...]

PIPE_NAME is what perf1 was started with (--pipe NAME). A COMMAND is a JSON object (see src/perf1-control.h), for example

  python -I control-client.py perf1-live '{"cmd":"status"}'
  python -I control-client.py perf1-live '{"cmd":"load","file":"C:/.../cues-v3.json"}' '{"cmd":"seek","t":31.5}'
  python -I control-client.py perf1-live '{"cmd":"pause"}' '{"cmd":"resume"}' '{"cmd":"stop"}'

Exit code 1 if the pipe cannot be opened or any reply says "ok": false.
"""
import json
import sys


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    path = rf"\\.\pipe\{argv[1]}"
    bad = 0
    for msg in argv[2:]:
        try:
            with open(path, "r+b", buffering=0) as f:   # perf1 serves one client at a time and opens a new pipe for the next
                f.write(msg.encode())
                reply = f.read(65536).decode()
        except OSError as ex:
            print(f"cannot reach {path}: {ex}")
            return 1
        print(reply)
        try:
            bad += 0 if json.loads(reply).get("ok") else 1
        except ValueError:
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
