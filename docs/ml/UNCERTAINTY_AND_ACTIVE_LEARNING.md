# Uncertainty and Active Learning

Step15 reports deterministic rank stability observations and their method.
Cross-fold variance, missing critical evidence, hard conflicts, low margins,
and disagreement with the baseline are uncertainty signals, not hidden
probabilities.

Active-learning output is a bounded list of label-query suggestions. It is
non-mutating: it cannot write labels, change candidate state, publish a
relationship, or send project data to an external provider.
