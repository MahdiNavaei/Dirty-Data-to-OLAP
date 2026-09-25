from __future__ import annotations

from types import SimpleNamespace

from dirty_data_to_olap.application.multi_source_runtime import _artifact_source_key, _ordered_snapshot_ids
from dirty_data_to_olap.domain.contracts.dependency import DependencyRequest
from dirty_data_to_olap.domain.contracts.profiling import ProfileMode, ProfileRequest


def test_artifact_source_key_reads_nested_source_identity() -> None:
    profile = SimpleNamespace(
        profile_request=ProfileRequest(
            profile_request_id="profile-request",
            source_id="source-profile",
            snapshot_id="snapshot-profile",
            selected_table_ids=("table",),
            mode=ProfileMode.FULL,
        )
    )
    dependency = SimpleNamespace(
        request=DependencyRequest(
            request_id="dependency-request",
            source_id="source-dependency",
            snapshot_id="snapshot-dependency",
            selected_table_ids=("table",),
        )
    )

    assert _artifact_source_key(profile, "profile-artifact") == "source-profile"
    assert _artifact_source_key(dependency, "dependency-artifact") == "source-dependency"
    assert _artifact_source_key(SimpleNamespace(source_id="source-direct"), "direct-artifact") == "source-direct"
    assert _artifact_source_key(SimpleNamespace(request=SimpleNamespace(source_ids=("a", "b"))), "schema-artifact") == "schema-artifact"


def test_ordered_snapshot_ids_preserve_source_snapshot_binding() -> None:
    snapshots = {
        "source-b": SimpleNamespace(snapshot=SimpleNamespace(snapshot_id="snapshot-b")),
        "source-a": SimpleNamespace(snapshot=SimpleNamespace(snapshot_id="snapshot-a")),
    }

    assert _ordered_snapshot_ids(("source-b", "source-a"), snapshots) == ("snapshot-a", "snapshot-b")
