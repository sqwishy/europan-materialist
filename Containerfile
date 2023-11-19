# Building this image's first stage (datagen) expects that the Barotrauma's
# Content directory is bind mounted in at /Content, for example:
#
#     podman build -v /games/Barotrauma/Content:/Content:ro
#
# Building the webbuild stage will write static files to /build/web/dist
#
# Building the pagesupload stage

FROM alpine:latest AS datagen

RUN apk update
RUN apk add python3 nodejs npm py3-lxml py3-pillow

# build sprite sheet and item data

WORKDIR /build

ADD baro-data.py .
RUN mkdir -p web/assets
RUN python3 baro-data.py \
            --items /Content/Items \
                    /Content/Map \
            --texts /Content/Texts \
            --sprites /Content \
            -- web/assets/sprites.css \
            > web/assets/stuff.json


# install web stuff

FROM datagen AS webinstall

WORKDIR /build/web

ADD web/package.json \
    web/package-lock.json \
    .
RUN npm install

ADD web/index.html \
    web/index.html \
    web/tsconfig.json \
    web/vite.config.js .
ADD web/src src
ADD web/public public
ADD web/assets assets

CMD npm x -- vite

# to upload to cloudflare pages
#
# requires environment variables
#   CLOUDFLARE_ACCOUNT_ID
#   CLOUDFLARE_API_TOKEN
#
# put them in a file and bind mount into the container when needed
# (example with secrets in password store using fish shell's psub)
#
# podman run --rm \
#            -v (pass s/cloudflare/apikeys | psub):/run/secrets/cloudflare \
#            however-you-tagged-the-build ash \
#            -c    'npm x -- vite build \
#                && npm install wrangler  \
#                && env $(cat /run/secrets/cloudflare) \
#                   npm x -- wrangler pages deploy --project-name materialist-next ./dist'
