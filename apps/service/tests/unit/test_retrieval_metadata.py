from __future__ import annotations

import json
from pathlib import Path

import pytest

from domain.retrieval_metadata import normalize_index_metadata, normalize_query_scope


def test_index_and_query_share_the_same_explicit_scope_normalization() -> None:
    indexed = normalize_index_metadata(
        "英语/七年级上册/第一单元/教材/语法.md",
        ("第一单元", "语法"),
    )
    queried = normalize_query_scope("请汇总英语七年级上册第一单元教材的知识点")

    assert indexed == queried
    assert indexed.subject == "英语"
    assert indexed.grade_volume == "七年级上册"
    assert indexed.unit_no == 1
    assert indexed.material_type == "textbook"
    assert indexed.scope_status == "resolved"


def test_authorized_vault_directory_samples_fail_closed_when_scope_is_not_explicit() -> None:
    fixture_path = (
        Path(__file__).parents[4] / "docs/fixtures/ret-00-02-vault-directory-samples.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    cases = {case["caseId"]: case for case in fixture["cases"]}
    root_case = cases["RV-LIVE-01"]
    derived_case = cases["RV-INDEX-01"]
    location_case = cases["RV-LOCATION-01"]
    location_heading = location_case["representativeLocations"][0].removeprefix("line: ")

    metadata_by_case = {
        "RV-LIVE-01": normalize_index_metadata(root_case["representativeRelativePaths"][0], ()),
        "RV-INDEX-01": normalize_index_metadata(
            derived_case["representativeRelativePaths"][0], ()
        ),
        "RV-LOCATION-01": normalize_index_metadata(
            root_case["representativeRelativePaths"][0], (location_heading,)
        ),
    }

    for case_id, metadata in metadata_by_case.items():
        assert metadata.scope_status == cases[case_id]["expected"]["scopeStatus"]
        assert metadata.is_resolved is False
        assert metadata.scope_key is None


def test_conflicting_path_and_heading_scope_fails_closed() -> None:
    metadata = normalize_index_metadata(
        "英语/七年级上册/第一单元/教材/语法.md",
        ("第二单元", "语法"),
    )

    assert metadata.scope_status == "recoverable"
    assert metadata.scope_key is None
    assert metadata.reason == "conflicting-unit-no"


@pytest.mark.parametrize("relative_path", ("", "../英语/七年级上册/第一单元/教材/a.md", "英语\\a.md"))
def test_index_metadata_rejects_non_normalized_vault_relative_paths(relative_path: str) -> None:
    with pytest.raises(ValueError, match="normalized vault-relative"):
        normalize_index_metadata(relative_path, ())
