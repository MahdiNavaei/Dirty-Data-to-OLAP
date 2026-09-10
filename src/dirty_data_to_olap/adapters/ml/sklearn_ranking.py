"""Optional, JSON-persisted sklearn ranking adapter.

This module is deliberately lazy about importing sklearn. The domain and
baseline paths remain usable when the optional ml extra is not installed.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import tempfile
from pathlib import Path
from typing import Any

from dirty_data_to_olap.domain.contracts.applied_ml import (
    MLDatasetRow,
    MLDatasetManifest,
    MLFeatureContribution,
    MLFeatureSchema,
    MLFeatureVector,
    MLModelEvidence,
    MLModelSpecification,
    MLModelStatus,
    ml_linear_score,
    ml_model_config_hash,
)


class OptionalMLUnavailable(RuntimeError):
    """Raised when the optional sklearn dependency is not installed."""


def _sklearn_runtime() -> tuple[Any, str]:
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn import __version__
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise OptionalMLUnavailable(
            "scikit-learn is unavailable; install the project ml extra"
        ) from exc
    return LogisticRegression, str(__version__)


def _artifact_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SklearnRelationshipRanker:
    """Transparent logistic ranking score over the fixed project feature schema."""

    def __init__(
        self,
        feature_schema: MLFeatureSchema,
        *,
        random_seed: int = 20260910,
        max_iter: int = 1000,
        c: float = 1.0,
    ) -> None:
        self.feature_schema = feature_schema
        self.random_seed = random_seed
        self.hyperparameters = {
            "solver": "liblinear",
            "class_weight": "balanced",
            "max_iter": max_iter,
            "C": c,
        }
        self._coefficients: tuple[float, ...] | None = None
        self._intercept: float | None = None
        self._evidence: MLModelEvidence | None = None

    @staticmethod
    def capability() -> tuple[bool, str, str]:
        try:
            _, version = _sklearn_runtime()
        except OptionalMLUnavailable as exc:
            return False, "unavailable", str(exc)
        return True, "scikit-learn", version

    def _matrix(self, vectors: tuple[MLFeatureVector, ...]) -> Any:
        import numpy as np

        expected = set(self.feature_schema.feature_order)
        matrix: list[list[float]] = []
        for vector in vectors:
            if set(vector.values) != expected:
                raise ValueError(
                    f"feature schema mismatch for {vector.candidate_id}: "
                    f"expected {sorted(expected)}, got {sorted(vector.values)}"
                )
            row = [float(vector.values[feature_id]) for feature_id in self.feature_schema.feature_order]
            if not np.isfinite(row).all():
                raise ValueError(f"non-finite feature vector: {vector.candidate_id}")
            matrix.append(row)
        return np.asarray(matrix, dtype=float)

    def train(
        self,
        rows: tuple[MLDatasetRow, ...],
        *,
        split_manifest: Any,
        dataset_manifest: MLDatasetManifest,
        model_id: str,
        artifact_path: str | Path | None = None,
    ) -> MLModelEvidence:
        LogisticRegression, sklearn_version = _sklearn_runtime()
        train_rows = tuple(
            row for row in rows if split_manifest.assignments.get(row.row_id) == "train"
        )
        if not train_rows:
            raise ValueError("grouped split produced no training rows")
        labels = tuple(row.label.label for row in train_rows)
        if set(labels) != {0, 1}:
            raise ValueError("training requires both positive and negative labels")
        model = LogisticRegression(
            random_state=self.random_seed,
            **self.hyperparameters,
        )
        matrix = self._matrix(tuple(row.feature_vector for row in train_rows))
        model.fit(matrix, labels)
        coefficients = tuple(float(value) for value in model.coef_[0])
        intercept = float(model.intercept_[0])
        config_hash = ml_model_config_hash(
            task=self.feature_schema.task,
            feature_schema=self.feature_schema,
            estimator_family="sklearn.linear_model.LogisticRegression",
            hyperparameters=self.hyperparameters,
            random_seed=self.random_seed,
            sklearn_version=sklearn_version,
            missing_value_policy="project_contract_missing_as_zero_with_explicit_missing_feature_ids",
        )
        specification = MLModelSpecification(
            model_id=model_id,
            task=self.feature_schema.task,
            hyperparameters=self.hyperparameters,
            random_seed=self.random_seed,
            feature_schema_id=self.feature_schema.schema_id,
            model_config_hash=config_hash,
            dataset_fingerprint=dataset_manifest.dataset_fingerprint,
            split_fingerprint=split_manifest.split_fingerprint,
            sklearn_version=sklearn_version,
            status=MLModelStatus.EXPERIMENTAL,
        )
        evidence = MLModelEvidence(
            specification=specification,
            class_labels=tuple(int(value) for value in model.classes_),
            coefficients=coefficients,
            intercept=intercept,
            training_rows=len(train_rows),
            training_groups=len({row.label.group_id for row in train_rows}),
            class_counts={"0": labels.count(0), "1": labels.count(1)},
            limitations=(
                "ranking score is uncalibrated and is not an acceptance probability",
                "model is experimental and benchmark-scoped",
                "grouped holdout metrics must be evaluated separately",
            ),
        )
        self._coefficients = coefficients
        self._intercept = intercept
        self._evidence = evidence
        if artifact_path is not None:
            self._write_json_artifact(Path(artifact_path), evidence)
        return self._evidence

    def _require_fitted(self) -> tuple[tuple[float, ...], float, MLModelEvidence]:
        if self._coefficients is None or self._intercept is None or self._evidence is None:
            raise RuntimeError("ranker has not been trained")
        return self._coefficients, self._intercept, self._evidence

    def score(self, vector: MLFeatureVector) -> float:
        coefficients, intercept, _ = self._require_fitted()
        self._matrix((vector,))
        return ml_linear_score(
            coefficients,
            intercept,
            self.feature_schema.feature_order,
            vector.values,
        )

    def contributions(
        self, vector: MLFeatureVector
    ) -> tuple[MLFeatureContribution, ...]:
        coefficients, _, evidence = self._require_fitted()
        self._matrix((vector,))
        output: list[MLFeatureContribution] = []
        for feature_id, coefficient in zip(
            self.feature_schema.feature_order, coefficients
        ):
            value = float(vector.values[feature_id])
            contribution = float(value * coefficient)
            output.append(
                MLFeatureContribution(
                    candidate_id=vector.candidate_id,
                    feature_id=feature_id,
                    feature_value=value,
                    coefficient=float(coefficient),
                    contribution=contribution,
                    direction=(
                        "positive"
                        if contribution > 0
                        else "negative"
                        if contribution < 0
                        else "neutral"
                    ),
                    missing=feature_id in vector.missing_feature_ids,
                )
            )
        if abs(
            ml_linear_score(
                coefficients, evidence.intercept, self.feature_schema.feature_order, vector.values
            )
            - (evidence.intercept + sum(item.contribution for item in output))
        ) > 1e-9:
            raise RuntimeError("feature contributions do not reconstruct ranking score")
        return tuple(output)

    def _write_json_artifact(
        self, path: Path, evidence: MLModelEvidence
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _, _, version = self.capability()
        payload = {
            "artifact_version": "step15-linear-json-v1",
            "model_id": evidence.specification.model_id,
            "task": evidence.specification.task.value,
            "feature_schema_id": self.feature_schema.schema_id,
            "feature_schema_version": self.feature_schema.version,
            "feature_order": list(self.feature_schema.feature_order),
            "coefficients": list(evidence.coefficients),
            "intercept": evidence.intercept,
            "class_labels": list(evidence.class_labels),
            "sklearn_version": version,
            "python_version": platform.python_version(),
            "model_config_hash": evidence.specification.model_config_hash,
            "dataset_fingerprint": evidence.specification.dataset_fingerprint,
            "split_fingerprint": evidence.specification.split_fingerprint,
            "status": evidence.specification.status.value,
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(path.parent),
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and Path(temporary).exists():
                Path(temporary).unlink()

        artifact_digest = _artifact_hash(serialized)
        self._evidence = self._evidence.model_copy(
            update={
                "artifact_location": str(path),
                "artifact_content_hash": artifact_digest,
            }
        )
