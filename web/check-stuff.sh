#!/usr/bin/fish
cd $(dirname "$0")
npm x -- tsc --noEmit --strict --target esnext \
  (cat src/Data.ts src/_meme.ts public/stuff.json | psub -s .ts)
