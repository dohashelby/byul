# SUP-16 요청별 root client 준비

실제 SUP-16 R5는 UID1000 helper의 `reader.snapshot()`에서 `KUBERNETES_CAPTURE_BINDING_UNAVAILABLE`로 실패했다. 바깥 root 운영과 helper의 kubeconfig 전달 경계가 달랐으며, 이후 D1에서 기존 sudo root→선정 Kubernetes API 조회가 성공했다. 이 API 조회 성공은 실제 capture나 제품 Ref 생성 성공이 아니다.

B 전용 요청별 감독 후보는 UID1000 helper·입력·출력을 유지하면서, 정확한 기존 root client에 한 요청만 위임한다. 상주 서비스·새 권한·kubeconfig 복사·chmod·A 공통 코드 변경은 없다. 선정 context/namespace/pod/container와 고정 wrapper 네 개의 SHA를 바인딩하고, 원 파이프 수집기 6,134bytes/SHA `25c81666a17e13651c0b5b3bd67c9f310c6feee8311fcac408404d895dc67290`를 변경 없이 전용 namespace에서 사용한다.

원 요청의 최대 5초 안에서 정리 0.65초와 결과 전달 0.35초를 예약한다. sudo·Python 시작과 namespace 준비도 같은 절대 기한에 포함한다. 실제 kubectl의 request timeout과 고정 원격 wrapper의 실행 budget은 남은 active budget 이하로 줄이며, 요청 argv와 실제 argv의 SHA를 별도로 남긴다. 원 수집 결과의 stdout/stderr bytes·returncode·capture flags를 보존하고, 별도 감독 결과를 추가한다. 기한 초과·불완전 wire·정리 미확인은 실패로 남기며 다음 요청을 자동 진행하지 않는다.

감독은 자기 요청이 만든 client process group만 신호 대상으로 삼고, 정확한 UID1000 caller의 PID/start를 확인한다. 일회성 PID/mount namespace의 PID1에 부모 종료 신호를 설정해 감독이 강제 종료될 때 같은 요청의 별도 session 자식도 종료되도록 한다. mount propagation은 새 mount namespace에서 private으로 바꾸고 호스트 proc/config를 수정하지 않는다. 원 host proc FD는 외부 caller·부모 신원 읽기에만 사용하고, 요청의 child PID는 새 namespace 기준으로 구분한다. 권한을 낮추면 부모 종료 신호가 해제되므로 UID/GID drop 뒤 재설정하고 정확한 부모 생존을 다시 확인한다. 이 커널 의미는 [Linux man-pages](https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html), [PID namespace 문서](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html)와 대조했다.

root Python은 파일 경로를 바로 실행하지 않는다. 고정 stdlib bootstrap이 ancestor FD/NOFOLLOW·NONBLOCK·REG·UID1000/mode0600·nlink1·size·FD identity·SHA를 확인한 동일 bytes를 compile한다. 이는 비정규 파일의 blocking open과 인터프리터의 선행 경로 읽기를 구분해 보수한 범위다. component와 원 bounded 사본은 UID1000/mode0700 전용 디렉터리 안의 REG0600 파일이어야 한다. 공개 저장소 파일을 실행 가능한 운영 배치로 간주하지 않고, 별도 배치기가 정확 bytes와 mode/owner를 고정한다.

검증은 단계별 source와 원자료를 보존한다. 초기 core6의 정상 raw, timeout/overflow, caller SIGKILL, leader0+descendant pipe, stale-start 거부를 재사용한다. stale-start 거부는 실제 PID 재사용 재현이 아니다. namespace4는 새 source의 FIFO 거부·정상 namespace 실행·outer root/PID1 SIGKILL을 확인했으며 최초 tool 출력 projection을 Linux 원 byte로 표현하지 않는다. 최종 bootstrap/postdrop4는 실제 raw/receipt와 연결해 검증된 무해 fixture의 동일 bytes 실행, wrong SHA/FIFO 선행 거부, UID/GID1000으로 낮춘 뒤의 부모 SIGKILL 정리를 확인했다. 초기 fixture 시점·marker JSON race 실패와 수정 harness 이력을 보존하며 이미 통과한 core/namespace 검증을 반복하지 않았다.

현재 이 기록은 CPU 준비 범위다. 실제 EC2의 sudo root Python·namespace·Kubernetes API wire·N1 helper 전체 소비와 정상/도전/복구 세 역할은 별도 실측 대상이다. 다음 운영은 새 배포·소비 연결을 GPU 정지 상태에서 완성한 뒤, 같은 기존 1대의 한 번 기동에서 실제 preflight→준비된 역할 순서로 진행한다. preflight만 확인하기 위한 별도 선행 기동이나 GPU를 켠 채 구현 대기는 하지 않는다. kernel uninterruptible task는 종료 보장 범위 밖이며 미확인 정리를 성공으로 표시하지 않는다. PACK33 DRAFT·LAB10/33·기존 FAIL/UNKNOWN/Refs는 변경하지 않는다.

공개 [B 전용 component](../../../tools/gpu_b_root_client.py)는 S2 최종 원 bytes 28,637B/SHA `e359cf357627e3f5c26e1300e489132be045b6ecffd0fa708162bc1d3604581b`와 같다. 기존 core source/raw의 root 독립50대조/불일치0(SHA `42eb6c334b28fe8fa4fc0c67047ec79be6a981a8adcc1717d0a3a961ba402f10`)과 S4 최종 변경분56대조/불일치0(SHA `d4cc954aad1f5faf0f074387812dcc01c6f3e9755109833e85d1f0756e952bde`)을 연결했다. S4는 코드 작성·새 실행 없이 source/원 raw/receipt와 기존 assert14개 보존을 확인했다. 새 S6 검토로 표시하지 않는다.

최종 새 CPU receipt SHA `2e3e50fbbd150849f3d1a6b01edeb07ea8dc33f86304f29052f79c30be353657`, stdout4,363B/SHA `d0ca2070b8db70a03d481a18757864d48f9adc1e00ba228db5bf229efe5c66fe`다. receipt의 `/usr/bin/python3.12 -I -S -B` argv와 별도 관측하지 않은 patch version/flag state를 구분한다. bootstrap fixture 성공을 전체 component `_entry`/wire 성공으로 확대하지 않는다. [공개 범위 요약](sup16-root-client-cpu-scope-20261111.json)은 개인 경로·계정·인스턴스·실측 credential을 포함하지 않는다.

이 도구는 보호된 제품528파일 목록 밖의 B 운영 준비 코드로 별도 검토했다. N1 helper/adapter/resolver와 새로운43 배포 파일의 실제 결합·검토는 별도이며, 이 공개 복사만으로 완료했다고 표시하지 않는다. 실제 runtime은 원 `2cb01e9f`/466파일과 별도로 식별하고 현재 공개 main 계열528파일의 GPU 실측으로 대체하지 않는다.
