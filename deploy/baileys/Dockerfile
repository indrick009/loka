FROM node:22-alpine

WORKDIR /app

COPY package.json ./

# Baileys pins libsignal to a git URL, so npm shells out to git. The tool is
# only needed to resolve that one dependency, so it is installed for the
# install and taken straight back out.
RUN apk add --no-cache --virtual .build-deps git \
    && npm install --omit=dev \
    && apk del .build-deps

COPY src ./src

ENV NODE_ENV=production
EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD wget -qO- http://127.0.0.1:3000/health || exit 1

CMD ["node", "src/index.js"]
