"""Exec probe for the Frappe roles that expose no HTTP endpoint.

    probe.py worker     Redis queue answers and this pod's RQ worker is registered
    probe.py scheduler  Redis queue answers and the scheduler process is running

Reads the same REDIS_QUEUE_* variables the entrypoint does. Exits non-zero
with a one-line reason on failure.
"""

import glob
import os
import socket
import sys

import redis


def _queue():
    return redis.Redis(
        host=os.environ.get("REDIS_QUEUE_HOST", "redis-queue"),
        port=int(os.environ.get("REDIS_QUEUE_PORT", "11000")),
        password=os.environ.get("REDIS_QUEUE_PASSWORD") or None,
        socket_connect_timeout=3,
        socket_timeout=3,
    )


def _worker(conn):
    # RQ records each live worker as a hash with its hostname; the hash expires
    # when the worker stops sending heartbeats.
    host = socket.gethostname().encode()
    for key in conn.smembers("rq:workers"):
        if conn.hget(key, "hostname") == host:
            return
    sys.exit("no RQ worker registered for this pod")


def _scheduler(_conn):
    for path in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(path, "rb") as fh:
                args = fh.read().split(b"\0")
        except OSError:
            continue
        if b"frappe.utils.bench_helper" in args and b"schedule" in args:
            return
    sys.exit("scheduler process not running")


def main():
    role = sys.argv[1] if len(sys.argv) > 1 else ""
    check = {"worker": _worker, "scheduler": _scheduler}.get(role)
    if check is None:
        sys.exit(f"usage: probe.py worker|scheduler (got {role!r})")
    conn = _queue()
    try:
        conn.ping()
        check(conn)
    except redis.RedisError as e:
        sys.exit(f"redis queue unreachable: {e.__class__.__name__}")


if __name__ == "__main__":
    main()
