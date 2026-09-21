#!/bin/sh
# Installs the secret scan as the git pre-commit hook.
cd "$(git rev-parse --show-toplevel)" && cp scripts/security/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit && echo "pre-commit secret scan installed"
