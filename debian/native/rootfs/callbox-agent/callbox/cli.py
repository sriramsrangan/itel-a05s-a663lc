"""`callbox`: the operator's command line.

  callbox status
  callbox sms send --sim 1 --to +919000000002 --text "test"      (own SIM numbers only)
  callbox ussd --sim 1 '*121#' [--then 1 --then 2]
  callbox test-call 121 --dtmf 1 [--before 6 --after 10]          (121 only)
  callbox forwarding query --sim 1 [--reason unconditional]
  callbox forwarding set --sim 1 --number +91XXXXXXXXXX --i-mean-it
  callbox forwarding disable --sim 1 --i-mean-it

Every command goes through the running agent's control socket. With --direct (for a first
hardware session, agent stopped) the command opens the modem itself; add --bring-up to run the
full modem start-up first. The device key is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .config import load_agent_config, load_audio_config


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="callbox", description="Call box phone control (talks to callbox-agent).")
    ap.add_argument("--direct", action="store_true", help="open the modem directly (the agent must be stopped)")
    ap.add_argument("--bring-up", action="store_true", help="with --direct: run the full modem start-up first")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="modem, SIMs, data, Wi-Fi, spool and call counters")

    sms = sub.add_parser("sms", help="SMS between the phone's own SIMs")
    smss = sms.add_subparsers(dest="sms_cmd", required=True)
    send = smss.add_parser("send")
    send.add_argument("--sim", type=int, choices=(1, 2), required=True)
    send.add_argument("--to", required=True)
    send.add_argument("--text", required=True)

    u = sub.add_parser("ussd", help="send a USSD code and print the reply")
    u.add_argument("code")
    u.add_argument("--sim", type=int, choices=(1, 2), default=1)
    u.add_argument("--then", action="append", default=[], help="reply to a menu prompt (repeatable)")
    u.add_argument("--timeout", type=float, default=30)

    t = sub.add_parser("test-call", help="call 121, play DTMF into the uplink, record and transcribe the downlink")
    t.add_argument("number")
    t.add_argument("--dtmf", default="1")
    t.add_argument("--before", type=float, default=6.0)
    t.add_argument("--after", type=float, default=10.0)

    f = sub.add_parser("forwarding", help="call forwarding (changes need --i-mean-it)")
    fs = f.add_subparsers(dest="cf_cmd", required=True)
    for name in ("query", "set", "disable"):
        p = fs.add_parser(name)
        p.add_argument("--sim", type=int, choices=(1, 2), default=1)
        p.add_argument("--reason", default="unconditional", choices=("unconditional", "busy", "noreply", "unreachable", "all", "conditional"))
        if name == "set":
            p.add_argument("--number", required=True)
        if name != "query":
            p.add_argument("--i-mean-it", action="store_true", help="really change forwarding on the network")
    return ap


def request_from(args) -> dict:
    if args.cmd == "status":
        return {"op": "status"}
    if args.cmd == "sms":
        return {"op": "sms_send", "sim": args.sim, "to": args.to, "text": args.text}
    if args.cmd == "ussd":
        return {"op": "ussd", "sim": args.sim, "code": args.code, "then": args.then, "timeout": args.timeout}
    if args.cmd == "test-call":
        return {"op": "test_call", "number": args.number, "dtmf": args.dtmf, "before": args.before, "after": args.after}
    if args.cmd == "forwarding":
        if args.cf_cmd == "query":
            return {"op": "cf_query", "sim": args.sim, "reason": args.reason}
        return {"op": "cf_change", "sim": args.sim, "reason": args.reason, "number": getattr(args, "number", None), "enable": args.cf_cmd == "set", "i_mean_it": bool(args.i_mean_it)}
    raise SystemExit("unknown command")


def refuse_early(args, cfg) -> str | None:
    """Refusals that need no modem, so they hold even if the agent is not running."""
    if args.cmd == "test-call" and args.number != "121":
        return "refused: test calls go to 121 only"
    if args.cmd == "sms" and args.to not in cfg.sim_numbers.values():
        return "refused: SMS may only be sent to the phone's own SIM numbers"
    if args.cmd == "forwarding" and args.cf_cmd != "query" and not args.i_mean_it:
        return "refused: changing call forwarding needs --i-mean-it"
    return None


def run_direct(args, req: dict) -> dict:
    from .agent import attach, build_services, dispatch, make_api

    cfg = load_agent_config()
    if os.path.exists(cfg.control_socket):
        from .agent import control_request

        try:
            control_request(cfg.control_socket, {"op": "status"}, timeout=5)
            return {"ok": False, "error": "callbox-agent is running; stop it (systemctl stop callbox-agent) or drop --direct"}
        except OSError:
            pass
    s = build_services(cfg, load_audio_config(), make_api(cfg))
    s.modem.open()
    attach(s)
    if args.bring_up and not s.modem.bring_up():
        return {"ok": False, "error": f"bring-up failed: {s.modem.state.bring_up_detail}"}
    if not args.bring_up:
        s.modem.mark_at_ok()
        s.modem.state.up = s.modem.ch(1).at_ok(3)
        for sim in (1, 2):
            s.modem.query_cpin(sim)
            s.modem.query_reg(sim)
        s.modem.query_ims(1)
    try:
        return {"ok": True, "result": dispatch(s, req)}
    finally:
        s.modem.close()


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_agent_config()
    why = refuse_early(args, cfg)
    if why:
        print(why, file=sys.stderr)
        return 2
    req = request_from(args)
    if args.direct:
        out = run_direct(args, req)
    else:
        from .agent import control_request

        try:
            out = control_request(cfg.control_socket, req)
        except OSError as exc:
            print(f"callbox-agent is not reachable at {cfg.control_socket} ({exc.strerror or exc}); use --direct with the agent stopped", file=sys.stderr)
            return 1
    if not out.get("ok"):
        print(out.get("error", "failed"), file=sys.stderr)
        return 1
    res = out["result"]
    if args.cmd == "test-call":
        print(res.get("verdict", ""))
    _print(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
