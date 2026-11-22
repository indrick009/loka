FROM node:22-bookworm-slim

# whatsapp-web.js drives a real WhatsApp Web in Chromium. The distribution's
# Chromium is used instead of the one puppeteer would download, so the image
# carries one browser, updated with the OS packages.
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium fonts-liberation ca-certificates wget \
    && rm -rf /var/lib/apt/lists/*

ENV PUPPETEER_SKIP_DOWNLOAD=1 \
    CHROMIUM_PATH=/usr/bin/chromium \
    NODE_ENV=production

WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev
COPY src ./src

EXPOSE 3000
# Chromium and WhatsApp Web take a while to come up on first start.
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=5 \
  CMD wget -qO- http://127.0.0.1:3000/health || exit 1

CMD ["node", "src/index.js"]
