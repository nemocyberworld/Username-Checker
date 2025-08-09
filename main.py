#!/usr/bin/env python3
"""
Username Checker — fast username enumeration across many sites

Upgrades (clean + robust):
- Works with many sites.yml shapes (list/dict/strings) via normalization
- Per-site numbered output: "[i/N] [ STATUS ] (ms) Site: URL"
- Retries & timeouts, rotating headers from headers.yml
- Per-domain concurrency limit + jitter, proxy support (http/socks)
- Site filtering (--only, case-insensitive), bulk usernames (--userlist)
- Export positives to JSONL/CSV
- Optional evidence-only mode (--evidence-only) using sites.yml -> evidence_regex
- Backward compatible placeholders: {!!} and {user}
- NEW: --links-out streams positive URLs immediately (flush + fsync, de-duplicated)
- DEFAULTS: --links-out hits.txt and evidence-only mode enabled (can override with --any-200)
- NEW: Interactive prompt for usernames if not provided on CLI
- NEW: Prints an on-screen "How to use" guide
- NEW: Ordered console output while preserving concurrency
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from time import perf_counter
from urllib.parse import urlparse
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple
import argparse
import random
import sys
import os
import time
import json
import csv
import re
import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from yaml import safe_load, safe_dump

# -----------------------
# Banner
# -----------------------

def print_banner():
    banner = r"""
██╗░░░██╗░██████╗███████╗██████╗░███╗░░██╗░█████╗░███╗░░░███╗███████╗
██║░░░██║██╔════╝██╔════╝██╔══██╗████╗░██║██╔══██╗████╗░████║██╔════╝
██║░░░██║╚█████╗░█████╗░░██████╔╝██╔██╗██║███████║██╔████╔██║█████╗░░
██║░░░██║░╚═══██╗██╔══╝░░██╔══██╗██║╚████║██╔══██║██║╚██╔╝██║██╔══╝░░
╚██████╔╝██████╔╝███████╗██║░░██║██║░╚███║██║░░██║██║░╚═╝░██║███████╗
░╚═════╝░╚═════╝░╚══════╝╚═╝░░╚═╝╚═╝░░╚══╝╚═╝░░╚═╝╚═╝░░░░░╚═╝╚══════╝

