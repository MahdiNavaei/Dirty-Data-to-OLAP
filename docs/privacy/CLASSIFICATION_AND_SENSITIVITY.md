# Classification and Sensitivity

`PrivacyClassification` separates state from handling level:

- `CONFIRMED_SENSITIVE`: an explicit project-owned rule or assertion identifies
  the field category.
- `POTENTIALLY_SENSITIVE`: a deterministic pattern or conservative artifact
  rule indicates possible sensitivity without semantic certainty.
- `EXPLICITLY_NON_SENSITIVE`: reserved for an explicit future policy rule; the
  detector does not infer it.
- `UNKNOWN`: no match or insufficient evidence. It is not public.

V1 categories include email, phone, person name, postal address, government,
account and financial identifiers, date of birth, precise location,
authentication secret, potentially sensitive free text and linkable record
reference. High-risk identifiers have no invented parsing semantics and are
not persisted as raw values by this layer.

Evidence is a safe reference containing category/policy reasoning, subject
reference and detector version. It never contains the inspected raw value.
`RESTRICTED` is used for source-faithful raw staging; profiles, quality results
and record references are conservatively sensitive or linkable metadata.
