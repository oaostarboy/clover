#!/usr/bin/env bash
# Real native dependencies and entry; gateway is standalone, not service proof.
set -euo pipefail
[[ $GITHUB_ACTIONS == true && $RUNNER_ENVIRONMENT == github-hosted ]]
STAGE="$RUNNER_TEMP/trusted-stage"
RUNTIME="$RUNNER_TEMP/trusted-runtime"
"$PY" -m venv "$RUNTIME"
"$RUNTIME/bin/python" -m pip install --disable-pip-version-check -e "$SOURCE" pydantic
"$RUNTIME/bin/python" -c 'import pydantic; print("actual Pydantic", pydantic.__version__)'
git clone --no-hardlinks "$SOURCE" "$STAGE"
git -C "$STAGE" checkout --detach "$TARGET_REF"
DEPENDENCIES=$("$RUNTIME/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
"$RUNTIME/bin/python" -I -S "$STAGE/scripts/staged_update.py"   --target "$INSTALL_DIR" --old-commit "$FROM_SHA"   --stage-commit "$TARGET_REF" --stage-tree "$TARGET_TREE"   --home "$CLOVER_HOME" --dependency-path "$DEPENDENCIES"
