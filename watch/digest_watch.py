#!/usr/bin/env python3
"""
digest_watch.py — watch a folder for video files and digest them automatically.

Designed to be driven by launchd so you can drop a screen recording into a
synced folder from an iPad and get a report back without touching the Mac.

Everything is configurable. No behaviour is hardcoded: the watch folder, the
digest flags, how long to wait for a sync to finish, what happens to a clip
after it is processed, and which notifications fire are all read from a JSON
config file (default: ~/.movie-digest-watch.json).

Usage:
    digest_watch.py                     # one pass, using the default config
    digest_watch.py --config PATH       # one pass, using a specific config
    digest_watch.py --init              # write a default config and exit
    digest_watch.py --dry-run           # report what would be digested
    digest_watch.py --once FILE         # digest a single file, ignoring state

Python 3.8+. No third-party dependencies (the digest script has its own).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import smtplib
import subprocess
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.movie-digest-watch.json")

# --------------------------------------------------------------------------
# Defaults. Every key here can be overridden in the config file.
# --------------------------------------------------------------------------

DEFAULTS = {
    # --- where to look -----------------------------------------------------
    "watch_dir": "~/Library/Mobile Documents/com~apple~CloudDocs/MovieDigest",
    "extensions": [".mov", ".mp4", ".m4v", ".avi", ".mkv"],
    "recursive": False,

    # --- what to run -------------------------------------------------------
    # python_bin must be an interpreter that has faster-whisper installed.
    "python_bin": sys.executable,
    "digest_script": os.path.join(REPO_ROOT, "scripts", "digest_movie.py"),
    "digest_timeout_seconds": 3600,

    # --- digest options (mirror digest_movie.py flags) ---------------------
    "mode": "standard",
    "model": "small",
    "max_frames": 60,
    "analyze": False,
    "no_report": False,
    "no_transcribe": False,
    "no_frames": False,
    "language": None,
    # Anything else you want passed straight through, e.g. ["--frame-width","800"]
    "extra_args": [],
    # Read the mode out of the filename: "clip.strict.mov" -> --mode strict.
    "filename_mode_override": True,

    # --- where output goes -------------------------------------------------
    # null = alongside the video, as <video>.digest
    "output_dir": None,

    # --- sync safety -------------------------------------------------------
    # A file is only digested once its size has held steady this long.
    "stable_seconds": 20,
    "stability_poll_seconds": 2,
    "max_stability_wait_seconds": 900,
    # Ask iCloud to materialise .icloud placeholder files before digesting.
    "icloud_download": True,

    # --- after a successful digest ----------------------------------------
    # "leave" | "move" | "trash"
    "on_success": "leave",
    "archive_dir": None,

    # --- notifications (any combination) ----------------------------------
    # "status_file" | "macos" | "email"
    "notify": ["status_file"],
    "status_filename": "STATUS.md",
    "email": {
        "to": None,
        "from": None,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": None,
        # Name of an env var holding the password. Never put the password here.
        "smtp_password_env": "MOVIE_DIGEST_SMTP_PASSWORD",
        "attach_report": True,
    },

    # --- bookkeeping -------------------------------------------------------
    "state_file": "~/.movie-digest-watch-state.json",
    "lock_file": "~/.movie-digest-watch.lock",
    "log_file": "~/Library/Logs/movie-digest-watch.log",
    "log_level": "info",
    # Re-digest a file if it changes after having been processed.
    "reprocess_on_change": True,
    # How many times to retry a file that keeps failing before giving up on it.
    # A corrupt clip would otherwise be retried on every single pass, forever.
    # Editing the file resets the count, as does clearing the state file.
    "max_retries": 3,
}

MODE_CHOICES = ("insano", "strict", "standard", "lenient")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}


class Log:
    def __init__(self, path, level="info"):
        self.path = os.path.expanduser(path) if path else None
        self.threshold = LEVELS.get(str(level).lower(), 20)
        if self.path:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def __call__(self, level, msg):
        if LEVELS.get(level, 20) < self.threshold:
            return
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = "[%s] %-5s %s" % (stamp, level.upper(), msg)
        print(line, file=sys.stderr, flush=True)
        if self.path:
            try:
                with open(self.path, "a") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass

    def debug(self, m): self("debug", m)
    def info(self, m): self("info", m)
    def warn(self, m): self("warn", m)
    def error(self, m): self("error", m)


def expand(path):
    return os.path.abspath(os.path.expanduser(path)) if path else None


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def deep_merge(base, override):
    """Merge override into a copy of base, one level deep for dicts."""
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            merged = dict(out[key])
            merged.update(value)
            out[key] = merged
        else:
            out[key] = value
    return out


# --------------------------------------------------------------------------
# Config and state
# --------------------------------------------------------------------------

def load_config(path):
    path = expand(path)
    if not os.path.isfile(path):
        raise SystemExit(
            "No config at %s.\n"
            "Run:  %s --init\n"
            "to write a documented default you can edit." % (path, sys.argv[0])
        )
    with open(path) as fh:
        try:
            user_cfg = json.load(fh)
        except ValueError as exc:
            raise SystemExit("Config %s is not valid JSON: %s" % (path, exc))
    cfg = deep_merge(DEFAULTS, user_cfg)
    if cfg["mode"] not in MODE_CHOICES:
        raise SystemExit("mode must be one of %s" % (", ".join(MODE_CHOICES),))
    if cfg["on_success"] not in ("leave", "move", "trash"):
        raise SystemExit("on_success must be leave, move, or trash")
    if cfg["on_success"] == "move" and not cfg.get("archive_dir"):
        raise SystemExit("on_success=move requires archive_dir")
    return cfg


def write_default_config(path):
    path = expand(path)
    if os.path.exists(path):
        raise SystemExit("Refusing to overwrite existing config at %s" % path)
    with open(path, "w") as fh:
        json.dump(DEFAULTS, fh, indent=2)
        fh.write("\n")
    print("Wrote default config to %s" % path)
    print("Edit it, then run this script again.")


def load_state(cfg):
    path = expand(cfg["state_file"])
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(cfg, state):
    path = expand(cfg["state_file"])
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def is_icloud_placeholder(name):
    return name.startswith(".") and name.endswith(".icloud")


def placeholder_target(path):
    """~/dir/.clip.mov.icloud  ->  ~/dir/clip.mov"""
    d, name = os.path.split(path)
    return os.path.join(d, name[1:-len(".icloud")])


def request_icloud_download(path, log):
    """Ask iCloud to materialise a placeholder. Returns True if requested."""
    try:
        subprocess.run(["/usr/bin/brctl", "download", path],
                       check=False, capture_output=True, timeout=30)
        log.info("iCloud download requested: %s" % os.path.basename(path))
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        log.warn("brctl download failed for %s: %s" % (path, exc))
        return False


def discover(cfg, log):
    """Return candidate video paths in the watch folder."""
    root = expand(cfg["watch_dir"])
    if not os.path.isdir(root):
        raise SystemExit("watch_dir does not exist: %s" % root)

    exts = tuple(e.lower() for e in cfg["extensions"])
    archive = expand(cfg.get("archive_dir")) if cfg.get("archive_dir") else None
    found = []

    walker = os.walk(root) if cfg["recursive"] else [(root, [], os.listdir(root))]
    for dirpath, dirnames, filenames in walker:
        # Never descend into digest output or the archive.
        dirnames[:] = [d for d in dirnames
                       if not d.endswith(".digest") and not d.startswith(".")]
        if ".digest" in dirpath:
            continue
        if archive and os.path.abspath(dirpath).startswith(archive):
            continue
        for name in filenames:
            full = os.path.join(dirpath, name)
            if is_icloud_placeholder(name):
                target = placeholder_target(full)
                if target.lower().endswith(exts) and cfg["icloud_download"]:
                    request_icloud_download(full, log)
                continue
            if name.startswith("."):
                continue
            if not name.lower().endswith(exts):
                continue
            found.append(full)
    return sorted(found)


def file_key(path):
    st = os.stat(path)
    return {"size": st.st_size, "mtime": int(st.st_mtime)}


def needs_processing(path, state, cfg):
    """True if this file has never been digested, or changed since it was."""
    record = state.get(os.path.abspath(path))
    if not record:
        return True

    try:
        current = file_key(path)
    except OSError:
        return False
    changed = (current["size"] != record.get("size")
               or current["mtime"] != record.get("mtime"))

    if record.get("status") != "ok":
        # Retry a failure, but not forever — a corrupt clip that will never
        # decode would otherwise be retried on every pass. A changed file is
        # a new take, so it gets a fresh budget.
        if changed:
            return True
        return record.get("attempts", 1) < cfg["max_retries"]

    if not cfg["reprocess_on_change"]:
        return False
    return changed


def wait_until_stable(path, cfg, log):
    """
    Block until the file stops growing. A clip still syncing down from iCloud
    or Dropbox will otherwise be digested half-written.
    Returns True if the file settled, False if it timed out or vanished.
    """
    stable_needed = cfg["stable_seconds"]
    poll = max(0.5, float(cfg["stability_poll_seconds"]))
    deadline = time.time() + cfg["max_stability_wait_seconds"]

    last = None
    steady_since = None
    while time.time() < deadline:
        try:
            current = file_key(path)
        except OSError:
            log.warn("vanished while waiting: %s" % path)
            return False
        if current == last:
            if steady_since is None:
                steady_since = time.time()
            elif time.time() - steady_since >= stable_needed:
                return True
        else:
            steady_since = None
            last = current
        time.sleep(poll)

    log.warn("timed out waiting for %s to stop changing" % os.path.basename(path))
    return False


# --------------------------------------------------------------------------
# Running the digest
# --------------------------------------------------------------------------

def ensure_digest_config(log):
    """
    digest_movie.py runs an interactive first-run prompt when ~/.movie-digest.json
    is missing. Under launchd there is no terminal, so that prompt would hang or
    crash the run. Make sure the file exists before we invoke the script.
    """
    path = os.path.expanduser("~/.movie-digest.json")
    if os.path.isfile(path):
        return
    with open(path, "w") as fh:
        json.dump({"mode": "standard", "no_report": False}, fh, indent=2)
    log.info("seeded %s so the digest script won't prompt" % path)


def mode_from_filename(path, fallback):
    """clip.strict.mov -> 'strict'. Falls back when no mode token is present."""
    stem = os.path.splitext(os.path.basename(path))[0]
    for token in reversed(stem.split(".")):
        if token.lower() in MODE_CHOICES:
            return token.lower()
    return fallback


def output_dir_for(path, cfg):
    if cfg.get("output_dir"):
        base = expand(cfg["output_dir"])
        stem = os.path.splitext(os.path.basename(path))[0]
        return os.path.join(base, stem + ".digest")
    return path + ".digest"


def build_command(path, cfg):
    mode = cfg["mode"]
    if cfg["filename_mode_override"]:
        mode = mode_from_filename(path, mode)

    outdir = output_dir_for(path, cfg)
    cmd = [
        expand(cfg["python_bin"]) or cfg["python_bin"],
        expand(cfg["digest_script"]),
        path,
        "--out", outdir,
        "--mode", mode,
        "--model", str(cfg["model"]),
        "--max-frames", str(cfg["max_frames"]),
    ]
    if cfg.get("language"):
        cmd += ["--language", str(cfg["language"])]
    if cfg["analyze"]:
        cmd.append("--analyze")
    if cfg["no_report"]:
        cmd.append("--no-report")
    if cfg["no_transcribe"]:
        cmd.append("--no-transcribe")
    if cfg["no_frames"]:
        cmd.append("--no-frames")
    cmd += [str(a) for a in cfg.get("extra_args", [])]
    return cmd, outdir, mode


def run_digest(path, cfg, log):
    """Run digest_movie.py on one file. Returns (ok, outdir, detail)."""
    cmd, outdir, mode = build_command(path, cfg)
    os.makedirs(os.path.dirname(outdir) or ".", exist_ok=True)
    log.info("digesting %s (mode=%s)" % (os.path.basename(path), mode))
    log.debug("command: %s" % " ".join(cmd))

    env = dict(os.environ)
    env.setdefault("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
    if "/opt/homebrew/bin" not in env["PATH"]:
        env["PATH"] = "/opt/homebrew/bin:" + env["PATH"]

    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,          # never let the script prompt
            capture_output=True,
            text=True,
            timeout=cfg["digest_timeout_seconds"],
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, outdir, "timed out after %ss" % cfg["digest_timeout_seconds"]
    except OSError as exc:
        return False, outdir, "could not launch digest script: %s" % exc

    elapsed = int(time.time() - started)
    tail = (proc.stderr or "").strip().splitlines()
    tail = "\n".join(tail[-15:])

    if proc.returncode != 0:
        log.error("digest failed (exit %s) for %s" % (proc.returncode, path))
        if tail:
            log.error(tail)
        return False, outdir, "exit %s\n%s" % (proc.returncode, tail)

    log.info("done in %ss -> %s" % (elapsed, outdir))
    return True, outdir, "completed in %ss" % elapsed


def post_process(path, cfg, log):
    """Move or trash the source clip after a successful digest."""
    action = cfg["on_success"]
    if action == "leave":
        return
    if action == "move":
        dest_dir = expand(cfg["archive_dir"])
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, os.path.basename(path))
        if os.path.exists(dest):
            stem, ext = os.path.splitext(os.path.basename(path))
            dest = os.path.join(dest_dir, "%s-%s%s" % (
                stem, datetime.now().strftime("%Y%m%d-%H%M%S"), ext))
        shutil.move(path, dest)
        log.info("archived source -> %s" % dest)
    elif action == "trash":
        trash = os.path.expanduser("~/.Trash")
        dest = os.path.join(trash, os.path.basename(path))
        try:
            shutil.move(path, dest)
            log.info("moved source to Trash")
        except OSError as exc:
            log.warn("could not trash %s: %s" % (path, exc))


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------

def notify_macos(title, message, log):
    script = 'display notification %s with title %s' % (
        json.dumps(message), json.dumps(title))
    try:
        subprocess.run(["/usr/bin/osascript", "-e", script],
                       check=False, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warn("osascript notification failed: %s" % exc)


def notify_email(cfg, subject, body, report_path, log):
    email = cfg.get("email") or {}
    to_addr = email.get("to")
    from_addr = email.get("from") or to_addr
    if not to_addr:
        log.warn("email notify enabled but email.to is not set")
        return
    password = os.environ.get(email.get("smtp_password_env") or "", "")
    if not password:
        log.warn("no SMTP password in $%s — skipping email"
                 % email.get("smtp_password_env"))
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body)

    if email.get("attach_report") and report_path and os.path.isfile(report_path):
        try:
            with open(report_path, "rb") as fh:
                msg.add_attachment(fh.read(), maintype="text", subtype="html",
                                   filename=os.path.basename(report_path))
        except OSError as exc:
            log.warn("could not attach report: %s" % exc)

    try:
        with smtplib.SMTP(email["smtp_host"], int(email["smtp_port"]), timeout=30) as smtp:
            smtp.starttls()
            smtp.login(email.get("smtp_user") or from_addr, password)
            smtp.send_message(msg)
        log.info("emailed report to %s" % to_addr)
    except Exception as exc:  # smtplib raises a wide variety
        log.warn("email failed: %s" % exc)


def write_status_file(cfg, entries, log):
    """A plain-text status board readable from the iPad Files app."""
    root = expand(cfg["watch_dir"])
    path = os.path.join(root, cfg["status_filename"])
    lines = ["# movie-digest status", "",
             "Last run: %s" % now_iso(), ""]
    if not entries:
        lines.append("Nothing to do — no new clips.")
    for entry in entries:
        icon = "OK  " if entry["ok"] else "FAIL"
        lines.append("- **%s** — %s" % (entry["name"], icon))
        lines.append("  - %s" % entry["detail"].replace("\n", "\n    "))
        if entry.get("report"):
            lines.append("  - report: `%s`" % entry["report"])
    lines.append("")
    try:
        with open(path, "w") as fh:
            fh.write("\n".join(lines))
    except OSError as exc:
        log.warn("could not write status file: %s" % exc)


def dispatch_notifications(cfg, entries, log):
    channels = cfg.get("notify") or []
    if "status_file" in channels:
        write_status_file(cfg, entries, log)
    if not entries:
        return
    ok_count = sum(1 for e in entries if e["ok"])
    fail_count = len(entries) - ok_count
    summary = "%d digested, %d failed" % (ok_count, fail_count)
    if "macos" in channels:
        notify_macos("movie-digest", summary, log)
    if "email" in channels:
        for entry in entries:
            status = "ready" if entry["ok"] else "FAILED"
            notify_email(
                cfg,
                "movie-digest: %s %s" % (entry["name"], status),
                entry["detail"],
                entry.get("report"),
                log,
            )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def acquire_lock(cfg, log):
    """Single instance only — launchd can fire us again mid-digest."""
    path = expand(cfg["lock_file"])
    handle = open(path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.info("another run is in progress — exiting")
        handle.close()
        return None
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def process_one(path, cfg, state, log, dry_run=False):
    name = os.path.basename(path)
    if dry_run:
        cmd, outdir, mode = build_command(path, cfg)
        print("would digest: %s (mode=%s) -> %s" % (name, mode, outdir))
        return None

    if not wait_until_stable(path, cfg, log):
        return {"name": name, "ok": False,
                "detail": "file never stopped changing (still syncing?)",
                "report": None}

    ok, outdir, detail = run_digest(path, cfg, log)
    report = os.path.join(outdir, "report.html")
    if not os.path.isfile(report):
        report = None

    key = os.path.abspath(path)
    previous = state.get(key) or {}
    try:
        unchanged = (previous.get("size") == os.stat(path).st_size
                     and previous.get("mtime") == int(os.stat(path).st_mtime))
    except OSError:
        unchanged = False
    attempts = (previous.get("attempts", 0) + 1) if unchanged else 1

    record = {"status": "ok" if ok else "error", "when": now_iso(),
              "output_dir": outdir, "detail": detail, "attempts": attempts}
    try:
        record.update(file_key(path))
    except OSError:
        pass
    state[key] = record

    if not ok and attempts >= cfg["max_retries"]:
        log.warn("giving up on %s after %d attempts" % (name, attempts))

    if ok:
        post_process(path, cfg, log)
    return {"name": name, "ok": ok, "detail": detail, "report": report}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Watch a folder and digest videos dropped into it.")
    ap.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                    help="Config file (default: %s)" % DEFAULT_CONFIG_PATH)
    ap.add_argument("--init", action="store_true",
                    help="Write a documented default config and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="List what would be digested, then exit")
    ap.add_argument("--once", metavar="FILE",
                    help="Digest a single file regardless of stored state")
    args = ap.parse_args(argv)

    if args.init:
        write_default_config(args.config)
        return 0

    cfg = load_config(args.config)
    log = Log(cfg["log_file"], cfg["log_level"])

    script = expand(cfg["digest_script"])
    if not os.path.isfile(script):
        log.error("digest_script not found: %s" % script)
        return 2

    lock = acquire_lock(cfg, log)
    if lock is None:
        return 0

    try:
        ensure_digest_config(log)
        state = load_state(cfg)

        if args.once:
            targets = [expand(args.once)]
        else:
            targets = [p for p in discover(cfg, log)
                       if needs_processing(p, state, cfg)]

        if not targets:
            log.debug("nothing new in %s" % expand(cfg["watch_dir"]))

        entries = []
        for path in targets:
            result = process_one(path, cfg, state, log, dry_run=args.dry_run)
            if result:
                entries.append(result)

        if not args.dry_run:
            save_state(cfg, state)
            dispatch_notifications(cfg, entries, log)

        return 0 if all(e["ok"] for e in entries) else 1
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    sys.exit(main())
