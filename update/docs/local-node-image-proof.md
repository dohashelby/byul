# 선택적 로컬 노드 이미지 검증

`node_image_verification = "local-containerd-v1"`은 controller가 대상 Kubernetes 노드에서 실행될 때 사용한다. 기존 reader의 기본값은 `None`이며 node image 검증 결과는 `False`다.

실행 operator는 같은 노드의 `/etc/machine-id`, boot ID와 `/sys/class/dmi/id/product_uuid`를 읽을 권한이 필요하다. EC2의 DMI 파일이 root0600이면 controller도 root 권한으로 실행하며 Kubernetes 인증 파일을 `KUBECONFIG`로 명시한다. 파일 권한은 변경하지 않는다. node subprocess의 `sudo -n`도 허용되어 있어야 한다. 읽기 권한 부족은 이미지 불일치와 구분하여 `node_image_failure_codes`에 고정 코드 `NODE_IMAGE_READ_PERMISSION_DENIED`로 남긴다. 예외 원문·경로·인증정보를 이 진단에 포함하지 않는다.

검증은 다음 읽기를 수행한다.

1. Kubernetes Node의 machineID·bootID·systemUUID·architecture와 현재 Linux/amd64 호스트를 대조한다.
2. 정확한 CID의 containerd 레코드와 Pod UID·namespace·Pod·container label을 대조한다.
3. named image target의 OCI index/선정 manifest/config 원 바이트 SHA와 선언 image digest를 확인한다. CRI reported digest가 index·manifest·config 중 어느 참조인지 구분한다.
4. config의 rootfs diff_ids로 chain ID를 계산하고 실제 active snapshot의 parent와 대조한다.
5. 고정 grpcurl1.9.3의 정확한 Snapshots/Mounts 조회 RPC와 실제 task의 `/proc/PID/mountinfo` root overlay lowerdir/upperdir/workdir 객체(dev/inode)를 대조한다. container/snapshot/RPC mount/process를 다시 읽는다.

노드 명령은 `sudo -n ctr`의 info/list/content get, 고정 grpcurl Mounts 조회와 `sudo -n python3 -I -S -B` 프로세스 reader로 제한된다. gRPC는 명시적 Unix URI `unix:///run/containerd/containerd.sock`, namespace `k8s.io`의 Mounts 메서드만 선언한 최소 proto를 사용한다. Activate/prepare/mount 작업은 호출하지 않는다. grpcurl binary는 root 소유·불변 읽기·정확 SHA256 `62e2e4315bb70fab2e27f86c1f7738d09076a097a2dc8e0f701e386251172e40`로 검사한다. 고정 grpcurl1.9.3의 `BlockingDial`은 network 인자를 실제 dial에 사용하지 않으므로 `-unix`와 함께 위 `unix:///` URI를 지정한다. 전체 예산은 최대30초다. 키·token·CRI Spec/환경변수와 OCI config 원문은 파일이나 보고서에 저장하지 않는다. 동적 Spec 원문 해시도 저장하지 않는다. 공개 영수증에는 identity projection과 OCI digest chain, 선택한 PID·namespace, 명령/선정자료의 해시와 byte 수를 남긴다.

CLI 수집 예시:

```sh
sudo -n env KUBECONFIG=/home/ubuntu/.kube/config \
  PYTHONPATH=runtime/src:update/src:platform/api/src:runtime \
  /operator/venv/bin/python runtime/tools/update_engine_link.py capture \
  --environment /operator/ENV.json --target /operator/native-target.json \
  --prepared /operator/assets/manifest.json --duration 180 \
  --node-image-verification local-containerd-v1 --seal \
  --output /operator/new-native-capture.json
```

`--seal`은 지정한 새 observer session의 기존 seal 절차를 수행한다. 이미 종료·seal한 세션의 원자료를 수집할 때는 이 옵션을 생략한다. 출력 경로는 새 파일이어야 한다.

기존 LIF-10 공개 실행기를 사용할 때는 `[request_termination]` 설정의 `target`, `prepared`, `max_duration_seconds`와 함께 위 `node_image_verification`을 명시한다. 실행기는 기존 정상 대조·선정 stream close·idle 관측·seal·후속 정상 대조 절차를 수행하고 기존 EVD/RES/RPT 경로를 사용한다.

구현 API는 `KubernetesLifecycleReader(..., node_image_verification='local-containerd-v1')`다. collector의 kernel/runner/process 주입은 EXAMPLE CPU 시험으로 기록된다. 다른 노드·CID·이미지·snapshot, 불완전 전송과 권한 부족은 검증 실패로 남고 신호 대상이나 native 완료를 승인하지 않는다.

읽기 전용 CLI 형태와 metadata 의미는 [containerd v2.2 containers](https://github.com/containerd/containerd/blob/v2.2.0/cmd/ctr/commands/containers/containers.go), [snapshot info](https://github.com/containerd/containerd/blob/v2.2.0/cmd/ctr/commands/snapshots/snapshots.go), [Container metadata](https://github.com/containerd/containerd/blob/v2.2.0/core/containers/containers.go)의 고정 소스를 대조했다. 실제 배포에서는 해당 runtime의 반환 schema를 닫힌 parser로 다시 확인한다.

Mounts wire는 실제 node의 [containerd v2.3.7 snapshots proto](https://github.com/containerd/containerd/blob/v2.3.7/api/services/snapshots/v1/snapshots.proto)와 [Mount 구조](https://github.com/containerd/containerd/blob/v2.3.7/api/types/mount.proto)의 필드번호를 대조했다. root mount 옵션의 경로는 메모리에서만 사용하며 영수증에는 객체 identity hash를 남긴다.

명시적 Unix URI는 [grpcurl v1.9.3 BlockingDial](https://github.com/fullstorydev/grpcurl/blob/v1.9.3/grpcurl.go#L572)의 실제 gRPC dial 경로를 확인해 적용했다.
