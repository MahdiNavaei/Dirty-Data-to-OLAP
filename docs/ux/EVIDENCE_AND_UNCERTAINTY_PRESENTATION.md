# Evidence and Uncertainty Presentation

## Evidence hierarchy

The experience distinguishes, in descending order of directness:

1. direct observation with an explicit scope;
2. declared metadata or constraint;
3. domain/human assertion;
4. derived interpretation or normalized signal;
5. hypothesis container or model-assisted suggestion;
6. human review decision recorded against the exact subject.

This is a hierarchy of meaning and traceability, not a universal truth ranking. A human decision is an authorization record, not a replacement for missing data or a model confidence value.

## Score and probability boundary

Raw metric values, normalized values, contributions, confidence bands, and decision scores are shown with their metric name, semantics, normalization method/version, and policy. The V1 fusion contract is uncalibrated. No copy, badge, sort label, or tooltip may call an uncalibrated score a probability, likelihood, precision, or posterior. Probability language is allowed only when a future calibrated contract explicitly provides calibration evidence, version, population, and scope.

## Coverage and provider state

`FULL`, `BOUNDED`, `SAMPLED`, `NULL_REDUCED`, `TEMPORALLY_UNALIGNED`, `INCOMPLETE`, and `UNKNOWN` reliability are displayed as labels with explanations. A sample is not a full scan. `UNAVAILABLE`, `FAILED`, `INCOMPLETE`, `SKIPPED`, `NOT_CONFIGURED`, and `PRIVACY_BLOCKED` producer states remain distinct. The UI never fills a missing value with zero or treats absence of a provider as negative evidence.

## Conflicts and missing evidence

Contradicting evidence is above the fold whenever it affects a decision. Each conflict shows type, severity, explanation, supporting/contradicting references, policy rule, scope and provenance. Missing evidence shows what was required, why it is missing, and whether retry, configuration, privacy authorization, or human review can resolve it.

## Confidence bands

`HIGH`, `MEDIUM`, `LOW`, `CONFLICTED`, and `INSUFFICIENT` are operational bands from the typed contract. They are not calibrated probabilities. `CONFLICTED` and `INSUFFICIENT` make approval consequences more restrictive; they do not disappear through sorting or color styling.

## Validation language

Validation uses the typed statuses `PASS`, `FAIL`, `REVIEW_REQUIRED`, `NOT_APPLICABLE`, and `NOT_EVALUATED`, with scope and severity. A top-level report status is accompanied by required check rows, discrepancies, evidence references, and binding identities. A failed or not-evaluated required check cannot be manually promoted to success.
