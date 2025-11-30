#!/bin/sh

set -e 

python baro-data.py --index files --output /publish/web/assets/bundles/

env VITE_BUILD_DATE=$(date -Is) \
    VITE_BUILD_HASH=$(cat .git-commit-hash) \
    npm x -- vite build

env $(cat /run/secrets/cloudflare) \
    npm x -- wrangler pages deploy --project-name ${PROJECT_NAME} ./dist
