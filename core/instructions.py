"""Turns prompts/*.xml into a system prompt. The XML is the source of truth; this just flattens it."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from config.settings import settings


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _render(el: ET.Element, depth: int = 0) -> list[str]:
    lines: list[str] = []
    indent = "  " * depth
    attrs = " ".join(f"{k}={v}" for k, v in el.attrib.items())
    body = _clean(el.text)
    children = list(el)
    if not children:
        label = el.tag.replace("_", " ")
        line = f"{indent}- {label}" + (f" [{attrs}]" if attrs else "") + (f": {body}" if body else "")
        lines.append(line)
        return lines
    header = f"{indent}{el.tag.replace('_', ' ').upper()}" + (f" [{attrs}]" if attrs else "")
    lines.append(header)
    if body:
        lines.append(f"{indent}  {body}")
    for c in children:
        lines.extend(_render(c, depth + 1))
    return lines


def load_instructions(name: str) -> tuple[str, dict[str, str]]:
    """Return (system_prompt, agent_attributes) for prompts/<name>.xml."""
    path: Path = settings.prompts_dir / f"{name}.xml"
    root = ET.parse(path).getroot()
    sections = []
    for section in root:
        sections.append("\n".join(_render(section)))
    prompt = f"AGENT {root.attrib.get('name', name)} v{root.attrib.get('version', '?')}\n\n" + "\n\n".join(sections)
    return prompt, dict(root.attrib)
