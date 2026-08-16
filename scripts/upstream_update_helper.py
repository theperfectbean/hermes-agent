#!/usr/bin/env python3
"""Hermes Upstream Update and Release Automation Helper.

Usage:
  upstream_update_helper.py fetch
  upstream_update_helper.py new-branch <upstream-tag>
  upstream_update_helper.py diff <upstream-tag>
  upstream_update_helper.py review-bundle <upstream-tag> <out-path>
  upstream_update_helper.py tag <release-tag>
  upstream_update_helper.py build-stage <release-tag> <dest-dir>
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def run(cmd: str, cwd: Path | None = None) -> str:
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
    if res.returncode != 0:
        print(f"Command failed: {cmd}\nSTDERR: {res.stderr.strip()}", file=sys.stderr)
        sys.exit(res.returncode)
    return res.stdout.strip()


def get_repo_root() -> Path:
    return Path(run("git rev-parse --show-toplevel"))


def cmd_fetch(args: argparse.Namespace) -> None:
    root = get_repo_root()
    print("Fetching upstream branches and tags...")
    run("git fetch upstream --tags", cwd=root)
    run("git fetch origin", cwd=root)
    print("Fetch completed successfully.")


def cmd_new_branch(args: argparse.Namespace) -> None:
    root = get_repo_root()
    tag = args.upstream_tag
    branch_name = f"release/{tag}-ct132"
    print(f"Creating branch {branch_name} from upstream tag {tag}...")
    run(f"git checkout -B {branch_name} {tag}", cwd=root)
    print(f"Switched to branch {branch_name}.")


def cmd_diff(args: argparse.Namespace) -> None:
    root = get_repo_root()
    base_tag = args.upstream_tag
    print(f"=== Comparing current branch against {base_tag} ===")
    stat = run(f"git diff --stat {base_tag}..HEAD", cwd=root)
    print(stat if stat else "No changes detected.")


def cmd_review_bundle(args: argparse.Namespace) -> None:
    root = get_repo_root()
    base_tag = args.upstream_tag
    out_path = Path(args.out_path).resolve()
    
    current_head = run("git rev-parse HEAD", cwd=root)
    stat = run(f"git diff --stat {base_tag}..HEAD", cwd=root)
    diff = run(f"git diff {base_tag}..HEAD", cwd=root)
    
    bundle = f"""# Hermes Hardening Review Bundle

**Base Upstream Tag:** `{base_tag}`  
**Current Head:** `{current_head}`  
**Date:** {run('date -u +"%Y-%m-%dT%H:%M:%SZ"')}  

## Summary of Changes
```text
{stat}
```

## Complete Diff
```diff
{diff}
```
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        f.write(bundle)
    print(f"Review bundle written to {out_path}")


def cmd_tag(args: argparse.Namespace) -> None:
    root = get_repo_root()
    tag = args.release_tag
    print(f"Tagging release {tag}...")
    run(f"git tag -a {tag} -m 'Release {tag}: Hardened CT132 downstream release'", cwd=root)
    print(f"Tag {tag} created. Push with: git push origin {tag}")


def cmd_build_stage(args: argparse.Namespace) -> None:
    root = get_repo_root()
    tag = args.release_tag
    dest = Path(args.dest_dir).resolve()
    
    print(f"Staging release {tag} to {dest}...")
    dest.mkdir(parents=True, exist_ok=True)
    src_dest = dest / "src"
    src_dest.mkdir(parents=True, exist_ok=True)
    
    # Export archive from git
    run(f"git archive {tag} | tar -x -C {src_dest}", cwd=root)
    
    # Inject canonical ct132-safety-overlay
    overlay_src = Path("/home/admin/workspace/.agents/plugins/ct132-safety-overlay")
    if overlay_src.exists():
        overlay_dst = src_dest / "plugins" / "ct132-safety-overlay"
        overlay_dst.mkdir(parents=True, exist_ok=True)
        run(f"cp -a {overlay_src}/* {overlay_dst}/")
        print(f"Injected ct132-safety-overlay into {overlay_dst}")
    else:
        print("Warning: ct132-safety-overlay source directory not found", file=sys.stderr)
        
    print(f"Release staging for {tag} complete at {dest}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes Upstream Update and Release Helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # fetch
    p_fetch = subparsers.add_parser("fetch", help="Fetch upstream tags and branches")
    p_fetch.set_defaults(func=cmd_fetch)

    # new-branch
    p_branch = subparsers.add_parser("new-branch", help="Create new release branch from upstream tag")
    p_branch.add_argument("upstream_tag", help="Upstream tag (e.g. v2026.8.13 or v0.20.1)")
    p_branch.set_defaults(func=cmd_new_branch)

    # diff
    p_diff = subparsers.add_parser("diff", help="Show stat of local hardening against upstream tag")
    p_diff.add_argument("upstream_tag", help="Upstream tag")
    p_diff.set_defaults(func=cmd_diff)

    # review-bundle
    p_bundle = subparsers.add_parser("review-bundle", help="Generate review bundle for Gemini Pro")
    p_bundle.add_argument("upstream_tag", help="Upstream tag")
    p_bundle.add_argument("out_path", help="Output path for review markdown file")
    p_bundle.set_defaults(func=cmd_review_bundle)

    # tag
    p_tag = subparsers.add_parser("tag", help="Tag release")
    p_tag.add_argument("release_tag", help="Downstream release tag (e.g. v0.20.1-ct132.1)")
    p_tag.set_defaults(func=cmd_tag)

    # build-stage
    p_stage = subparsers.add_parser("build-stage", help="Stage release source with bundled safety plugin")
    p_stage.add_argument("release_tag", help="Release tag")
    p_stage.add_argument("dest_dir", help="Destination release directory (e.g. /opt/hermes/releases/<tag>)")
    p_stage.set_defaults(func=cmd_build_stage)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
