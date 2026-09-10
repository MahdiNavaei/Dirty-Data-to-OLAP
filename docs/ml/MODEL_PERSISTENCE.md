# Model Persistence

The only Step15 model artifact format is a canonical JSON document containing
the feature schema identity/order, coefficients, intercept, class labels,
scikit-learn version, model configuration hash, dataset fingerprint, split
fingerprint, and experimental status.

Pickle, joblib, executable serializers, native estimator objects, raw staged
values, and labels are forbidden in model artifacts. Publication is atomic
and the artifact content hash is retained in model evidence. Reload tooling
must reconstruct the linear score from these fields rather than deserialize
code.
