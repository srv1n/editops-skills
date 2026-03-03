#!/usr/bin/env python3
"""
Import a Markdown design spec into Beads (bd) as:
  - 1 epic (from the first H1)
  - N child issues (from each H2) under that epic

Markdown format:
  # Epic title

  ## Story title
  Optional freeform text (used as Description if no explicit sections)

  ### Type
  task|feature|bug|chore|epic

  ### Priority
  0-4 or P0-P4

  ### Description
  ...

  ### Design
  ...

  ### Acceptance Criteria
  ...

  ### Labels
  comma, separated, labels

  ### Dependencies
  blocks:bd-123, discovered-from:bd-456
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SUPPORTED_SECTIONS = {
    "priority": "priority",
    "type": "type",
    "description": "description",
    "design": "design",
    "acceptance criteria": "acceptance",
    "acceptance": "acceptance",
    "assignee": "assignee",
    "labels": "labels",
    "dependencies": "dependencies",
    "deps": "dependencies",
}


@dataclass
class IssueDraft:
    title: str
    freeform: str = ""
    fields: Dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str:
        return self.fields.get(key, "").strip()


def _split_csvish(value: str) -> List[str]:
    items: List[str] = []
    for part in value.replace("\n", " ").split(","):
        part = part.strip()
        if not part:
            continue
        items.append(part)
    return items


def parse_spec_markdown(path: Path) -> Tuple[Optional[str], List[IssueDraft]]:
    epic_title: Optional[str] = None
    stories: List[IssueDraft] = []

    current_story: Optional[IssueDraft] = None
    current_section: Optional[str] = None
    section_lines: List[str] = []

    def finalize_section() -> None:
        nonlocal current_section, section_lines, current_story
        if current_story is None or current_section is None:
            return
        normalized = SUPPORTED_SECTIONS.get(current_section.lower())
        if normalized is None:
            # Unknown section; ignore silently (design specs often have extra sections)
            section_lines = []
            current_section = None
            return
        content = "\n".join(section_lines).strip()
        if content:
            current_story.fields[normalized] = content
        section_lines = []
        current_section = None

    def finalize_story() -> None:
        nonlocal current_story, current_section, section_lines
        finalize_section()
        if current_story is None:
            return
        current_story.freeform = current_story.freeform.strip()
        stories.append(current_story)
        current_story = None
        current_section = None
        section_lines = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip("\n")

        if line.startswith("# "):
            # New epic title. Finalize any in-progress story first.
            finalize_story()
            if epic_title is None:
                epic_title = line[2:].strip()
            continue

        if line.startswith("## "):
            finalize_story()
            current_story = IssueDraft(title=line[3:].strip())
            continue

        if line.startswith("### "):
            if current_story is None:
                continue
            finalize_section()
            current_section = line[4:].strip()
            section_lines = []
            continue

        if current_story is None:
            continue

        if current_section is None:
            # Freeform content directly under H2 before any H3.
            if current_story.freeform:
                current_story.freeform += "\n"
            current_story.freeform += line
        else:
            section_lines.append(line)

    finalize_story()
    return epic_title, stories


def _parse_bd_json(stdout: str) -> Dict[str, Any]:
    """
    Parse `bd ... --json` output.

    `bd --json` prints a single JSON object, but it may be:
    - preceded by warnings (stdout)
    - pretty-printed across multiple lines

    So we extract the last {...} block from stdout and parse it.
    """
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("bd did not output JSON")
    blob = stdout[start : end + 1]
    return json.loads(blob)


def run_bd(args: List[str], *, env: Dict[str, str]) -> Dict[str, Any]:
    proc = subprocess.run(
        ["bd", *args],
        text=True,
        capture_output=True,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "bd command failed:\n"
            f"  cmd: bd {' '.join(args)}\n"
            f"  exit: {proc.returncode}\n"
            f"  stderr:\n{proc.stderr.strip()}\n"
            f"  stdout:\n{proc.stdout.strip()}\n"
        )
    return _parse_bd_json(proc.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a Markdown design spec into Beads (bd).")
    parser.add_argument("spec", type=Path, help="Path to a Markdown spec (H1 epic, H2 stories).")
    parser.add_argument(
        "--labels",
        default="",
        help="Comma-separated labels to apply to the epic and all created children (e.g. spec,creativeops).",
    )
    parser.add_argument("--epic-title", default="", help="Override epic title (otherwise uses first H1).")
    parser.add_argument("--epic-priority", default="2", help="Epic priority (0-4 or P0-P4). Default: 2.")
    parser.add_argument("--default-type", default="task", help="Default story type if unspecified. Default: task.")
    parser.add_argument("--default-priority", default="2", help="Default story priority if unspecified. Default: 2.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be created, but don't call bd.")
    parser.add_argument(
        "--write-mapping",
        default="",
        help="Write a JSON mapping file with created IDs (path).",
    )
    args = parser.parse_args()

    spec_path: Path = args.spec
    if not spec_path.exists():
        print(f"Spec not found: {spec_path}", file=sys.stderr)
        return 2
    if spec_path.suffix.lower() not in {".md", ".markdown"}:
        print("Spec must be a .md/.markdown file", file=sys.stderr)
        return 2

    epic_title, stories = parse_spec_markdown(spec_path)
    if args.epic_title.strip():
        epic_title = args.epic_title.strip()
    if not epic_title:
        print("No epic title found. Add a first-level heading like '# Epic title' or pass --epic-title.", file=sys.stderr)
        return 2
    if not stories:
        print("No stories found. Add second-level headings like '## Story title'.", file=sys.stderr)
        return 2

    common_labels = _split_csvish(args.labels)

    if args.dry_run:
        print(f"Epic: {epic_title} (priority {args.epic_priority})")
        print(f"Labels: {', '.join(common_labels) if common_labels else '(none)'}")
        print("")
        for story in stories:
            story_type = story.get("type") or args.default_type
            story_priority = story.get("priority") or args.default_priority
            story_labels = common_labels + _split_csvish(story.get("labels"))
            print(f"- {story.title} [{story_type}, P{story_priority}] labels={story_labels or '(none)'}")
        return 0

    env = dict(os.environ)
    env.setdefault("BD_ACTOR", env.get("USER", "codex"))

    epic_description = f"Spec: {spec_path.as_posix()}"
    epic_json = run_bd(
        [
            "create",
            epic_title,
            "--type",
            "epic",
            "--priority",
            str(args.epic_priority),
            "--description",
            epic_description,
            *(["--labels", ",".join(common_labels)] if common_labels else []),
            "--json",
        ],
        env=env,
    )
    epic_id = epic_json.get("id")
    if not epic_id:
        raise RuntimeError("bd create did not return an epic id")

    created: List[Dict[str, Any]] = []
    for story in stories:
        story_type = story.get("type") or args.default_type
        story_priority = story.get("priority") or args.default_priority
        description = story.get("description") or story.freeform.strip()
        design = story.get("design")
        acceptance = story.get("acceptance")
        assignee = story.get("assignee")
        deps = story.get("dependencies")

        story_labels = common_labels + _split_csvish(story.get("labels"))
        story_args: List[str] = [
            "create",
            story.title,
            "--parent",
            epic_id,
            "--type",
            story_type,
            "--priority",
            str(story_priority),
        ]
        if description:
            story_args += ["--description", description]
        if design:
            story_args += ["--design", design]
        if acceptance:
            story_args += ["--acceptance", acceptance]
        if assignee:
            story_args += ["--assignee", assignee]
        if story_labels:
            story_args += ["--labels", ",".join(story_labels)]
        if deps:
            story_args += ["--deps", ",".join(_split_csvish(deps))]
        story_args += ["--json"]

        story_json = run_bd(story_args, env=env)
        created.append(
            {
                "title": story.title,
                "id": story_json.get("id"),
                "type": story_type,
                "priority": story_priority,
            }
        )

    mapping = {"spec": spec_path.as_posix(), "epic": {"id": epic_id, "title": epic_title}, "children": created}
    if args.write_mapping:
        Path(args.write_mapping).write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(mapping, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
