"""The dangling-editable-install gate, driven by fabricated site-packages trees.

No venv is created and nothing is installed: the two shapes setuptools writes
are just text files, so a temp directory reproduces both exactly. That keeps
this honest about the thing it checks -- whether a mapped path still holds an
importable package -- without a 500MB dependency on being right.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_venv import broken, mapped_paths  # noqa: E402


def _package(root: Path, name: str = "kinesis") -> Path:
    pkg = root / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    return pkg


def test_reads_the_finder_module_mapping(tmp_path):
    site = tmp_path / "site-packages"
    site.mkdir()
    tree = tmp_path / "checkout"
    pkg = _package(tree)
    (site / "__editable___kinesis_0_1_0_finder.py").write_text(
        f"MAPPING = {{'kinesis': {str(pkg)!r}}}\n"
    )
    assert mapped_paths(site) == [pkg]


def test_reads_the_pth_directory_listing(tmp_path):
    site = tmp_path / "site-packages"
    site.mkdir()
    tree = tmp_path / "checkout"
    _package(tree)
    (site / "__editable__.kinesis-0.1.0.pth").write_text(f"{tree}\n")
    assert mapped_paths(site) == [tree / "kinesis"]


def test_no_editable_install_is_not_a_finding(tmp_path):
    site = tmp_path / "site-packages"
    site.mkdir()
    assert mapped_paths(site) == []
    assert broken([]) == []


def test_a_live_mapping_is_not_broken(tmp_path):
    pkg = _package(tmp_path / "checkout")
    assert broken([pkg]) == []


def test_a_deleted_worktree_is_broken(tmp_path):
    """The failure this gate exists for: the tree the mapping names is gone."""
    assert broken([tmp_path / "removed-worktree" / "kinesis"]) == [
        tmp_path / "removed-worktree" / "kinesis"
    ]


def test_a_directory_without_init_is_broken(tmp_path):
    """A leftover empty directory must not read as a healthy install."""
    hollow = tmp_path / "checkout" / "kinesis"
    hollow.mkdir(parents=True)
    assert broken([hollow]) == [hollow]
