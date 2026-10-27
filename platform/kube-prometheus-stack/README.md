# kube-prometheus-stack

Argo CD Application 정의는 깃에 없고 웹 콘솔에만 있다. 여기에 같은 값을 적어 둔다.
상세 설계는 `test/kube-prometheus-stack-설치.md`.

| 항목 | 값 |
|---|---|
| Application | `kube-prometheus-stack` |
| 차트 | `prometheus-community/kube-prometheus-stack` |
| repoURL | `https://prometheus-community.github.io/helm-charts` |
| targetRevision | `92.13.1` |
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

최상위에 `.yaml` 을 두지 않는다. Argo CD 는 `ref` 소스의 `path` 를 매니페스트로도
렌더링하므로, values 파일이 최상위에 있으면 매니페스트로 읽힐 소지가 있다.

## 전제

- `/var/lib/prometheus` 는 kubelet 이 자동으로 만들고(`DirectoryOrCreate`),
  소유권은 `chown-data` initContainer 가 맞춘다. **노드에서 손으로 할 일은 없다.**
- 저장소는 루트 디스크를 쓴다. 전환 기준은 설치 문서 §5-0.
- `busybox` 이미지를 Docker Hub 에서 받는다. 노드의 인터넷 경로가 필요하다.
- Grafana 외부 노출은 보류. 1 단계는 `port-forward` 로 확인한다.
