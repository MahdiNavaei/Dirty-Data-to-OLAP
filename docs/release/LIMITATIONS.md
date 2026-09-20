# V1 limitations

The following limitations are part of the release truth, not footnotes:

- The accepted browser journey is local/reference and uses managed CSV import.
- Oracle support is deferred.
- SQL adapter evidence is limited to the documented CI service versions and
  source boundary; it is not production deployment evidence.
- G5 scores are uncalibrated decision scores. No inference threshold is
  selected and automation is not authorized.
- Entity-resolution output requires the reviewed policy path; clusters are not
  automatic canonical truth.
- No revenue or GMV semantics are invented.
- The local API's `local_test` and `trusted_proxy` modes are integration
  boundaries, not production authentication or credential management.
- The local SQLite/filesystem runtime does not claim a production queue,
  exactly-once distributed delivery, HA, or multi-node failover.
- Step37/38 evidence is bounded local/reference performance. One-million,
  ten-million, and 100-million-row execution is not claimed; no production
  capacity or SLA is measured.
- The container image scan passed with `--exit-code 0 --ignore-unfixed`; that
  is not a zero-vulnerability claim.
- Red-team evidence is bounded to the documented local threat model; no
  universal security, deployment IAM, live network-provider pentest, or host
  compromise claim is made.
- The Step40 demo is a project-local control-plane smoke, not the full product
  workflow.
- G15 PASS means the documentation and bounded release evidence are coherent;
  it does not mean universal production readiness.
