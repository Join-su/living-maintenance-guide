"""Static scan backing the listed netguard gaps (network_zero_check (a); rules S1~S7 fixed in build/S-03 packet C2).

  python scripts/static_net_scan.py [--root DIR] [--replace REL=REL2 ...]
Prints JSON {ok, violations}. Exit 1 on any violation. Reads files only; never executes them."""
import argparse
import ipaddress
import json
import re
import sys
from pathlib import Path

PY_DIRS = ("packrt", "tracewriter", "tests", "scripts")   # agent-pack-runtime (copied from llmops-lite-v2 scripts/static_net_scan.py at the split, EV-023)
NET_PROGRAMS = r"(curl|wget|nc|ncat|telnet|node)"
ALLOWED_INVALID = "netguard-probe.invalid"
SELF = "scripts/static_net_scan.py"
DENY_ALL_USERS = ("scripts/validate_judges.py", "scripts/run_gates.py")


def targets(root):
    files = [p for d in PY_DIRS for p in (root / d).rglob("*") if p.is_file() and p.suffix in (".py", ".cjs", ".sh", ".sb") and "__pycache__" not in p.parts]
    if (root / "scripts/isolated-env").exists():
        files.append(root / "scripts/isolated-env")
    return sorted(f for f in files if f.relative_to(root).as_posix() != SELF)  # the scanner holds the patterns itself


def scan(root, replace=None):
    replace = replace or {}
    violations = []
    for p in targets(root):
        rel = str(p.relative_to(root))
        src = root / replace.get(rel, rel)
        text = src.read_text(errors="replace")
        py_like = p.suffix == ".py" or rel == "scripts/isolated-env"
        shell = p.suffix == ".sh" or rel == "scripts/isolated-env"
        if p.suffix == ".sb":  # S9 also on sandbox profiles: the browser deny tag only in scripts/net-deny.sb
            for n, line in enumerate(text.splitlines(), 1):
                if "LLMOPS-BROWSER" in line and rel != "scripts/net-deny.sb":
                    violations.append({"rule": "S9", "file": rel, "line": n, "why": "the browser deny tag in a profile other than scripts/net-deny.sb", "text": line.strip()[:160]})
            continue
        if shell:  # S8 on logical lines too: a sandbox-exec command continued with a trailing backslash
            logical, start = "", 1
            for n, line in enumerate(text.splitlines(), 1):
                if not logical:
                    start = n
                logical += line.rstrip("\\") + " "
                if line.rstrip().endswith("\\"):
                    continue
                if n != start and re.search(r"(?<![\w-])net-deny\.sb", logical) and "sandbox-exec" in logical and rel != "scripts/verify-e2e.sh":
                    violations.append({"rule": "S8", "file": rel, "line": start, "why": "net-deny.sb used outside the browser launcher line (continued command)", "text": logical.strip()[:160]})
                logical = ""
        for n, line in enumerate(text.splitlines(), 1):
            def v(rule, why):
                violations.append({"rule": rule, "file": rel, "line": n, "why": why, "text": line.strip()[:160]})
            if py_like and re.search(r"^\s*(import\s+_socket|from\s+_socket\s+import)", line):
                v("S1", "_socket imported directly")
            if py_like and re.search(r"\bctypes\b", line):
                v("S2", "ctypes")
            if shell and re.search(rf"^\s*(exec\s+)?(\S*/)?{NET_PROGRAMS}\s", line) and "sandbox-exec" not in line:
                v("S3", "network program started without sandbox-exec")
            if p.suffix == ".py" and rel.split("/")[0] in ("tests", "scripts") and re.search(r"(subprocess|Popen|\.run\(|iso\()", line) \
                    and re.search(rf"[\"'](\S*/)?{NET_PROGRAMS}[\"']", line) and not (rel in DENY_ALL_USERS and "judge-net-deny.sb" in line):
                v("S4", "network program started from Python outside the deny-all profile of the judges/gate tools")
            # S8 (UF-S05-2): net-deny.sb (denials kept out of the verdict) only on the browser launcher line of verify-e2e.sh;
            # judge-net-deny.sb (denials counted) only in the judges and gate tools
            if re.search(r"(?<![\w-])net-deny\.sb", line) and "sandbox-exec" in line and rel != "scripts/verify-e2e.sh":
                v("S8", "net-deny.sb used outside the browser launcher line")
            if "judge-net-deny.sb" in line and "sandbox-exec" in line and rel not in DENY_ALL_USERS:
                v("S8", "judge-net-deny.sb used outside the judges/gate tools")
            if "LLMOPS-BROWSER" in line and rel not in DENY_ALL_USERS:
                v("S9", "the browser deny tag (kept out of the verdict) used outside scripts/net-deny.sb")
            if p.suffix == ".cjs" and re.search(r"(execFile|execFileSync|spawn|spawnSync|exec|execSync)\(", line) \
                    and re.search(rf"[\"'`](\S*/)?{NET_PROGRAMS}[\"'`]", line):
                v("S4", "network program started from the E2E script (its process tree is outside the verdict)")
            for ip in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", line):
                try:
                    addr = ipaddress.ip_address(ip)
                except ValueError:
                    continue
                if not (addr.is_loopback or ip == "0.0.0.0"):
                    v("S5", f"fixed non-loopback IPv4 {ip}")
            if re.search(r"https?://(?!127\.0\.0\.1|localhost|\[::1\])[A-Za-z0-9]", line):
                v("S6", "fixed non-loopback URL")
            for name in re.findall(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.invalid\b", line):
                if name != ALLOWED_INVALID:
                    v("S7", f".invalid name other than {ALLOWED_INVALID}")
    return {"ok": not violations, "files_scanned": len(targets(root)), "violations": violations}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--replace", nargs="*", default=[])
    a = ap.parse_args()
    res = scan(Path(a.root), dict(r.split("=", 1) for r in a.replace))
    print(json.dumps(res, ensure_ascii=False, indent=1))
    sys.exit(0 if res["ok"] else 1)
