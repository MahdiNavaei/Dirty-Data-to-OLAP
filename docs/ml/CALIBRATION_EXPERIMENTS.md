# Calibration Experiments

Calibration is a separate grouped experiment. It is not applied to the
ranking score and does not change the Step15 contract. With insufficient
positive/negative validation evidence the result is
INSUFFICIENT_CALIBRATION_DATA; the service does not manufacture calibrated
probabilities.

Any future calibration report must retain the grouped split, calibration
method, row count, Brier/ECE definitions, limitations, and the distinction
between an experimental score and a business decision.
