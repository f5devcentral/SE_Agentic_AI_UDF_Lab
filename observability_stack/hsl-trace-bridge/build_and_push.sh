#!/bin/bash
#

TARGET_VERSION="v0.2"

docker build --no-cache -t 10.1.1.7:30500/demo-travel/hsl-trace-bridge:$TARGET_VERSION .
docker push 10.1.1.7:30500/demo-travel/hsl-trace-bridge:$TARGET_VERSION

