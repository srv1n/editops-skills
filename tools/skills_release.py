#!/usr/bin/env python3
"""
Release tooling for the `editops-skills` distribution repo.

Goals:
- Lint skills for Agent Skills spec compliance + repo conventions.
- Build per-skill zips and bundle zips (from .claude-plugin/marketplace.json).
- Emit SHA256SUMS for release assets.

Stdlib only (runs in GitHub Actions without extra deps).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILLS_ROOT = REPO_ROOT / "skills"
DEFAULT_MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?\n)---\s*\n", re.DOTALL)

FORBIDDEN_README = "README.md"
REQUIRED_MD_SECTION = "## Safety / Security"
TRIGGER_TESTS_REL = Path("references") / "TRIGGER_TESTS.md"
TRIGGER_TESTS_INLINE = "references/TRIGGER_TESTS.md"

# Keep exclusions tight; dist repo should not contain most of these anyway.
EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    "node_modules",
    ".venv",
    "venv",
}
EXCLUDE_FILES = {
    ".DS_Store",
}


@dataclass(frozen=True)
class SkillIssue:
    skill: str
    path: str
    message: str


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_frontmatter(text: str) -> str | None:
    m = FRONTMATTER_RE.search(text)
    if not m:
        return None
    return m.group("body")


def _yaml_value(front: str, key: str) -> str | None:
    # Minimal YAML "key: value" extraction for stable keys.
    pat = re.compile(rf"(?m)^{re.escape(key)}\s*:\s*(.+?)\s*$")
    m = pat.search(front)
    if not m:
        return None
    val = m.group(1).strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1]
    return val


def _has_yaml_key(front: str, key: str) -> bool:
    pat = re.compile(rf"(?m)^{re.escape(key)}\s*:\s*.+$")
    return bool(pat.search(front))


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except Exception:
        return str(path)


def lint_skill(skill_dir: Path, *, skills_root: Path) -> list[SkillIssue]:
    skill = skill_dir.name
    issues: list[SkillIssue] = []

    if not NAME_RE.match(skill):
        issues.append(
            SkillIssue(
                skill=skill,
                path=_rel(skill_dir, skills_root),
                message="Skill folder name must be kebab-case (a-z0-9 and hyphens only).",
            )
        )
    if len(skill) > 64:
        issues.append(
            SkillIssue(
                skill=skill,
                path=_rel(skill_dir, skills_root),
                message="Skill folder name must be ≤ 64 characters.",
            )
        )
    if "claude" in skill or "anthropic" in skill:
        issues.append(
            SkillIssue(
                skill=skill,
                path=_rel(skill_dir, skills_root),
                message="Skill folder name may not contain reserved words (claude/anthropic).",
            )
        )

    # No README.md anywhere inside a skill folder.
    for readme in skill_dir.rglob(FORBIDDEN_README):
        issues.append(
            SkillIssue(
                skill=skill,
                path=_rel(readme, skills_root),
                message="README.md is not allowed inside a skill folder. Move docs into SKILL.md or references/.",
            )
        )

    # No symlinks inside skills (zip safety).
    for p in skill_dir.rglob("*"):
        if p.is_symlink():
            issues.append(
                SkillIssue(
                    skill=skill,
                    path=_rel(p, skills_root),
                    message="Symlinks are not allowed inside a skill folder; copy the target content instead.",
                )
            )

    # Required: LICENSE.txt for distribution policy.
    if not (skill_dir / "LICENSE.txt").exists():
        issues.append(
            SkillIssue(
                skill=skill,
                path=_rel(skill_dir / "LICENSE.txt", skills_root),
                message="Missing LICENSE.txt (required for distribution).",
            )
        )

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        issues.append(
            SkillIssue(
                skill=skill,
                path=_rel(skill_md, skills_root),
                message="Missing SKILL.md",
            )
        )
        return issues

    text = _read_text(skill_md)
    front = _parse_frontmatter(text)
    if front is None:
        issues.append(
            SkillIssue(
                skill=skill,
                path=_rel(skill_md, skills_root),
                message="SKILL.md must start with YAML frontmatter (--- ... ---).",
            )
        )
        return issues

    # Spec: no angle brackets in frontmatter (security rule).
    if "<" in front or ">" in front:
        issues.append(
            SkillIssue(
                skill=skill,
                path=_rel(skill_md, skills_root),
                message="Frontmatter contains '<' or '>' which is disallowed by the Skills spec.",
            )
        )

    name = _yaml_value(front, "name")
    if not name:
        issues.append(
            SkillIssue(skill=skill, path=_rel(skill_md, skills_root), message="Frontmatter missing `name:`")
        )
    else:
        if name != skill:
            issues.append(
                SkillIssue(
                    skill=skill,
                    path=_rel(skill_md, skills_root),
                    message=f"Frontmatter name '{name}' must match the skill folder '{skill}'.",
                )
            )
        if not NAME_RE.match(name):
            issues.append(
                SkillIssue(
                    skill=skill,
                    path=_rel(skill_md, skills_root),
                    message="Frontmatter `name:` must be kebab-case (a-z0-9 and hyphens only).",
                )
            )
        if len(name) > 64:
            issues.append(
                SkillIssue(
                    skill=skill,
                    path=_rel(skill_md, skills_root),
                    message="Frontmatter `name:` must be ≤ 64 characters.",
                )
            )
        if "claude" in name or "anthropic" in name:
            issues.append(
                SkillIssue(
                    skill=skill,
                    path=_rel(skill_md, skills_root),
                    message="Frontmatter `name:` may not contain reserved words (claude/anthropic).",
                )
            )

    desc = _yaml_value(front, "description")
    if not desc:
        issues.append(
            SkillIssue(skill=skill, path=_rel(skill_md, skills_root), message="Frontmatter missing `description:`")
        )
    else:
        if len(desc) > 1024:
            issues.append(
                SkillIssue(
                    skill=skill,
                    path=_rel(skill_md, skills_root),
                    message="Frontmatter `description:` must be ≤ 1024 characters.",
                )
            )

    # Repo conventions (kept strict so distribution stays high quality).
    license_val = _yaml_value(front, "license")
    if license_val != "MIT":
        issues.append(
            SkillIssue(
                skill=skill,
                path=_rel(skill_md, skills_root),
                message="Frontmatter `license:` must be `MIT` for this repo.",
            )
        )

    comp = _yaml_value(front, "compatibility")
    if not comp:
        issues.append(
            SkillIssue(
                skill=skill,
                path=_rel(skill_md, skills_root),
                message="Frontmatter missing `compatibility:` (recommended for portability).",
            )
        )
    else:
        if len(comp) > 500:
            issues.append(
                SkillIssue(
                    skill=skill,
                    path=_rel(skill_md, skills_root),
                    message="Frontmatter `compatibility:` must be ≤ 500 characters.",
                )
            )

    if not _has_yaml_key(front, "metadata"):
        issues.append(
            SkillIssue(
                skill=skill,
                path=_rel(skill_md, skills_root),
                message="Frontmatter missing `metadata:` block (repo convention).",
            )
        )
    else:
        # Lightweight presence checks for required metadata keys.
        for req in ("author", "version", "category", "tags"):
            if not re.search(rf"(?m)^\s+{re.escape(req)}\s*:\s*.+$", front):
                issues.append(
                    SkillIssue(
                        skill=skill,
                        path=_rel(skill_md, skills_root),
                        message=f"Frontmatter metadata missing `{req}:` (repo convention).",
                    )
                )

    if REQUIRED_MD_SECTION not in text:
        issues.append(
            SkillIssue(
                skill=skill,
                path=_rel(skill_md, skills_root),
                message=f"Missing required section `{REQUIRED_MD_SECTION}` (repo convention).",
            )
        )

    trigger_tests_path = skill_dir / TRIGGER_TESTS_REL
    if not trigger_tests_path.exists():
        issues.append(
            SkillIssue(
                skill=skill,
                path=_rel(trigger_tests_path, skills_root),
                message="Missing references/TRIGGER_TESTS.md (repo convention).",
            )
        )
    if TRIGGER_TESTS_INLINE not in text:
        issues.append(
            SkillIssue(
                skill=skill,
                path=_rel(skill_md, skills_root),
                message="SKILL.md does not reference references/TRIGGER_TESTS.md (repo convention).",
            )
        )

    return issues


def lint_repo(*, skills_root: Path) -> list[SkillIssue]:
    issues: list[SkillIssue] = []
    if not skills_root.exists():
        return [
            SkillIssue(
                skill="(repo)",
                path=str(skills_root),
                message="Missing skills root directory.",
            )
        ]

    for sd in sorted([p for p in skills_root.iterdir() if p.is_dir()]):
        issues.extend(lint_skill(sd, skills_root=skills_root))
    return issues


def _iter_files_for_zip(root: Path) -> Iterable[Path]:
    for p in sorted(root.rglob("*")):
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        if p.name in EXCLUDE_FILES:
            continue
        if p.is_dir():
            continue
        yield p


def _sanitize_tag(tag: str) -> str:
    tag = (tag or "").strip()
    # Keep common semver tags like v0.1.0; drop any path separators.
    tag = tag.replace(os.sep, "_").replace("/", "_")
    return tag


def _zip_dir(src_dir: Path, out_zip: Path, *, arc_prefix: str) -> None:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in _iter_files_for_zip(src_dir):
            rel = p.relative_to(src_dir).as_posix()
            zf.write(p, arcname=f"{arc_prefix}/{rel}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_marketplace(path: Path) -> dict:
    data = json.loads(_read_text(path))
    if not isinstance(data, dict) or "plugins" not in data:
        raise ValueError("marketplace.json must be an object containing `plugins`")
    return data


def cmd_lint(args: argparse.Namespace) -> int:
    issues = lint_repo(skills_root=args.skills_root)
    if not issues:
        print(f"skills_release: lint ok ({len([p for p in args.skills_root.iterdir() if p.is_dir()])} skills)")
        return 0

    print(f"skills_release: lint failed ({len(issues)} issue(s))")
    by_skill: dict[str, list[SkillIssue]] = {}
    for i in issues:
        by_skill.setdefault(i.skill, []).append(i)

    for skill, items in sorted(by_skill.items(), key=lambda x: x[0]):
        print(f"- {skill}:")
        for it in items:
            print(f"  - {it.message} ({it.path})")
    return 1


def cmd_build_zips(args: argparse.Namespace) -> int:
    issues = lint_repo(skills_root=args.skills_root)
    if issues:
        # Reuse lint report for clarity.
        for i in issues[:5]:
            print(f"ERROR: {i.skill}: {i.message} ({i.path})", file=sys.stderr)
        print("ERROR: lint failed; refusing to build zips.", file=sys.stderr)
        return 1

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    tag = _sanitize_tag(args.tag) if args.tag else ""
    suffix = f"-{tag}" if tag else ""

    skills = sorted([p for p in args.skills_root.iterdir() if p.is_dir()])
    if args.skill:
        allowed = set(args.skill)
        missing = sorted([s for s in allowed if not (args.skills_root / s).exists()])
        if missing:
            print(f"ERROR: Unknown skill(s): {', '.join(missing)}", file=sys.stderr)
            return 2
        skills = [args.skills_root / s for s in sorted(allowed)]

    built: list[Path] = []

    for sd in skills:
        name = sd.name
        out_zip = out_dir / f"{name}{suffix}.zip"
        _zip_dir(sd, out_zip, arc_prefix=name)
        built.append(out_zip)
        print(f"Built {out_zip.name}")

    # Bundles (optional)
    if args.marketplace and args.marketplace.exists():
        marketplace = _load_marketplace(args.marketplace)
        for plugin in marketplace.get("plugins", []):
            if not isinstance(plugin, dict):
                continue
            bundle_name = str(plugin.get("name", "")).strip()
            skills_list = plugin.get("skills", [])
            if not bundle_name or not isinstance(skills_list, list):
                continue

            out_zip = out_dir / f"{bundle_name}{suffix}.zip"
            out_zip.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for sn in skills_list:
                    sn = str(sn)
                    src = args.skills_root / sn
                    if not src.exists():
                        raise ValueError(f"Bundle '{bundle_name}' references missing skill '{sn}'")
                    for p in _iter_files_for_zip(src):
                        rel = p.relative_to(src).as_posix()
                        zf.write(p, arcname=f"{sn}/{rel}")
            built.append(out_zip)
            print(f"Built {out_zip.name}")

    # SHA256SUMS
    sums_path = out_dir / "SHA256SUMS.txt"
    lines = []
    for p in sorted(built, key=lambda x: x.name):
        lines.append(f"{_sha256(p)}  {p.name}")
    sums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {sums_path.name}")

    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="editops-skills release tooling (lint + build zips)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_lint = sub.add_parser("lint", help="Lint skills for spec + repo conventions")
    ap_lint.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    ap_lint.set_defaults(fn=cmd_lint)

    ap_build = sub.add_parser("build-zips", help="Build per-skill and bundle zip assets")
    ap_build.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    ap_build.add_argument("--marketplace", type=Path, default=DEFAULT_MARKETPLACE)
    ap_build.add_argument("--out", dest="out_dir", type=Path, default=REPO_ROOT / "dist")
    ap_build.add_argument("--tag", type=str, default="")
    ap_build.add_argument("--skill", action="append", help="Limit to specific skill(s) (repeatable)")
    ap_build.set_defaults(fn=cmd_build_zips)

    args = ap.parse_args(argv)
    args.skills_root = args.skills_root.resolve()
    if hasattr(args, "marketplace") and getattr(args, "marketplace", None):
        args.marketplace = args.marketplace.resolve()
    if hasattr(args, "out_dir") and getattr(args, "out_dir", None):
        args.out_dir = args.out_dir.resolve()
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
