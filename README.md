# ss-k8s

SayScene의 Kubernetes 애플리케이션 매니페스트와 플랫폼 Helm values를 관리한다. **AWS의 CPU Whisper STT와 로컬 GPU의 vLLM을 하나의 클러스터에서 연결**한다. FE 정적 파일과 애플리케이션 소스는 별도 저장소에 있다.

## 현재 구성

```text
Browser → CloudFront ┬→ S3: FE 정적 파일
                     └→ ALB → ss-be ┬→ Redis: 진행 중 대화
                                    ├→ ss-triton: CPU Whisper STT
                                    └→ vllm-qwen: 로컬 GPU LLM
```

- AWS는 kubeadm 기반 클러스터다. 기본 EC2가 control-plane과 일반 워커를 겸하고, Karpenter가 CPU 워커를 추가한다.
- STT는 전용 `t3.xlarge` 노드에서 실행한다. KEDA가 요청 부하에 따라 Pod를 1~2개로 조절한다.
- LLM은 RTX 4060 8GB를 가진 외부 WSL2 워커에서 실행한다. AWS와 로컬 워커는 WireGuard VPN으로 연결하며, VPN 구축 절차는 이 저장소의 범위가 아니다.
- **GPU Triton은 사용하지 않는다.** `ss-triton`은 기존 배포 이름을 유지한 CPU STT 서버다.

## 디렉토리

| 경로 | 역할 |
|---|---|
| [`apps/ss-be`](apps/ss-be) | Spring Boot API, Redis, ConfigMap·Secret, Ingress, ServiceMonitor |
| [`apps/ss-triton`](apps/ss-triton) | CPU Whisper STT와 KEDA ScaledObject |
| [`apps/ss-vllm`](apps/ss-vllm) | 외부 GPU vLLM, 로컬 모델 캐시 PV/PVC, ServiceMonitor |
| [`apps/loadtest`](apps/loadtest) | vLLM 직접 호출 부하 테스트와 결과 수집 |
| [`platform/karpenter`](platform/karpenter) | Custom AMI 기반 EC2NodeClass와 CPU NodePool |
| [`platform/keda`](platform/keda) | KEDA Helm Application |
| [`platform/kube-prometheus-stack`](platform/kube-prometheus-stack) | Prometheus·Grafana·Alertmanager·Exporter 설정 |
| [`platform/alloy`](platform/alloy), [`platform/loki`](platform/loki), [`platform/tempo`](platform/tempo) | 로그·추적 수집과 저장 |
| [`platform/dcgm-exporter`](platform/dcgm-exporter) | 외부 NVIDIA 노드의 GPU 메트릭 |
| [`init`](init) | 초기 설치용 파일. 현재 운영 설정과 구분해 사용 |
| [`apps/llm`](apps/llm) | **이전 CPU LLM 대안**. `replicas: 0`이며 현행 LLM 경로가 아님 |

## 서비스와 AI 설정

| 대상 | namespace / 연결 | 현재 설정 |
|---|---|---|
| BE | `sayscene` / `/api` Ingress | 8080, replica 1, Redis 연결 포함 readiness |
| Redis | `sayscene` / `ss-redis:6379` | Redis 7.4, 단일 replica, RDB·AOF·영속 볼륨 없음 |
| STT | `sayscene` / `ss-triton:8001` | faster-whisper `small.en`, CPU int8, threads 2, 동시 처리 1 |
| LLM | `vllm` / `vllm-qwen.vllm.svc.cluster.local:8000` | `Qwen/Qwen3-4B-FP8`, API 모델명 `qwen3-4b-fp8` |

BE의 실제 연결 주소와 모델명은 [`apps/ss-be/01_configmap.yaml`](apps/ss-be/01_configmap.yaml)에 있다. 브라우저는 내부 추론 서버에 직접 접속하지 않는다.

### 배치와 확장

- STT Pod는 `sayscene.io/workload=stt` 전용 노드에 배치한다. Pod당 CPU `3400m`·메모리 `2Gi`를 예약·제한한다.
- KEDA는 처리·대기시간으로 계산한 부하(`threshold: 0.8`)와 `STT_BUSY` 비율(`0.05`)을 사용한다. 축소 안정화 시간은 300초다. 최대 Pod 수를 바꾸면 STT NodePool의 CPU·메모리 상한도 함께 맞춘다.
- vLLM은 외부 NVIDIA 노드 selector와 `nvidia` RuntimeClass를 사용한다. 이미지 `vllm/vllm-openai:v0.32.0-cu129`, 모델 revision 고정, context 4096, `max-num-seqs: 1`(동시성 비교 실험 설정), GPU 메모리 비율 0.80이다. 슬롯은 실험에 따라 변경하므로 현재 매니페스트와 측정 조건을 구분한다.
- LLM 캐시는 `sayscene-gpu-wsl`의 local PV 30Gi를 사용한다. 노드에 종속되며, vLLM 교체 전략은 `Recreate`다. 로컬 GPU를 Karpenter가 자동 증설하지는 않는다.
- Karpenter CPU 워커는 SSM Parameter Store에서 kubeadm 가입 정보를 읽는다. AMI·IAM·SSM·VPN 등 외부 선행 설정까지 이 저장소가 자동 생성하는 것은 아니다.

## 관측성

Grafana 대시보드, FE→BE trace 조회, Loki 로그 조회, Discord 알림 수신은 운영에서 사용 중이다. 아래 설정은 메트릭·로그·추적의 역할과 저장 경계를 구분한다.

