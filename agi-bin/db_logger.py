#!/usr/bin/env python3
"""
db_logger.py

Logs every custom-AMD decision to vicidial_custom_amd_log, reusing the
existing ViciDial database credentials from /etc/astguiclient.conf so no
new secrets need to be created or stored.

Logging failures never raise -- a DB hiccup should never be allowed to
break call handling. Errors go to stderr (visible via AGI VERBOSE/asterisk
console if AGILOG is on) and the function just returns False.
"""

import re
import sys

_CONF_PATH = "/etc/astguiclient.conf"

_CONF_KEYS = {
    "VARDB_server": "host",
    "VARDB_database": "database",
    "VARDB_port": "port",
    "VARDB_user": "user",
    "VARDB_pass": "password",
}


def _read_astguiclient_conf(path=_CONF_PATH):
    """Parse the `KEY => value` style astguiclient.conf into a dict of
    connection kwargs for pymysql.connect()."""
    values = {}
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.match(r"^(\S+)\s*=>\s*(.*)$", line)
                if not m:
                    continue
                key, val = m.group(1), m.group(2).strip()
                if key in _CONF_KEYS:
                    values[_CONF_KEYS[key]] = val
    except OSError as e:
        print(f"db_logger: could not read {path}: {e}", file=sys.stderr)
        return None

    if "port" in values:
        try:
            values["port"] = int(values["port"])
        except ValueError:
            values["port"] = 3306

    required = {"host", "database", "user", "password"}
    if not required.issubset(values.keys()):
        print(f"db_logger: {path} missing one of {required}", file=sys.stderr)
        return None

    return values


def _connect():
    try:
        import pymysql
    except ImportError:
        print(
            "db_logger: pymysql not installed -- "
            "install with: zypper install python3-PyMySQL",
            file=sys.stderr,
        )
        return None

    conn_kwargs = _read_astguiclient_conf()
    if not conn_kwargs:
        return None

    try:
        return pymysql.connect(charset="utf8mb4", autocommit=True, **conn_kwargs)
    except Exception as e:
        print(f"db_logger: connection failed: {e}", file=sys.stderr)
        return None


def log_decision(
    call_uniqueid=None,
    lead_id=None,
    campaign_id=None,
    extension=None,
    mode="SHADOW",
    custom_status=None,
    custom_cause=None,
    custom_run_time_ms=None,
    custom_total_time_ms=None,
    custom_detail=None,
    stock_status=None,
    stock_cause=None,
    recording_path=None,
):
    """Insert one row into vicidial_custom_amd_log. Returns True/False;
    never raises."""
    agreement = None
    if stock_status and custom_status:
        agreement = 1 if stock_status.upper() == custom_status.upper() else 0

    conn = _connect()
    if conn is None:
        return False

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vicidial_custom_amd_log
                    (call_uniqueid, lead_id, campaign_id, extension, mode,
                     custom_status, custom_cause, custom_run_time_ms,
                     custom_total_time_ms, custom_detail,
                     stock_status, stock_cause, agreement, recording_path)
                VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s)
                """,
                (
                    call_uniqueid, lead_id, campaign_id, extension, mode,
                    custom_status, custom_cause, custom_run_time_ms,
                    custom_total_time_ms, custom_detail,
                    stock_status, stock_cause, agreement, recording_path,
                ),
            )
        return True
    except Exception as e:
        print(f"db_logger: insert failed: {e}", file=sys.stderr)
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def lookup_campaign_id(callerid):
    """Best-effort lookup of campaign_id for the in-progress call, same
    source table VD_amd.agi itself uses (vicidial_auto_calls). Returns None
    on any failure -- this is an optional enrichment, not required for the
    detector to work."""
    if not callerid:
        return None

    conn = _connect()
    if conn is None:
        return None

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT campaign_id FROM vicidial_auto_calls "
                "WHERE callerid=%s ORDER BY auto_call_id DESC LIMIT 1",
                (callerid,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as e:
        print(f"db_logger: campaign_id lookup failed: {e}", file=sys.stderr)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def lookup_stock_result(call_uniqueid=None, lead_id=None):
    """For shadow mode: look up what the REAL (stock AMD driven) call
    outcome was, from vicidial_log.status. Returns (status_code, None) or
    (None, None) if not found yet (e.g. called too soon after the call).

    Status code meanings relevant here (see VD_amd.agi):
        AA      -> stock AMD said MACHINE, call handed to voicemail path
        AM/UNKAM-> stock AMD said MACHINE, greeting message played
        AL/UNKAL-> stock AMD said MACHINE, message fully played, hung up
        (anything else, notably calls that reached an agent) -> stock AMD said HUMAN
    """
    conn = _connect()
    if conn is None:
        return None, None

    try:
        with conn.cursor() as cur:
            if call_uniqueid:
                cur.execute(
                    "SELECT status FROM vicidial_log WHERE uniqueid LIKE %s "
                    "ORDER BY call_date DESC LIMIT 1",
                    (call_uniqueid.split(".")[0] + "%",),
                )
            elif lead_id:
                cur.execute(
                    "SELECT status FROM vicidial_log WHERE lead_id=%s "
                    "ORDER BY call_date DESC LIMIT 1",
                    (lead_id,),
                )
            else:
                return None, None

            row = cur.fetchone()
            if not row:
                return None, None

            status_code = row[0]
            machine_codes = {"AA", "AM", "UNKAM", "AL", "UNKAL", "ADAIR"}
            stock_status = "MACHINE" if status_code in machine_codes else "HUMAN"
            return stock_status, status_code
    except Exception as e:
        print(f"db_logger: lookup failed: {e}", file=sys.stderr)
        return None, None
    finally:
        try:
            conn.close()
        except Exception:
            pass
