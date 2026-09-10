"""Leakage-safe, scenario-group evaluation splitting."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from typing import Iterable, Mapping

from .contracts import InferenceEvaluationExample, SplitManifest


def _bucket(group_id: str, seed: int) -> int:
    return int(hashlib.sha256(f"{seed}:{group_id}".encode()).hexdigest()[:8], 16) % 100


def assign_groups(group_ids: Iterable[str], *, seed: int, explicit_roles: Mapping[str, str] | None = None) -> dict[str, str]:
    roles = {"DEVELOPMENT": (), "CALIBRATION": (), "TEST": ()}
    output: dict[str, str] = {}
    for group_id in sorted(set(group_ids)):
        if explicit_roles and group_id in explicit_roles:
            role = explicit_roles[group_id]
        else:
            bucket = _bucket(group_id, seed)
            role = "DEVELOPMENT" if bucket < 60 else "CALIBRATION" if bucket < 80 else "TEST"
        if role not in roles:
            raise ValueError(f"unknown split role: {role}")
        output[group_id] = role
    return output


def build_split_manifest(*, examples: Iterable[InferenceEvaluationExample], truth_fingerprint: str, seed: int, explicit_roles: Mapping[str, str] | None = None, reverse_pair_groups: Mapping[str, str] | None = None) -> SplitManifest:
    rows = tuple(examples)
    group_roles = assign_groups((row.scenario_group_id for row in rows), seed=seed, explicit_roles=explicit_roles)
    group_ids_by_split: dict[str, list[str]] = defaultdict(list)
    example_ids_by_split: dict[str, list[str]] = defaultdict(list)
    class_counts: dict[str, Counter[str]] = defaultdict(Counter)
    slice_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for group_id, role in sorted(group_roles.items()):
        group_ids_by_split[role].append(group_id)
    for row in rows:
        role = group_roles[row.scenario_group_id]
        example_ids_by_split[role].append(row.example_id)
        if row.label is not None:
            class_counts[role][str(row.label)] += 1
        for tag in row.slice_tags:
            slice_counts[role][tag] += 1
    group_sets = [set(values) for values in group_ids_by_split.values()]
    leakage = {"group_intersections": {}, "reverse_pair_leakage": True}
    for left_index, left in enumerate(sorted(group_ids_by_split)):
        for right in sorted(group_ids_by_split)[left_index + 1:]:
            intersection = sorted(set(group_ids_by_split[left]).intersection(group_ids_by_split[right]))
            leakage["group_intersections"][f"{left}:{right}"] = intersection
    reverse_pair_groups = reverse_pair_groups or {}
    reverse_split = {pair: group_roles.get(group) for pair, group in reverse_pair_groups.items()}
    leakage["reverse_pair_split"] = reverse_split
    leakage["reverse_pair_leakage"] = len({value for value in reverse_split.values() if value}) <= 1 if reverse_split else True
    return SplitManifest(
        split_id=f"step18-split-{seed}",
        algorithm="sha256_group_bucket_or_frozen_roles_v1",
        seed=seed,
        group_ids_by_split={key: tuple(sorted(value)) for key, value in sorted(group_ids_by_split.items())},
        example_ids_by_split={key: tuple(sorted(value)) for key, value in sorted(example_ids_by_split.items())},
        class_counts_by_split={key: dict(sorted(value.items())) for key, value in sorted(class_counts.items())},
        slice_counts_by_split={key: dict(sorted(value.items())) for key, value in sorted(slice_counts.items())},
        truth_fingerprint=truth_fingerprint,
        leakage_audit=leakage,
    )
