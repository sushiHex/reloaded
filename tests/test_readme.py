from __future__ import annotations

import pathlib

from reloaded.__main__ import build_parser

README = pathlib.Path(__file__).resolve().parents[1] / "README.md"


def _subcommand_names() -> list[str]:
    for action in build_parser()._actions:
        if getattr(action, "choices", None):
            return list(action.choices.keys())
    raise AssertionError("no subparsers action found on the CLI parser")


def test_every_subcommand_is_documented_in_the_readme():
    """The README's Commands table is hand-written prose, not generated from
    argparse — nothing wires the two together. Without this, adding, renaming,
    or removing a subcommand only needs to touch __main__.py to silently leave
    the README wrong; this fails CI instead of letting that drift unnoticed."""
    text = README.read_text(encoding="utf-8")
    names = _subcommand_names()
    assert names, "sanity check: the parser must actually have subcommands"
    for name in names:
        assert f"`reloaded {name}" in text, (
            f"'{name}' is a real subcommand but isn't mentioned in README.md's Commands table"
        )
