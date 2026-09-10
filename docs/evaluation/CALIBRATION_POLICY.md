# Calibration Policy

Step18 does not turn fusion, ER, matcher, or learned ranking scores into business probabilities. The report retains `UNCALIBRATED_DECISION_SCORE` semantics and the assessment keeps automation `NOT_AUTHORIZED`.

The optional Step15 grouped calibration experiment executed with the local scikit-learn adapter and retained a label-shuffle control. Its small synthetic sample is not sufficient to justify a production calibration curve or threshold. The global Step18 calibration status remains `INSUFFICIENT_CALIBRATION_DATA`.