| 신호 | 수집·저장 경로 | 주요 관측 대상 |
|---|---|---|
| 메트릭 | Prometheus → ServiceMonitor/Exporter | BE HTTP·JVM, STT 대기·처리시간, vLLM TTFT·토큰·큐·KV 캐시, 노드·Pod·GPU |
| 로그 | Alloy logs DaemonSet → Loki → S3 | 배치된 노드의 Pod stdout/stderr, `namespace`·`pod`·`container` 라벨 |
| 추적 | FE → BE relay / BE Java agent → Alloy OTLP → Tempo → S3 | 사용자 동작과 BE·Redis·추론 HTTP 호출 연결 |
| 알림 | Prometheus rules → Alertmanager → Discord | 루트 디스크 사용률, vLLM 첫 토큰 지연 |

- BE `/actuator/prometheus`, STT·vLLM `/metrics`를 수집한다. BE ConfigMap은 HTTP histogram을 켜 API별 p95 계산을 지원한다.
- FE trace는 `/api/telemetry/traces`를 거친다. BE agent는 trace만 내보내고, 메트릭·로그 exporter는 꺼 중복 수집을 피한다. 추론 서버 내부 span을 별도로 추가한 구성은 아니다.
- 로그 Alloy에는 control-plane toleration만 명시되어 있다. **STT·GPU taint 노드의 로그까지 자동 수집한다고 가정하지 않는다.**
- Grafana는 `/grafana/` 하위 경로를 사용하고 Prometheus·Loki·Tempo를 조회한다. Loki·Tempo는 S3에 저장하지만 WAL·작업 파일은 임시 로컬 저장이며 Pod 교체 시 미전송 데이터가 유실될 수 있다.
- Prometheus는 local PV 5Gi, 보존 설정 `7d`·`1GiB`를 함께 사용한다. Grafana는 local PV 2Gi다. 이 구성은 HA나 데이터 무손실을 보장하지 않는다.
- Discord webhook은 `monitoring/alertmanager-discord` Secret에서 읽는다. 알림은 평일 09:00~19:00 KST에 전송하며 firing·resolved를 모두 보낸다. `vllm` namespace 경고는 Pod까지 그룹 기준에 포함한다. Git에 webhook URL을 넣지 않는다.
- 사용자 정의 알림은 루트 디스크 **85% 초과 10분**, vLLM **5분 평균 TTFT 1.2초 초과가 2분 유지** 조건이다. 세부 규칙·수신 시간은 해당 values를 기준으로 한다.

## 배포 경계

1. FE는 자체 CI에서 S3로 배포한다. BE·STT는 각 저장소 CI가 ECR에 `<SHA7>` 이미지를 push한 뒤 이 저장소의 `kustomization.yaml` 태그를 갱신한다.
2. Argo CD가 Application별 매니페스트·Helm values를 반영한다. Git push와 이미지 태그 변경만으로 모든 App의 Sync·Pod rollout이 완료되지는 않는다.
3. Helm 차트 Application 중 일부는 웹 콘솔에서 관리한다. 특히 kube-prometheus-stack의 `valueFiles` 순서와 수동 Sync·Server-Side Apply 설정은 [차트 README](platform/kube-prometheus-stack/README.md)를 따른다.
4. ConfigMap의 환경변수 변경은 기존 프로세스에 즉시 주입되지 않는다. 실제 Sync revision, 배포 이미지와 새 Pod의 설정을 함께 확인한다.

**저장소 루트에 `kubectl apply -f .`를 실행하지 않는다.** Helm values, 초기 설치 파일, 이전 앱과 현재 앱이 섞여 있다. Secret 값과 외부 리소스는 별도 준비하고 대상 Application의 Diff를 확인한다.

## 로컬 검증

저장소 루트에서 실행한다. `kubectl`의 Kustomize 기능과 Python 3, Bash가 필요하다. 아래 명령은 렌더링·자체 검사만 하며 클러스터에 적용하거나 부하를 발생시키지 않는다.

```bash
kubectl kustomize apps/ss-be > /dev/null
kubectl kustomize apps/ss-triton > /dev/null
kubectl kustomize apps/ss-vllm > /dev/null
kubectl kustomize apps/loadtest > /dev/null
python3 apps/loadtest/bench.py selftest
bash -n apps/loadtest/run.sh
```

Helm values는 위 Kustomize 검사 대상이 아니다. 차트 버전과 실제 Application의 values 순서로 별도 렌더링한다.

`apps/loadtest`의 CronJob은 기본 정지 상태다. **`run.sh`는 실제 Job을 만들어 LLM에 부하를 준다.** 실행 전 서비스 영향을 확인하며, 결과는 control-plane의 `/opt/sayscene-loadtest/results/<Job 이름>/`에 저장한다.

## 관련 문서

- [현재 PRD·아키텍처](https://github.com/say-scene/ss-docs/blob/main/english-speaking-prd-architecture-v1.md)
- [CPU AMI·클러스터 구축](https://github.com/say-scene/ss-docs/blob/main/al2023-cpu-cluster-karpenter-guide.md)
- [VPC CNI 네트워크 장애 재발 방지](https://github.com/say-scene/ss-docs/blob/main/al2023-vpc-cni-network-prevention-guide.md)
- [관측성 설치·운영](https://github.com/say-scene/ss-docs/blob/main/monitoring/sayscene-observability-manual-v1.md)
- [vLLM 동시 요청 부하 테스트 기록](https://github.com/say-scene/ss-docs/blob/main/loadtest/vllm-concurrency-loadtest-v1.md)
