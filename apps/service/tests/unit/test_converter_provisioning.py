from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import api.main as api_main
from api.runtime import RuntimeState
from domain.evidence import ArtifactRef, DocumentGraph, PdfRegionLocator
from workers.converters.launcher import _docling_blocks, _mineru_blocks
from workers.converters.profiles import require_profile
from workers.converters.provisioning import (
    ProvisionedProfiles,
    default_converter_root,
    load_provisioned_profiles,
)
from workers.converters.quality_gate import StructuralQualityGate


def test_loader_verifies_only_a_controlled_manifest_and_keeps_approval_gates_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ObsidianPlatform" / "converters"
    executable = root / "pandoc-3.10" / "Pandoc" / "pandoc.exe"
    config = root / "profiles" / "pandoc.json"
    model = root / "models" / "pandoc-assets.json"
    executable.parent.mkdir(parents=True)
    config.parent.mkdir(parents=True)
    model.parent.mkdir(parents=True)
    executable.write_bytes(b"fixed pandoc executable")
    config.write_text('{"offline":true}', encoding="utf-8")
    model.write_bytes(b"fixed supporting asset")
    _write_manifest(
        root,
        {
            "profile_id": "pandoc-3.10-local",
            "engine": "pandoc",
            "engine_version": "3.10",
            "executable": "pandoc-3.10/Pandoc/pandoc.exe",
            "executable_sha256": _digest(executable),
            "config": "profiles/pandoc.json",
            "config_sha256": _digest(config),
            "models": [{"path": "models/pandoc-assets.json", "sha256": _digest(model)}],
            "resource_limits": {"wall_clock_seconds": 60, "memory_mb": 4096},
            # These untrusted manifest claims must not enable a local engine.
            "release_approved": True,
            "network_denied": True,
        },
    )

    loaded = load_provisioned_profiles(root)
    profile = loaded.profile_for("pandoc")

    assert profile is not None
    assert Path(profile.executable_path or "").resolve() == executable.resolve()
    assert profile.model_hashes == (_digest(model),)
    assert profile.release_approved is False
    assert profile.network_denied is False
    assert profile.isolation_boundary == "local-process"
    assert require_profile(profile, "pandoc").reason_code == "release-approval-missing"


def test_loader_rejects_path_escape_and_changed_hashes(tmp_path: Path) -> None:
    root = tmp_path / "converters"
    root.mkdir()
    outside = tmp_path / "outside.exe"
    outside.write_bytes(b"outside")
    _write_manifest(
        root,
        {
            "profile_id": "escaped",
            "engine": "pandoc",
            "engine_version": "3.10",
            "executable": "../outside.exe",
            "executable_sha256": _digest(outside),
            "config": "missing.json",
            "config_sha256": "0" * 64,
            "models": [],
            "resource_limits": {"wall_clock_seconds": 60},
        },
    )

    escaped = load_provisioned_profiles(root)

    assert escaped.profile_for("pandoc") is None
    assert escaped.unavailable_reasons["pandoc"] == "profile-integrity-invalid"

    executable = root / "pandoc.exe"
    config = root / "pandoc.json"
    executable.write_bytes(b"old binary")
    config.write_bytes(b"fixed config")
    _write_manifest(
        root,
        {
            "profile_id": "changed",
            "engine": "pandoc",
            "engine_version": "3.10",
            "executable": "pandoc.exe",
            "executable_sha256": "0" * 64,
            "config": "pandoc.json",
            "config_sha256": _digest(config),
            "models": [],
            "resource_limits": {"wall_clock_seconds": 60},
        },
    )

    changed = load_provisioned_profiles(root)

    assert changed.profile_for("pandoc") is None
    assert changed.unavailable_reasons["pandoc"] == "profile-integrity-invalid"


