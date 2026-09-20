# Security boundary

The current security posture is bounded and evidence-led. Source access is
read-only, bounded, and provider-policy dependent; the API enforces project
and run scope; generic artifact payloads and unsafe browser disclosures are
restricted; and the Step39 red-team suite covers the documented local threat
model.

Read the [source database security matrix](SOURCE_DATABASE_SECURITY_MATRIX.md),
[Step33 threat model](STEP33_APPLICATION_SECURITY_THREAT_MODEL.md), and
[Step39 findings](redteam/STEP39_FINDINGS.md).

The local `local_test` and `trusted_proxy` modes are test/integration
boundaries, not production authentication. G10/G13 PASS does not claim
universal security, deployment IAM, live provider pentesting, host compromise
resistance, or secret exfiltration testing against a deployed system.
