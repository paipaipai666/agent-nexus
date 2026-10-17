"""Generate desktop setting constraints from the authoritative settings model."""

import argparse
import json
from pathlib import Path

from pydantic import SecretStr

from agentnexus.core.config import Settings
from agentnexus.services.configuration import SETTABLE_KEYS


def generate():
    """Return the TypeScript setting metadata used by the desktop form."""
    root = Path(__file__).resolve().parents[1]
    labels = json.loads((root / "desktop/settings-labels.json").read_text(encoding="utf-8"))
    result = []
    for key, presentation in labels.items():
        field = Settings.model_fields[key]
        kind = "text"
        if field.annotation is SecretStr:
            kind = "password"
        elif field.annotation is bool:
            kind = "boolean"
        elif field.annotation in (int, float):
            kind = "number"
        item = {
            "key": key, "group": presentation[0], "label": presentation[1],
            "description": presentation[2], "unit": presentation[3] if len(presentation) > 3 else "",
            "common": presentation[4] if len(presentation) > 4 else False,
            "kind": kind, "editable": key in SETTABLE_KEYS,
            "defaultValue": "" if kind == "password" else field.get_default(call_default_factory=True),
        }
        if kind == "number":
            item["step"] = 1 if field.annotation is int else 0.001
            for constraint in field.metadata:
                if hasattr(constraint, "ge"):
                    item["min"] = constraint.ge
                if hasattr(constraint, "le"):
                    item["max"] = constraint.le
        result.append(item)
    header = """export interface SettingField {
  key: string
  group: string
  label: string
  description: string
  unit: string
  common: boolean
  kind: 'text' | 'password' | 'boolean' | 'number'
  editable: boolean
  defaultValue: unknown
  min?: number
  max?: number
  step?: number
}

export const settingFields: SettingField[] = """
    return root / "desktop/src/services/settingsFields.ts", header + json.dumps(result, ensure_ascii=False, indent=2) + "\n"


def main():
    """Write the generated file, or verify it without changing files."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target, content = generate()
    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            raise SystemExit("Desktop settings metadata needs regeneration.")
        print("Desktop settings metadata matches the backend model.")
    else:
        target.write_text(content, encoding="utf-8")
        print(f"Generated {target}")


if __name__ == "__main__":
    main()
