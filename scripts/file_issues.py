#!/usr/bin/env python3
"""
file_issues.py — file selected QA-digest findings as GitHub issues.

Meant to be driven by Claude Code from inside the project being QA'd: Claude
reads digest.md, proposes the bugs it found, you pick the ones worth filing,
and this script does the mechanical part.

Keyframes referenced by a bug are pushed to an orphan branch (default
`qa-assets`) and rewritten as raw URLs, because the GitHub REST API cannot
attach images to an issue the way the web UI can.

Input is a JSON file describing the issues to create:

    [
      {
        "title": "Grid connector drops on second placement",
        "body":  "**Steps**\\n1. ...\\n\\n**Expected** ...\\n\\n**Actual** ...",
        "frames": ["frames/0012_00h01m04s.jpg"],
        "labels": ["bug", "needs-triage"]
      }
    ]

`frames` are paths relative to the digest directory (or absolute).

Usage:
    file_issues.py --digest DIR --issues bugs.json [--repo owner/name]
    file_issues.py --digest DIR --issues bugs.json --dry-run

Requires the `gh` CLI, authenticated with `repo` scope.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import subprocess
import sys
import time

DEFAULT_ASSETS_BRANCH = "qa-assets"
RAW_URL = "https://raw.githubusercontent.com/{repo}/{branch}/{path}"


def eprint(*a):
    print(*a, file=sys.stderr, flush=True)


class GhError(RuntimeError):
    pass


def gh(*args, stdin=None, parse=True):
    """Run a gh command and optionally parse JSON from stdout."""
    proc = subprocess.run(["gh"] + list(args), input=stdin, capture_output=True,
                          text=True)
    if proc.returncode != 0:
        raise GhError((proc.stderr or proc.stdout or "").strip())
    out = (proc.stdout or "").strip()
    if not parse or not out:
        return out
    try:
        return json.loads(out)
    except ValueError:
        return out


def require_gh():
    try:
        subprocess.run(["gh", "--version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        raise SystemExit("ERROR: the gh CLI is required. brew install gh")
    try:
        gh("auth", "status", parse=False)
    except GhError as exc:
        raise SystemExit("ERROR: gh is not authenticated.\n"
                         "Run: gh auth login\n%s" % exc)


def parse_remote_url(url):
    """
    git remote URL -> "owner/name", or None if it isn't a GitHub remote.

    Handles the three shapes git hands out:
        https://github.com/owner/name.git
        git@github.com:owner/name.git
        ssh://git@github.com/owner/name.git
    """
    if not url:
        return None
    url = url.strip()
    if url.endswith(".git"):
        url = url[:-4]
    if "github.com" not in url:
        return None
    if url.startswith("git@"):
        _, _, path = url.partition(":")
    else:
        marker = "github.com"
        path = url[url.index(marker) + len(marker):].lstrip("/:")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    return "%s/%s" % (parts[-2], parts[-1])


def detect_repo(explicit=None):
    """
    owner/name for the repo we're filing against.

    Read the git remote directly rather than asking gh. `gh repo view --json`
    goes through GraphQL, which has its own hourly quota separate from REST --
    so it can fail while every call this script actually needs still works.
    Parsing the remote costs nothing and works offline.
    """
    if explicit:
        return explicit
    try:
        url = subprocess.run(["git", "remote", "get-url", "origin"],
                             capture_output=True, text=True).stdout
    except OSError:
        url = ""
    repo = parse_remote_url(url)
    if repo:
        return repo
    raise SystemExit(
        "ERROR: could not work out which repo to file against.\n"
        "No GitHub 'origin' remote in %s.\n"
        "Run this from inside the project's git checkout, or pass "
        "--repo owner/name." % os.getcwd())


def repo_is_private(repo):
    """REST, not GraphQL -- see the note in detect_repo."""
    try:
        return bool(api("GET", "/repos/%s" % repo).get("private"))
    except (GhError, AttributeError, TypeError):
        return False


# --------------------------------------------------------------------------
# Asset branch
# --------------------------------------------------------------------------

def api(method, path, payload=None):
    args = ["api", "--method", method, path]
    stdin = None
    if payload is not None:
        args += ["--input", "-"]
        stdin = json.dumps(payload)
    return gh(*args, stdin=stdin)


def branch_exists(repo, branch):
    try:
        api("GET", "/repos/%s/git/ref/heads/%s" % (repo, branch))
        return True
    except GhError:
        return False


def create_orphan_branch(repo, branch):
    """
    Create an empty branch with no history in common with main, so QA
    screenshots never touch the code history.
    """
    readme = ("# QA assets\n\nScreenshots referenced by issues filed from "
              "qa-digest.\nThis branch is intentionally orphaned - it shares "
              "no history with the code.\n")
    blob = api("POST", "/repos/%s/git/blobs" % repo,
               {"content": base64.b64encode(readme.encode()).decode(),
                "encoding": "base64"})
    tree = api("POST", "/repos/%s/git/trees" % repo,
               {"tree": [{"path": "README.md", "mode": "100644",
                          "type": "blob", "sha": blob["sha"]}]})
    commit = api("POST", "/repos/%s/git/commits" % repo,
                 {"message": "Initialise QA asset branch", "tree": tree["sha"],
                  "parents": []})
    api("POST", "/repos/%s/git/refs" % repo,
        {"ref": "refs/heads/%s" % branch, "sha": commit["sha"]})
    eprint("[assets] created orphan branch %s" % branch)


def upload_frame(repo, branch, local_path, remote_path):
    with open(local_path, "rb") as fh:
        content = base64.b64encode(fh.read()).decode()
    payload = {"message": "Add QA frame %s" % os.path.basename(remote_path),
               "content": content, "branch": branch}
    # If it's somehow already there, the API needs the blob sha to replace it.
    try:
        existing = api("GET", "/repos/%s/contents/%s?ref=%s"
                       % (repo, remote_path, branch))
        if isinstance(existing, dict) and existing.get("sha"):
            payload["sha"] = existing["sha"]
    except GhError:
        pass
    result = api("PUT", "/repos/%s/contents/%s" % (repo, remote_path), payload)
    return result["content"]["path"]


# --------------------------------------------------------------------------
# Issue assembly
# --------------------------------------------------------------------------

def load_issues(path):
    with open(path) as fh:
        try:
            data = json.load(fh)
        except ValueError as exc:
            raise SystemExit("ERROR: %s is not valid JSON: %s" % (path, exc))
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not data:
        raise SystemExit("ERROR: expected a non-empty JSON list of issues.")
    for i, issue in enumerate(data, 1):
        if not issue.get("title"):
            raise SystemExit("ERROR: issue %d has no title." % i)
        if not issue.get("body"):
            raise SystemExit("ERROR: issue %d (%s) has no body."
                             % (i, issue["title"]))
    return data


def resolve_frame(digest_dir, frame):
    path = frame if os.path.isabs(frame) else os.path.join(digest_dir, frame)
    if not os.path.isfile(path):
        raise SystemExit("ERROR: frame not found: %s" % path)
    if mimetypes.guess_type(path)[0] not in ("image/jpeg", "image/png"):
        raise SystemExit("ERROR: not a jpg/png image: %s" % path)
    return path


def remote_path_for(digest_dir, local_path, stamp):
    """Namespace frames by digest run so two runs never collide."""
    slug = os.path.basename(os.path.normpath(digest_dir)).replace(".digest", "")
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in slug)
    return "frames/%s-%s/%s" % (slug, stamp, os.path.basename(local_path))


def build_body(issue, image_urls, repo_private):
    body = issue["body"].rstrip()
    if not image_urls:
        return body
    parts = [body, "", "---", "", "### Keyframes", ""]
    for url, caption in image_urls:
        parts.append("![%s](%s)" % (caption, url))
        parts.append("")
    if repo_private:
        parts.append("> Note: this repo is private, so the images above only "
                     "render for users signed in with access. GitHub's raw "
                     "URLs are not public for private repos.")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="File selected qa-digest findings as GitHub issues.")
    ap.add_argument("--digest", required=True,
                    help="The .digest directory the findings came from")
    ap.add_argument("--issues", required=True,
                    help="JSON file describing the issues to create")
    ap.add_argument("--repo", default=None,
                    help="owner/name (default: inferred from the current repo)")
    ap.add_argument("--assets-branch", default=DEFAULT_ASSETS_BRANCH,
                    help="Orphan branch for keyframes (default: %s)"
                         % DEFAULT_ASSETS_BRANCH)
    ap.add_argument("--no-images", action="store_true",
                    help="File text-only issues; skip uploading keyframes")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would be filed, upload nothing")
    args = ap.parse_args(argv)

    digest_dir = os.path.abspath(os.path.expanduser(args.digest))
    if not os.path.isdir(digest_dir):
        raise SystemExit("ERROR: no such digest directory: %s" % digest_dir)

    issues = load_issues(args.issues)

    # Fail on a missing frame before anything is uploaded or filed.
    for issue in issues:
        issue["_frames"] = [resolve_frame(digest_dir, f)
                            for f in (issue.get("frames") or [])
                            if not args.no_images]

    if args.dry_run:
        repo = args.repo or "(inferred at run time)"
        print("Would file %d issue(s) against %s\n" % (len(issues), repo))
        for i, issue in enumerate(issues, 1):
            print("%d. %s" % (i, issue["title"]))
            if issue.get("labels"):
                print("   labels: %s" % ", ".join(issue["labels"]))
            print("   frames: %d" % len(issue["_frames"]))
            first = issue["body"].strip().splitlines()[0] if issue["body"] else ""
            print("   body:   %s..." % first[:70])
            print()
        return 0

    require_gh()
    repo = detect_repo(args.repo)
    private = repo_is_private(repo)
    eprint("[repo] %s%s" % (repo, " (private)" if private else ""))

    needs_assets = any(issue["_frames"] for issue in issues)
    if needs_assets and not branch_exists(repo, args.assets_branch):
        create_orphan_branch(repo, args.assets_branch)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    created = []
    for issue in issues:
        image_urls = []
        for local in issue["_frames"]:
            remote = remote_path_for(digest_dir, local, stamp)
            eprint("[assets] uploading %s" % os.path.basename(local))
            upload_frame(repo, args.assets_branch, local, remote)
            image_urls.append((
                RAW_URL.format(repo=repo, branch=args.assets_branch,
                               path=remote),
                os.path.basename(local)))

        body = build_body(issue, image_urls, private)
        cmd = ["issue", "create", "--repo", repo,
               "--title", issue["title"], "--body", body]
        for label in issue.get("labels") or []:
            cmd += ["--label", label]
        try:
            url = gh(*cmd, parse=False)
        except GhError as exc:
            # A missing label is the usual culprit and it's recoverable.
            if "label" in str(exc).lower() and issue.get("labels"):
                eprint("[warn] labels rejected (%s) - filing without them"
                       % ", ".join(issue["labels"]))
                url = gh("issue", "create", "--repo", repo,
                         "--title", issue["title"], "--body", body, parse=False)
            else:
                raise SystemExit("ERROR: could not create issue %r:\n%s"
                                 % (issue["title"], exc))
        eprint("[filed] %s" % url)
        created.append({"title": issue["title"], "url": url})

    print(json.dumps({"repo": repo, "created": created}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
