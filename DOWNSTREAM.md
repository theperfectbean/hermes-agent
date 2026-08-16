# Downstream Maintenance & Provenance Guide

This repository is the downstream maintenance fork of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) for homelab operations.

---

## 1. Branch and Repository Architecture

```text
upstream/main (NousResearch/hermes-agent)
    │
    ▼ (clean 1:1 mirror)
origin/main (theperfectbean/hermes-agent)
    │
    ▼ (branched from upstream tag, e.g. v2026.8.13)
release/vX.Y.Z-ct132 (integration branch for generic hardening)
    │
    ▼ (annotated release tag)
tag: vX.Y.Z-ct132.N
```

### Core Invariants
1. **Clean `main` Mirror**: The `main` branch of this fork directly tracks `upstream/main`. No local experimental or hardening commits are merged into `main`.
2. **Dedicated Release Branches**: Downstream hardening is developed and maintained on `release/vX.Y.Z-ct132` branches, rooted at stable upstream release tags.
3. **Clean Policy Separation**: Homelab-specific policy plugins (e.g. `ct132-safety-overlay`) and operational topologies are intentionally **excluded** from this public repository. They are maintained in a private repository and injected at release build/staging time.
4. **Manual, Approval-Gated Promotion**: Production services never self-update. Deployment and atomic symlink promotion on production hosts are external, manual, reviewed, and approval-gated.

---

## 2. Generic Hardening Scope

The patches on downstream release branches provide generic operational reliability improvements:
- **Evidence-First Turn Finalization**: Structured observation requirements before completing material tasks.
- **Forward Telemetry Attribution**: Clean capture of model, billing provider, and operational status metadata without polluting prompt replay.
- **Subprocess & Search Hardening**: Process registry containment and isolated multi-profile SQLite/FTS search indexing.
- **Automated CI Validation**: 87 targeted unit and integration tests enforcing containment and metadata invariants.

---

## 3. Upstream Update Workflow

To update this fork against a new upstream release:

```bash
# 1. Fetch latest upstream tags and branches
./scripts/upstream_update_helper.py fetch

# 2. Create a new release integration branch from the upstream release tag
./scripts/upstream_update_helper.py new-branch <upstream-tag>

# 3. Reapply generic hardening patches onto release/vX.Y.Z-ct132
# (Detect and drop any local patches that upstream has absorbed)

# 4. Run local CI checks and test suite
./scripts/run_ci_checks.sh

# 5. Generate structured review bundle for evaluation
./scripts/upstream_update_helper.py review-bundle <upstream-tag> review-bundle.md

# 6. Tag the downstream release
./scripts/upstream_update_helper.py tag vX.Y.Z-ct132.1

# 7. Stage release artifact for deployment
./scripts/upstream_update_helper.py build-stage vX.Y.Z-ct132.1 /opt/hermes/releases/vX.Y.Z-ct132.1
```

---

## 4. Releases and Provenance

- **Production Commit**: `70f03f13f` on `release/v0.20.1-ct132`
- **Release Tag**: `v0.20.1-ct132.1`
- **Base Upstream Tag**: `v2026.8.13` (Hermes Agent 0.20.1)