def test_default_root_uses_only_localappdata_converter_location(monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", r"C:\\Users\\example\\AppData\\Local")
    monkeypatch.setenv("PATH", r"C:\\somewhere\\with\\pandoc")

    assert default_converter_root() == Path(r"C:\\Users\\example\\AppData\\Local") / "ObsidianPlatform" / "converters"


def test_composition_root_injects_one_private_store_and_unavailable_profile_map(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "converters"
    monkeypatch.setattr(
        api_main,
        "load_provisioned_profiles",
        lambda: ProvisionedProfiles(
            root,
            {},
            {"mineru": "profile-missing", "pandoc": "profile-missing", "docling": "profile-missing"},
        ),
    )
    runtime = RuntimeState(data_directory=tmp_path / "app-data", sqlite_version="3.45.1")

    app = api_main.create_app(runtime=runtime)
    service = app.state.import_task_service
    worker = service.worker

    assert service.converter_profile == {}
    assert service.artifact_store is worker._artifact_store
    assert service.artifact_store.root == runtime.data_directory / "conversion-artifacts"


def test_loader_requires_a_hash_bound_local_approval_record(tmp_path: Path) -> None:
    root = tmp_path / "converters"
    executable = root / "pandoc.exe"
    config = root / "pandoc.json"
    root.mkdir()
    executable.write_bytes(b"pandoc")
    config.write_bytes(b"offline")
    profile = {
        "profile_id": "pandoc-local",
        "engine": "pandoc",
        "engine_version": "3.10",
        "executable": "pandoc.exe",
        "executable_sha256": _digest(executable),
        "config": "pandoc.json",
        "config_sha256": _digest(config),
        "models": [],
        "resource_limits": {"wall_clock_seconds": 60},
    }
    _write_manifest(root, profile)
    (root / "converter-release-approval.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "approved_profiles": [
                    {
                        "engine": "pandoc",
                        "profile_id": "pandoc-local",
                        "executable_sha256": _digest(executable),
                        "config_hash": _digest(config),
                        "model_hashes": [],
                        "license_disposition": "local-use",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    profile = load_provisioned_profiles(root).profile_for("pandoc")

    assert profile is not None
    assert profile.release_approved is True
    assert require_profile(profile, "pandoc").allowed is True


def test_mineru_content_list_adapter_uses_real_regions_and_raw_json_evidence() -> None:
    raw = ArtifactRef(
        artifact_id="raw-mineru",
        attempt_id="attempt-1",
        sha256="a" * 64,
        media_type="application/json",
        role="converter-json",
        private_relative_path="pending/raw-mineru",
        producer_object_id="mineru/probe_content_list_v2.json",
    )
    blocks, issues = _mineru_blocks(
        [
            [
                {
                    "type": "title",
                    "content": {"level": 2, "title_content": [{"type": "text", "content": "Title"}]},
                    "bbox": [10, 20, 100, 40],
                },
                {
                    "type": "paragraph",
                    "content": {"paragraph_content": [{"type": "text", "content": "Body"}]},
                    "bbox": [10, 50, 100, 70],
                },
            ]
        ],
        "attempt-1",
        raw,
    )

    assert not issues
    assert [block.kind for block in blocks] == ["heading", "paragraph"]
    assert isinstance(blocks[0].locators[0], PdfRegionLocator)
    assert blocks[1].retrieval_projection == "Body"
    assert blocks[0].evidence_refs[0].artifact_sha256 == raw.sha256


def test_mineru_adapter_preserves_formula_and_table_while_classifying_furniture_as_warning() -> None:
    raw = ArtifactRef(
        artifact_id="raw-mineru",
        attempt_id="attempt-1",
        sha256="a" * 64,
        media_type="application/json",
        role="converter-json",
        private_relative_path="pending/raw-mineru",
        producer_object_id="mineru/probe_content_list_v2.json",
    )
    blocks, issues = _mineru_blocks(
        [
            [
                {
                    "type": "equation_interline",
                    "content": {
                        "math_type": "latex",
                        "math_content": "x^2 - 1 = 0",
                    },
                    "bbox": [10, 20, 100, 40],
                },
                {
                    "type": "table",
                    "content": {
                        "html": (
                            "<table><tr><td rowspan=\"2\">Term</td><td>Meaning</td></tr>"
                            "<tr><td>Value</td></tr></table>"
                        )
                    },
                    "bbox": [10, 50, 100, 90],
                },
                {
                    "type": "page_header",
                    "content": {"page_header_content": [{"type": "text", "content": "Unit"}]},
                    "bbox": [10, 100, 100, 120],
                },
            ]
        ],
        "attempt-1",
        raw,
    )

    assert [block.kind for block in blocks] == ["formula", "table"]
    assert blocks[0].payload.to_dict()["latex"] == "x^2 - 1 = 0"
    assert blocks[1].payload.to_dict()["rows"] == [["Term", "Meaning"], ["Value"]]
    assert blocks[1].payload.to_dict()["rowspan"] == [[2, 1], [1]]
    assert len(issues) == 1
    assert issues[0].severity == "warning"

    graph = DocumentGraph(
        graph_id="graph-1",
        source_sha256="a" * 64,
        input_snapshot_hash="a" * 64,
        selected_attempt_id="attempt-1",
        blocks=tuple(blocks),
        assets=(),
        issues=tuple(issues),
    )

    assert StructuralQualityGate().evaluate(graph, {"document_kind": "pdf", "page_count": 1}).action == "accepted"


def test_mineru_empty_paragraph_becomes_a_located_review_issue() -> None:
    raw = ArtifactRef(
        artifact_id="raw-mineru",
        attempt_id="attempt-1",
        sha256="a" * 64,
        media_type="application/json",
        role="converter-json",
        private_relative_path="pending/raw-mineru",
        producer_object_id="mineru/probe_content_list_v2.json",
    )

    blocks, issues = _mineru_blocks(
        [[{"type": "paragraph", "content": {"paragraph_content": []}, "bbox": [10, 20, 100, 40]}]],
        "attempt-1",
        raw,
    )

    assert not blocks
    assert [issue.code for issue in issues] == ["mineru-empty-text"]
    assert isinstance(issues[0].locator, PdfRegionLocator)


def test_docling_empty_formula_becomes_a_located_review_issue_instead_of_raising() -> None:
    raw = ArtifactRef(
        artifact_id="raw-docling",
        attempt_id="attempt-1",
        sha256="a" * 64,
        media_type="application/json",
        role="converter-json",
        private_relative_path="pending/raw-docling",
        producer_object_id="docling/result.json",
    )
    blocks, issues = _docling_blocks(
        {
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "label": "formula",
                    "orig": "x^2 - 1 = 0",
                    "text": "",
                    "prov": [
                        {
                            "page_no": 1,
                            "bbox": {"l": 10, "b": 20, "r": 100, "t": 40},
                        }
                    ],
                }
            ]
        },
        "pdf",
        "attempt-1",
        raw,
    )

    assert not blocks
    assert [issue.code for issue in issues] == ["docling-formula-unresolved"]
    assert isinstance(issues[0].locator, PdfRegionLocator)


def _write_manifest(root: Path, profile: dict[str, object]) -> None:
    (root / "converter-profiles.json").write_text(
        json.dumps({"schema_version": 1, "profiles": [profile]}), encoding="utf-8"
    )


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()
