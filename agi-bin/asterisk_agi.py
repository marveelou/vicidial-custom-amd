#!/usr/bin/env python3
"""
asterisk_agi.py

Minimal Asterisk AGI protocol client -- just enough to read the agi_*
environment dump, issue RECORD FILE / SET VARIABLE / GET VARIABLE / VERBOSE /
HANGUP commands, and parse the "200 result=..." responses.

This deliberately does not try to be a full-featured AGI library (no need
for DTMF handling, full app catalog, etc.) -- amd_agi.py only needs the
handful of commands below.
"""

import re
import sys


class AGIException(Exception):
    pass


_RESPONSE_RE = re.compile(r"^(\d{3})\s+result=(-?\d+)\s*(?:\((.*)\))?")


class AGI:
    def __init__(self, stdin=None, stdout=None, stderr=None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self.env = {}
        self._read_env()

    def _read_env(self):
        while True:
            line = self.stdin.readline()
            if not line:
                break
            line = line.rstrip("\r\n")
            if line == "":
                break
            if ":" in line:
                key, _, value = line.partition(":")
                self.env[key.strip()] = value.strip()

    # -- low level -----------------------------------------------------

    def _send(self, command):
        self.stdout.write(command + "\n")
        self.stdout.flush()
        response = self.stdin.readline()
        return self._parse_response(response)

    def _parse_response(self, response):
        response = (response or "").strip()
        m = _RESPONSE_RE.match(response)
        if not m:
            return {"code": None, "result": None, "data": response}
        return {
            "code": int(m.group(1)),
            "result": int(m.group(2)),
            "data": m.group(3) or "",
        }

    # -- commands actually used by amd_agi.py ---------------------------

    def get_variable(self, name):
        resp = self._send(f"GET VARIABLE {name}")
        if resp["result"] == 1:
            return resp["data"]
        return ""

    def set_variable(self, name, value):
        # value is wrapped in quotes per AGI protocol convention for
        # arguments that may contain spaces/special chars
        resp = self._send(f'SET VARIABLE {name} "{value}"')
        return resp["result"] == 1

    def verbose(self, message, level=1):
        safe_message = str(message).replace('"', "'")
        return self._send(f'VERBOSE "{safe_message}" {level}')

    def stream_file(self, filename, escape_digits=""):
        return self._send(f'STREAM FILE {filename} "{escape_digits}"')

    def record_file(
        self,
        filename,
        fmt="wav",
        escape_digits="",
        timeout_ms=5000,
        offset=0,
        beep=False,
        silence_secs=None,
    ):
        cmd = f'RECORD FILE {filename} {fmt} "{escape_digits}" {timeout_ms}'
        if offset:
            cmd += f" {offset}"
        if beep:
            cmd += " BEEP"
        if silence_secs is not None:
            cmd += f" s={silence_secs}"
        return self._send(cmd)

    def hangup(self, channel=""):
        return self._send(f"HANGUP {channel}".strip())

    def exec_app(self, app, *args):
        argstr = ",".join(str(a) for a in args)
        return self._send(f'EXEC {app} "{argstr}"')
