ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install Python and minimal deps
RUN apk add --no-cache \
    python3 \
    py3-pip \
    chromium \
    chromium-chromedriver \
    nss \
    freetype \
    harfbuzz \
    ttf-freefont \
    && rm -rf /var/cache/apk/*

# Set Playwright to use system Chromium
ENV PLAYWRIGHT_BROWSERS_PATH=/usr
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV CHROMIUM_PATH=/usr/bin/chromium-browser

# Install Python packages
COPY requirements.txt /tmp/
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Copy application
COPY rootfs /

# Make scripts executable
RUN chmod +x /opt/spotify_yt_sync/*.py 2>/dev/null || true \
    && chmod +x /etc/services.d/sync/run

WORKDIR /opt/spotify_yt_sync
