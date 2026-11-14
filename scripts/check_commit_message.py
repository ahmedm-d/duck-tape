"""Validate Conventional Commit subjects without third-party dependencies."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

SUBJECT = re.compile(r"^(feat|fix|docs|test|refactor|perf|build|ci|chore|revert)\([a-z][a-z0-9-]*\)!?: [a-z][^\r\n]+$")
REVISION_RANGE = re.compile(r"^[0-9a-f]{40}\.\.[0-9a-f]{40}$")


def valid(subject: str) -> bool:
    return len(subject) <= 72 and bool(SUBJECT.fullmatch(subject))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject")
    parser.add_argument("--message-file", type=Path)
    parser.add_argument("--title-env", help="Environment variable containing the PR title")
    parser.add_argument("--range-env", help="Environment variable containing base SHA..head SHA")
    args = parser.parse_args(argv)
    subjects = []
    try:
        if args.subject is not None:
            subjects.append(("subject", args.subject))
        if args.message_file:
            lines = args.message_file.read_text(encoding="utf-8-sig").splitlines()
            subjects.append(("commit", lines[0] if lines else ""))
        if args.title_env:
            subjects.append(("PR title", os.environ.get(args.title_env, "")))
        if args.range_env:
            revision_range = os.environ.get(args.range_env, "")
            if not REVISION_RANGE.fullmatch(revision_range):
                raise ValueError("Expected a complete base SHA..head SHA range")
            commits = subprocess.run(["git", "rev-list", "--no-merges", revision_range], check=True, capture_output=True, text=True).stdout.splitlines()
            for commit in commits:
                subject = subprocess.run(["git", "show", "-s", "--format=%s", commit], check=True, capture_output=True, text=True).stdout.rstrip("\n")
                subjects.append((commit[:8], subject))
        if not subjects:
            raise ValueError("Supply a subject, message file, PR title or commit range")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("Commit check could not run: " + type(exc).__name__, file=sys.stderr)
        return 1
    invalid = [label for label, subject in subjects if not valid(subject)]
    if invalid:
        print("Invalid commit subject at " + ", ".join(invalid) + ". Use type(scope): lowercase imperative subject, at most 72 characters.", file=sys.stderr)
        return 1
    print(f"Commit conventions passed ({len(subjects)} subject(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
