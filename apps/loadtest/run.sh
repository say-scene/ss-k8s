#!/usr/bin/env bash
# vLLM 부하테스트 1회 실행. control-plane 노드에서 실행한다(결과 hostPath가 이 노드에 있다).
#   ./run.sh
# 1. vLLM 롤아웃이 끝났고 파드가 1개인지 확인한다.
# 2. 실행 중인 파드 인자에서 --max-num-seqs를 읽어 Job 이름과 환경변수에 넣는다.
# 3. CronJob 틀에서 Job을 만들고 로그를 따라간다. 끊기면 kubectl -n loadtest logs -f job/<이름>으로 다시 붙는다.
# 결과: /opt/sayscene-loadtest/results/<Job 이름>/ (root 소유라 sudo로 읽는다)
set -euo pipefail

NS=loadtest
VLLM_NS=vllm
VLLM=vllm-qwen
RESULTS=/opt/sayscene-loadtest/results

kubectl -n "$VLLM_NS" rollout status "deploy/$VLLM" --timeout=60s

pods=$(kubectl -n "$VLLM_NS" get pod -l "app=$VLLM" -o name | wc -l)
if [ "$pods" -ne 1 ]; then
  echo "vLLM 파드가 ${pods}개다. 롤아웃이 끝나 1개만 남았을 때 실행한다." >&2
  exit 1
fi

slots=$(kubectl -n "$VLLM_NS" get pod -l "app=$VLLM" \
  -o jsonpath='{range .items[0].spec.containers[?(@.name=="server")].args[*]}{@}{"\n"}{end}' \
  | grep -x -A1 -- --max-num-seqs | tail -n1)
if [ -z "$slots" ]; then
  echo "vLLM 파드 인자에서 --max-num-seqs를 찾지 못했다." >&2
  exit 1
fi

# 측정이 겹치면 서로의 부하가 섞인다.
active=$(kubectl -n "$NS" get job -o jsonpath='{range .items[?(@.status.active)]}{.metadata.name}{" "}{end}')
if [ -n "$active" ]; then
  echo "진행 중인 측정이 있다: $active" >&2
  exit 1
fi

job="lt-slot${slots}-$(date +%Y%m%d-%H%M%S)"
echo "Job $job 생성 (max-num-seqs=$slots)"
kubectl -n "$NS" create job "$job" --from=cronjob/vllm-loadtest --dry-run=client -o yaml \
  | kubectl set env --local -f - "MAX_NUM_SEQS=$slots" -o yaml \
  | kubectl create -f -

# 이미지 pull이 끝나야 로그를 볼 수 있다. 바로 실패한 경우에도 아래에서 상태를 확인한다.
kubectl -n "$NS" wait --for=condition=Ready pod -l "batch.kubernetes.io/job-name=$job" --timeout=300s || true
kubectl -n "$NS" logs -f "job/$job" || true

# 파드가 끝난 직후에는 Job 상태가 아직 갱신되지 않았을 수 있다.
if ! kubectl -n "$NS" wait --for=jsonpath='{.status.succeeded}'=1 "job/$job" --timeout=30s >/dev/null; then
  echo "Job $job 이 성공하지 않았다. kubectl -n $NS describe job $job 으로 확인한다." >&2
  exit 1
fi
# 빈 칸이 있으면 column 정렬이 밀려서 그대로 출력한다.
sudo cat "$RESULTS/$job/summary.csv"
