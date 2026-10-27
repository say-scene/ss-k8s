#!/bin/bash
# llm 워커 AMI 생성 직전 빌더 인스턴스 정리. 패키지는 건드리지 않는다.
# 사용: sudo bash cleanup.sh → 재접속하지 말고 콘솔에서 정지 후 AMI 생성.
set -euo pipefail

MODEL_DIR=/opt/models/Qwen3-4B-Instruct-2507
VLLM_IMAGE=docker.io/vllm/vllm-openai-cpu:v0.32.0-x86_64
USER_HOME=/home/ec2-user

[[ $EUID -eq 0 ]] || { echo "sudo로 실행하세요." >&2; exit 1; }

# 1. 사전 검사: 조인 흔적이 있으면 새 노드가 가입을 건너뛴다.
[[ ! -e /etc/kubernetes/kubelet.conf ]] || { echo "kubelet.conf 존재: 조인된 인스턴스입니다." >&2; exit 1; }
[[ -f "$MODEL_DIR/config.json" ]] || { echo "모델 파일이 없습니다: $MODEL_DIR" >&2; exit 1; }
ctr -n k8s.io images ls -q | grep -x "$VLLM_IMAGE" >/dev/null || { echo "vLLM 이미지가 없습니다: $VLLM_IMAGE" >&2; exit 1; }

# 2. 다운로드 캐시 (가중치 본체와 패키지는 유지)
rm -rf "$MODEL_DIR/.cache" "$USER_HOME/.cache/huggingface" "$USER_HOME/.cache/pip"
dnf clean all

# 3. 인스턴스 고유 정보: 새 인스턴스 첫 부팅 때 재생성된다.
cloud-init clean --logs
truncate -s 0 /etc/machine-id

# 4. 빌더 접속 키와 셸 기록
rm -f "$USER_HOME/.ssh/authorized_keys" /root/.ssh/authorized_keys
rm -f "$USER_HOME/.bash_history" /root/.bash_history

# 5. 로그와 임시 파일
journalctl --rotate
journalctl --vacuum-time=1s
find /var/log -type f -name '*.gz' -delete
find /tmp /var/tmp -mindepth 1 -delete

echo "== 남은 모델: $(du -sh "$MODEL_DIR" | cut -f1)"
echo "== vLLM 이미지: $VLLM_IMAGE"
echo "완료. 재접속하지 말고 콘솔에서 인스턴스를 정지한 뒤 AMI를 생성하세요."
