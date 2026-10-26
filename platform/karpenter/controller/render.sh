#!/usr/bin/env bash
# values.yaml 로 karpenter.yaml 을 다시 만든다. karpenter.yaml 은 직접 수정하지 않는다.
set -euo pipefail
cd "$(dirname "$0")"

helm template karpenter oci://public.ecr.aws/karpenter/karpenter \
  --version 1.15.0 \
  --namespace kube-system \
  --kube-version 1.35.9 \
  --include-crds \
  -f values.yaml > karpenter.yaml
