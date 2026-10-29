# kube-prometheus-stack

Argo CD Application 정의는 깃에 없고 웹 콘솔에만 있다. 여기에 같은 값을 적어 둔다.
상세 설계는 `test/kube-prometheus-stack-설치.md`.

| 항목 | 값 |
|---|---|
| Application | `kube-prometheus-stack` |
| 차트 | `prometheus-community/kube-prometheus-stack` |
| repoURL | `https://prometheus-community.github.io/helm-charts` |
| targetRevision | `92.13.1` |
| values 소스 | `https://github.com/say-scene/ss-k8s.git` · `main` · `path: platform/kube-prometheus-stack` · `ref: values` |
| Destination namespace | `monitoring` (Auto-create 켬) |
| Sync | 수동 · **Server-Side Apply** |
| Prune · Force | 끔 |

`Server-Side Apply` 를 켜지 않으면 CRD 가 커서 annotation 256KB 한도에 걸려 실패한다.
`Destination namespace` 를 비우면 `default` 로 간다 (Karpenter 전례).

## valueFiles 순서

뒤 파일이 앞을 덮는다. 순서를 바꾸면 결과가 달라질 수 있다.

```
$values/platform/kube-prometheus-stack/common/values.yaml
$values/platform/kube-prometheus-stack/prometheus-operator/values.yaml
$values/platform/kube-prometheus-stack/prometheus/values.yaml
$values/platform/kube-prometheus-stack/grafana/values.yaml
$values/platform/kube-prometheus-stack/alertmanager/values.yaml
$values/platform/kube-prometheus-stack/prometheus-node-exporter/values.yaml
$values/platform/kube-prometheus-stack/kube-state-metrics/values.yaml
```

**파일을 추가·삭제하면 Argo CD 웹 콘솔의 `valueFiles` 목록도 함께 고쳐야 한다.**
깃만 고치면 반영되지 않는다.

## 폴더 규칙

폴더명은 `values.yaml` 안의 최상위 키와 같다. 그래서 `node-exporter` 가 아니라
`prometheus-node-exporter` 다. 폴더만 보면 안에 어떤 키가 있는지 알 수 있다.

values 파일은 **하위 폴더에만** 둔다. Argo CD 는 `ref` 소스의 `path` 를 매니페스트로도
렌더링하는데 디렉터리 스캔은 비재귀라, 하위 폴더의 values 는 읽히지 않는다.
최상위의 `storage.yaml` 은 반대로 **의도적으로 적용되는 매니페스트**다.

## 전제

- 저장소는 기본 노드의 `/var/lib/prometheus` 를 `storage.yaml` 의 local PV 로 노출하고,
  `storageSpec.volumeClaimTemplate` 이 그것을 쓴다. 전환 기준은 설치 문서 §5-0.
- **PV 의 `nodeAffinity` 에 기본 노드 이름이 박혀 있다.** 노드를 교체하면 같이 고친다.
- **설치 전에 기본 노드에서 한 번 실행해야 한다.** local PV 는 `hostPath` 와 달리
  디렉터리를 자동으로 만들지 않는다. 없으면 `FailedMount: path does not exist` 로 멈춘다.

  ```bash
  sudo mkdir -p /var/lib/prometheus /var/lib/grafana
  ```

  소유권은 `fsGroup` 이 맞추므로 `chown` 은 필요 없다 (local PV 는 적용 대상).
- Grafana 도 같은 방식으로 기본 노드의 `/var/lib/grafana` 를 쓴다 (`grafana-local`).
  켜지 않으면 `grafana.db` 가 `emptyDir` 라 대시보드가 롤아웃마다 사라진다.
- Grafana 관리자 비밀번호는 `grafana-admin` Secret 에 있다. 깃에는 이름만 둔다.
  **Sync 전에 Secret 을 먼저 만들어야 한다.** 명령은 `grafana/values.yaml` 주석에 있다.
- Grafana 외부 노출은 보류. 1 단계는 `port-forward` 로 확인한다.
