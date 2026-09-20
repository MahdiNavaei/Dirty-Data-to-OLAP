# OSS reuse, licenses, and attribution

The repository's reuse policy is adapter-first: external runtimes stay behind
project-owned contracts, native vendor objects do not become application
artifacts, and research clones are not runtime dependencies. The detailed
ledger is [`docs/oss/REUSE_RESEARCH_LEDGER.md`](../oss/REUSE_RESEARCH_LEDGER.md).

## Current dependency attribution

The ledger records the reviewed licenses and obligations for the actual
dependency boundary, including dlt (Apache-2.0), DataProfiler (Apache-2.0),
Valentine (Apache-2.0), scikit-learn (BSD-3-Clause), SQLAlchemy (MIT), PyArrow
(Apache-2.0), openpyxl (MIT), and Python's CSV module (PSF-2.0). Installed
versions and optional extras are declared in `pyproject.toml` and the lockfile.

Desbordante is an AGPL-3.0-only research/provider boundary and is not shipped
as a normal host dependency. Its provider obligations must be reassessed
before any distribution that links or ships it. Splink and other optional
providers remain subject to their own upstream license and packaging terms.

## Project license status

No top-level `LICENSE` or `NOTICE` file is currently tracked. This is an
explicit unresolved legal/distribution decision, not permission to assume a
license or fabricate attribution text. No project license was created or
changed by Step41. The current workflow performs no public package/release
publication; obtain the project-owner decision before distributing the
repository or bundled runtime.
