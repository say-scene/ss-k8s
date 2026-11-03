#!/usr/bin/env bash
# vLLM 부하테스트 1회 실행. control-plane 노드에서 실행한다(결과 hostPath가 이 노드에 있다).
#   ./run.sh
# 1. vLLM 롤아웃이 끝났고 파드가 1개인지 확인한다.
# 2. 실행 중인 파드 인자에서 --max-num-seqs를 읽어 Job 이름과 환경변수에 넣는다.
# 3. CronJob 틀에서 Job을 만들고 바로 끝난다. 진행 상황은 출력되는 kubectl logs 명령으로 본다.
# 결과: /opt/sayscene-loadtest/results/<Job 이름>/ (root 소유라 sudo로 읽는다)
set -euo pipefail

NS=loadtest
VLLM_NS=vllm
VLLM=vllm-qwen
RESULTS=/opt/sayscene-loadtest/results

kubectl -n "$VLLM_NS" rollout status "deploy/$VLLM" --timeout=60s >/dev/null

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

# 단계: 1(기준) → slot(배치 꽉 참) → slot×2 → slot×4(대기열). slot 1이면 1이 겹쳐 뺀다.
# 단계당 건수는 모든 단계 같다. 최고 단계에서도 워커당 10건이 되게 slot×4×10.
if [ "$slots" -eq 1 ]; then
  concurrency="1 2 4"
else
  concurrency="1 $slots $((slots * 2)) $((slots * 4))"
fi
requests=$((slots * 4 * 10))

job="lt-slot${slots}-$(date +%Y%m%d-%H%M%S)"
kubectl -n "$NS" create job "$job" --from=cronjob/vllm-loadtest --dry-run=client -o yaml \
  | kubectl set env --local -f - "MAX_NUM_SEQS=$slots" "CONCURRENCY=$concurrency" "REQUESTS_PER_STEP=$requests" -o yaml \
  | kubectl create -f - >/dev/null

echo "Job $job (max-num-seqs=$slots, 동시 요청 $concurrency, 단계당 ${requests}건)"
echo "로그: kubectl -n $NS logs -f job/$job"
echo "결과: sudo cat $RESULTS/$job/summary.csv"