░█████╗░██╗░░██╗███████╗░█████╗░██╗░░██╗███████╗██████╗░
██╔══██╗██║░░██║██╔════╝██╔══██╗██║░██╔╝██╔════╝██╔══██╗
██║░░╚═╝███████║█████╗░░██║░░╚═╝█████═╝░█████╗░░██████╔╝
██║░░██╗██╔══██║██╔══╝░░██║░░██╗██╔═██╗░██╔══╝░░██╔══██╗
╚█████╔╝██║░░██║███████╗╚█████╔╝██║░╚██╗███████╗██║░░██║
░╚════╝░╚═╝░░╚═╝╚══════╝░╚════╝░╚═╝░░╚═╝╚══════╝╚═╝░░╚═╝
    """
    print(banner)
    print("\033[38;2;110;200;255mPowered by HackToLive Academy\033[0m\n")

def print_howto():
    print("How to use:")
    print("  • Single user:        python main.py USERNAME")
    print('  • Limit sites:        python main.py USERNAME --only "GitHub,Twitter,Reddit"')
    print("  • Use a proxy:        python main.py USERNAME --proxy socks5://127.0.0.1:9050")
    print("  • Bulk from file:     python main.py --userlist users.txt")
    print("  • Outputs (default):  --links-out hits.txt (stream URLs) + --evidence-only")
    print("  • Looser matching:    add --any-200 to save any HTTP 200\n")
    print("Tip: without CLI usernames, you'll be prompted to enter them interactively.")
    print("==========================================================================\n")

# -----------------------
# Load YAML + normalize
# -----------------------

def _abs_here(relpath: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relpath)

def load_yaml(relpath: str) -> Any:
    path = _abs_here(relpath)
    try:
        with open(path, "r", encoding="utf8") as f:
            return safe_load(f.read())
    except Exception as e:
        print(f"Error, could not read {relpath}: {e}")
        sys.exit(1)

def normalize_sites(raw: Any) -> List[Dict[str, Any]]:
    """
    Accept:
      - list of dicts: [{'name':..., 'url':..., 'evidence_regex':[...]}]
      - list of strings: ['https://site/{user}', ...]
      - dict mapping: {'GitHub': 'https://...', 'Reddit': {'url':'...','evidence_regex':[...]} }
    Return: list of dicts with at least: {'name', 'url'}
    """
    out: List[Dict[str, Any]] = []
    if isinstance(raw, dict):
        for name, val in raw.items():
            if isinstance(val, str):
                out.append({"name": name, "url": val})
            elif isinstance(val, dict):
                d = {"name": name, **val}
                if "url" not in d and "template" in d:
                    d["url"] = d["template"]
                if "url" in d:
                    out.append(d)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                d = dict(item)
                if "url" not in d and "template" in d:
                    d["url"] = d["template"]
                if "name" not in d:
                    try:
                        d["name"] = urlparse(d.get("url", "")).netloc or "site"
                    except Exception:
                        d["name"] = "site"
                if "url" in d:
                    out.append(d)
            elif isinstance(item, str):
                dom = urlparse(item).netloc or item
                out.append({"name": dom, "url": item})
    else:
        print("sites.yml format not recognized. Use list/dict.")
        sys.exit(1)
    return out

_sites_raw = load_yaml("sites.yml")
sites: List[Dict[str, Any]] = normalize_sites(_sites_raw)
header_cfg: Dict[str, Any] = load_yaml("headers.yml") or {}

# -----------------------
# Pretty print
# -----------------------

USE_COLOR = True
def printc(rgb: Tuple[int,int,int], text: str):
    if not USE_COLOR:
        print(text, flush=True); return
    r, g, b = rgb
    print(f"\033[38;2;{r};{g};{b}m{text}\033[0m", flush=True)

GREEN = (38, 182, 82)
RED   = (250, 41, 41)
YEL   = (255, 211, 0)

# -----------------------
# HTTP Session factory
# -----------------------

def make_session(timeout: float, proxy: Optional[str]):
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.mount("https://", HTTPAdapter(max_retries=retry))

    base_headers = header_cfg.get("Base", {}) if isinstance(header_cfg, dict) else {}
    for k, v in base_headers.items():
        s.headers[k] = v

    uas = header_cfg.get("User-Agents") if isinstance(header_cfg, dict) else None
    if uas: s.headers["User-Agent"] = random.choice(uas)

    langs = header_cfg.get("Accept-Languages") if isinstance(header_cfg, dict) else None
    if langs: s.headers["Accept-Language"] = random.choice(langs)

    if proxy:
        s.proxies.update({"http": proxy, "https": proxy})

    s.request_timeout = timeout
    return s

# -----------------------
# Per-domain concurrency
# -----------------------

_domain_limits = defaultdict(lambda: 3)
_domain_inflight = defaultdict(int)

def _domain_guard(url: str):
    dom = urlparse(url).netloc
    while _domain_inflight[dom] >= _domain_limits[dom]:
        time.sleep(0.02)
    _domain_inflight[dom] += 1
    return dom

def _domain_release(dom: str):
    _domain_inflight[dom] -= 1
    if _domain_inflight[dom] < 0:
        _domain_inflight[dom] = 0

# -----------------------
# Core helpers
# -----------------------

def format_url(tmpl: str, username: str) -> str:
    return tmpl.replace("{!!}", username).replace("{user}", username)

def fetch_page(session: requests.Session, url: str):
    time.sleep(random.uniform(0.08, 0.25))  # jitter
    start = perf_counter()
    dom = _domain_guard(url)
    try:
        resp = session.get(url, timeout=session.request_timeout, allow_redirects=True)
        ms = int((perf_counter() - start) * 1000.0)
        return resp, ms
    except requests.RequestException as e:
        ms = int((perf_counter() - start) * 1000.0)
        return e, ms
    finally:
        _domain_release(dom)

def evidence_match(text: str, patterns: Optional[List[str]], username: str) -> bool:
    if not patterns:
        return True
    if isinstance(patterns, str):
        patterns = [patterns]
    for pat in patterns:
        pat = pat.replace("{user}", re.escape(username)).replace("{!!}", re.escape(username))
        try:
            if re.search(pat, text or "", flags=re.I | re.M):
                return True
        except re.error:
            continue
    return False

def scout_page(session, username: str, site: Dict[str, Any], ordinal: int, total: int, evidence_only: bool):
    """
    Perform the request and RETURN the formatted line & metadata.
    Printing happens in-order in the main thread.
    """
    name = site.get("name", "site")
    url = format_url(site["url"], username)

    res, elapsed = fetch_page(session, url)
    color = YEL
    status = "ERR"
    hit200 = False
    hit_verified = False

    if isinstance(res, requests.Response):
        status = str(res.status_code)
        if res.status_code == 200:
            hit200 = True
            if evidence_match(res.text, site.get("evidence_regex"), username):
                hit_verified = True
                color = GREEN
            else:
                color = YEL
        elif res.status_code == 404:
            color = RED
        else:
            color = YEL
    else:
        status = f"ERR: {type(res).__name__}"

    line = f"[{ordinal}/{total}] [ {status} ] ({elapsed}ms) {name}: {url}"
    should_save = hit_verified if evidence_only else hit200

    return {
        "site": name,
        "username": username,
        "url": url,
        "status": status,
        "ms": elapsed,
        "hit200": hit200,
        "hit": hit_verified,
        "save": should_save,
        "ordinal": ordinal,
        "total": total,
        "line": line,
        "color": color,
    }

# -----------------------
# Export helpers
# -----------------------

def export_jsonl(filepath: str, rows: List[Dict[str, Any]]):
    with open(filepath, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

def export_csv(filepath: str, rows: List[Dict[str, Any]]):
    fields = ["site", "username", "url", "status", "ms", "hit200", "hit"]
    with open(filepath, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})

# -----------------------
# Streaming links helpers
# -----------------------

_link_lock = threading.Lock()

def _ensure_parent(path: str):
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

def _load_existing_lines(path: str) -> set:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {line.rstrip("\n") for line in f if line.strip()}
    except FileNotFoundError:
        return set()

def _append_line_safe(fh, line: str, seen: set):
    if line in seen:
        return False
    with _link_lock:
        if line in seen:
            return False
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
        seen.add(line)
    return True

# -----------------------
# Runner
# -----------------------

def read_user_list(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def scout(
    usernames: List[str],
    threads: int,
    timeout: float,
    proxy: Optional[str],
    only_sites: Optional[set],
    hits_jsonl: Optional[str],
    csv_out: Optional[str],
    evidence_only: bool,
    links_out: Optional[str],
):
    session = make_session(timeout=timeout, proxy=proxy)

    # filter sites by --only (case-insensitive)
    if only_sites:
        only_lc = {s.lower() for s in only_sites}
        target_sites = [p for p in sites if p.get("name", "").lower() in only_lc]
        if not target_sites:
            print("Warning: --only matched no sites. Using all sites.")
            target_sites = sites
    else:
        target_sites = sites

    cpu = os.cpu_count() or 1
    max_threads = max(1, cpu * 5)
    thread_count = min(max(1, threads), max_threads)

    print_banner()
    print_howto()
    print("Scouting user(s):", usernames)
    print("Page count:", len(target_sites))
    print("Maximum threads:", thread_count)
    print("Headers:")
    print("========================================")
    print(safe_dump(dict(session.headers), indent=2).strip())
    print("========================================")

    tasks: List[Tuple[str, Dict[str, Any]]] = [(user, site) for user in usernames for site in target_sites]
    total = len(tasks)

    hits_to_save: List[Dict[str, Any]] = []
    futures = []
    start = perf_counter()

    link_fh = None
    link_seen = set()
    if links_out:
        _ensure_parent(links_out)
        link_seen = _load_existing_lines(links_out)
        link_fh = open(links_out, "a", encoding="utf-8")

    try:
        # Submit all tasks
        with ThreadPoolExecutor(max_workers=thread_count) as ex:
            for idx, (user, site) in enumerate(tasks, start=1):
                futures.append(ex.submit(scout_page, session, user, site, idx, total, evidence_only))

            # Ordered printer: buffer results and print in ordinal order
            results_buffer: Dict[int, Dict[str, Any]] = {}
            next_to_print = 1

            for fut in as_completed(futures):
                try:
                    result = fut.result()
                except Exception as e:
                    printc(RED, f"[ ERR ] (exception) {e}")
                    continue

                results_buffer[result["ordinal"]] = result

                # Print any consecutive ready results in order
                while next_to_print in results_buffer:
                    r = results_buffer.pop(next_to_print)

                    # in-order console line
                    printc(r["color"], r["line"])

                    # handle saving
                    if r.get("save"):
                        hits_to_save.append(r)
                        if link_fh:
                            _append_line_safe(link_fh, r["url"], link_seen)

                    next_to_print += 1

        dur = round(perf_counter() - start, 2)
        print("========================================")
        print(f"Completed {total} requests in {dur}s")
        print(f"Saved positives: {len(hits_to_save)} (mode: {'evidence-only' if evidence_only else 'any-200'})")

        if hits_jsonl:
            export_jsonl(hits_jsonl, hits_to_save)
            printc((110, 200, 255), f"Saved JSONL -> {hits_jsonl}")
        if csv_out:
            export_csv(csv_out, hits_to_save)
            printc((110, 200, 255), f"Saved CSV   -> {csv_out}")
        if link_fh:
            printc((110, 200, 255), f"Saved links -> {links_out}")
    finally:
        if link_fh:
            link_fh.close()

# -----------------------
# CLI
# -----------------------

def parse_args():
    ap = argparse.ArgumentParser(description="Scout users on popular websites.")
    ap.add_argument("usernames", nargs="*", help="One or more usernames")
    ap.add_argument("--userlist", type=str, help="File with one username per line")
    ap.add_argument("--threads", type=int, default=32, help="Max worker threads")
    ap.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout seconds")
    ap.add_argument("--proxy", type=str, help="HTTP/SOCKS proxy (e.g., socks5://127.0.0.1:9050)")
    ap.add_argument("--only", type=str, help="Comma-separated site names to include")
    ap.add_argument("--hits-out", type=str, help="Write positives to JSONL file (end-of-run)")
    ap.add_argument("--csv-out", type=str, help="Write positives to CSV file (end-of-run)")
    ap.add_argument("--links-out", type=str, default="hits.txt",
                    help="Append-only: write each positive URL immediately (default: hits.txt)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--evidence-only", dest="evidence_only", action="store_true",
                      help="Only save when evidence_regex matches (default)")
    mode.add_argument("--any-200", dest="evidence_only", action="store_false",
                      help="Count any HTTP 200 as a hit (ignore evidence_regex)")
    ap.set_defaults(evidence_only=True)
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    ap.add_argument("--count", action="store_true", help="Print site count and exit")
    ap.add_argument("--no-howto", action="store_true", help="Do not print the how-to guide at start")
    return ap.parse_args()

def _prompt_usernames_interactive() -> List[str]:
    try:
        raw = input("Enter username(s) (comma or space separated): ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(1)
    parts = re.split(r"[,\s]+", raw)
    return [p for p in (x.strip() for x in parts) if p]

def main():
    args = parse_args()
    global USE_COLOR
    USE_COLOR = not args.no_color

    if not args.no_howto and not args.count:
        print_howto()

    if args.count:
        print(len(sites))
        return

    usernames = list(args.usernames or [])
    if args.userlist:
        usernames.extend(read_user_list(args.userlist))

    if not usernames:
        print("No usernames supplied on CLI or via --userlist.")
        usernames = _prompt_usernames_interactive()
        if not usernames:
            print("No usernames entered. Exiting.")
            sys.exit(1)

    only_sites = set(map(str.strip, args.only.split(","))) if args.only else None

    scout(
        usernames=usernames,
        threads=args.threads,
        timeout=args.timeout,
        proxy=args.proxy,
        only_sites=only_sites,
        hits_jsonl=args.hits_out,
        csv_out=args.csv_out,
        evidence_only=args.evidence_only,
        links_out=args.links_out,
    )

if __name__ == "__main__":
    main()
