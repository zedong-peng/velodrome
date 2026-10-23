FROM ubuntu:24.04

# What the harness installers need, baked in so every trial starts from the same
# image instead of running apt for each agent. Nothing here helps with drawing.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git python3 xz-utils \
 && rm -rf /var/lib/apt/lists/*

ARG NODE_VERSION=24.22.1
RUN curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
  | tar -xJ -C /usr/local --strip-components=1 \
 && ln -s /usr/local/bin/node /usr/local/bin/nodejs \
 && node --version && npm --version

WORKDIR /app
