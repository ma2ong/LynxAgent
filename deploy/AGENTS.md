# Deployment Rules

- Keep provider-specific assets under `deploy/<provider>/`.
- Never commit credentials, private keys, `.env` files, database files, or generated release archives.
- Shell scripts must use `set -euo pipefail` and remain safe to run repeatedly.
- Production services must run as a dedicated non-root user.
- Persistent data must live outside release directories so deployments cannot overwrite it.
- Every deployment path must include a health check and an automatic rollback-safe failure.
