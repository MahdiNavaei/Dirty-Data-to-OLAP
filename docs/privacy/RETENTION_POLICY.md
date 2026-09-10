# Retention Policy

`RetentionPolicy` distinguishes restricted staging, derived sensitive
artifacts, linkable record metadata, debug bundles and exports. Restricted
staging is retained only for the run or until explicit cleanup; debug bundles
are ephemeral; exports contain no raw sensitive values.

The Step10 cleanup helper can delete only an explicitly selected path beneath
the privacy-owned ephemeral root and rejects path escape or root deletion. It
cannot delete source artifacts, operational data or a broad workspace. Full
artifact lifecycle/storage ownership remains with later platform work.
# Step11 cleanup hardening

Cleanup requires a project-authorized privacy-owned directory beneath the
configured root and rejects source, staging, code and external paths. The
privacy service cannot delete source artifacts.
