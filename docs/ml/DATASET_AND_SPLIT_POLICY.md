# Dataset and Split Policy

Training labels are explicit benchmark labels and are stored separately from
feature construction. Each row carries a base-scenario group. All rows from
one group stay in one split. Reverse-direction logical pairs share that group,
and duplicate logical pairs crossing groups are rejected.

The Step15 split is deterministic and records its seed and fingerprint. It
does not fall back to a random row split when groups are insufficient. Small
datasets remain explicitly limited or fail training when the train partition
does not contain both labels. Operational labels are not inferred from model
output and are never mutated by active learning.
