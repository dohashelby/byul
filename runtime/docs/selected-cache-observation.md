# SUP007/015 최소 cache I/O 관측

고정 source의 TG-CACHE는 `security_file_permission(mask=2)`만 선택한다(`enforcement/tetragon.py:149–159`). 기존 normalize는 `path/access/identity`를 보존하지만 syscall 성공 반환값·실제 write byte를 보존하지 않는다(`events.py:356–424`). 이 원자료는 permission check이며 실제 생성/읽기라고 판정하지 않는다. 기존 collector의 drop/gap/확인 시각도 보존한다.

`tools/selected_cache_metadata.py`는 별도 tracing policy나 수집 서비스를 만들지 않는 stopped-file 후처리다. tracer/엔진/프로세스 신호를 실행하지 않는다.

- `inventory --root G1 --out NEW`: 기존 선언의 compiler 하위경로만 no-follow 순회·regular file hash/stat. 기본2000entry/256file/64MiB per-file/256MiB total/10초, bound·변경·symlink는 partial. 원내용은 출력하지 않는다.
- `tetragon --root G1 --input EXISTING_NORMALIZED_JSONL --pod-uid UID --container-id CID --out NEW`: 기존 normalized TG-CACHE의 같은 confirmed PodUID/CID·compiler 경로만 permission check로 선택한다. PID가 없으면 누락, UID가 없으면 null이다.
- `strace --root G1 --input PRIVATE_STOPPED_TRACE --pid HOST_PID --uid OBSERVED_UID --out NEW`: 아래 raw numeric 명령의 완료된 open과 read/write/mmap 경로 후보만 정제한다. FD는 같은 PID 안의 관측된 open을 후보 힌트로만 연결한다. 각 I/O 후보는 `candidate_path`와 `descriptor_path_verified=False`를 갖는다. clone/thread/상속/unfinished/resumed/실패/미지FD는 누락으로 보존하며 불명확한 FD 변경·다른 PID·불완전 행은 기존 힌트를 비운다. mmap은 mapping 후보이며 실제 page read를 합격으로 만들지 않는다.

모든 report는 generation_root를 선언으로 기록한다. 기존 actual asset report의 PodUID/CID·hostPath/mount/compile_profile과 이 root를 결속해야 한다. process binding·coverage·전체 compiler 소비·security verdict는 자동 통과시키지 않는다. root 밖의 report 경로에0600 새 파일만 생성한다. active model/cache에 report를 쓰거나 기존 파일을 덮지 않는다.

## 선정 하위경로

기본 `triton`, `inductor`, `cuda`, `vllm/torch_compile_cache`, `home/.cache/flashinfer`, `xdg/flashinfer`와 `.so/.cubin/.ptx/.o/.a/.bin/.json/.py/.pkl`의 metadata만 허용한다. 실제 ENV/source에서 다른 compiler 경로를 확인했으면 `--prefix`로 그 상대경로만 명시한다. HF token·model·args/env·다른 디렉터리는 반출하지 않는다. hash는 실행/역직렬화하지 않는다.

## root 전용 strace 실행 recipe

공식 [strace manual](https://man7.org/linux/man-pages/man1/strace.1.html)의 `-e raw=...`는 인자를 numeric hex로 출력하고, `-e read=.../-e write=...`는 원데이터 dump를 켠다. 이 recipe는 후자의 dump와 execve/명령행/env trace를 사용하지 않는다. `-yy`는 FD 경로를 제공한다. 이 옵션 확인은 공개 문서의 명세 확인이며 실제 host strace 실행은 root 담당이다.

1. root가 기존 PID/context 수집기로 host PID·start_ticks·UID·boot/ns·선정 cgroup/containerID를 trace 전후 확인한다. PID 재사용/namespace·CID 변경/확인불가는 unbound다. 프로세스 검색·argv/env 출력 대신 root가 이미 선택한 process만 사용한다. 이 source는 PID/UID를 CLI에서 받으므로 해당값의 실제 근거를 함께 보존한다.
2. cold G1 file0 → 정상기동/요청 뒤 inventory의 실제 compiler artifact 생성과 기존 TG write permission event를 결속한다. inventory의 새파일과 TG permission만 있으면 그 관측까지만 기록한다.
3. warm startup read가 필요하면 root/B가 가진 실제 owned hold-start/launch barrier가 있을 때 trace를 먼저 부착한 뒤 시작한다. barrier가 없어서 startup read를 놓치면 미관측으로 둔다. D는 새 엔진 시작/kill driver를 만들지 않는다.
4. root private0700 디렉터리에 umask077을 적용하고 하나의 strace 출력 파일만 쓴다. root가 설치판 `strace -h`에서 지원을 확인한다. 15초의 wall timeout과8MiB `RLIMIT_FSIZE`를 root wrapper에 적용한다. 지원판이면 `--syscall-limit=10000`도 추가한다. tracer 제한 도달은 coverage complete가 아니다. `--kill-on-exit`은 사용하지 않는다.

아래 argv는 root/B가 PID와 원본 경로를 정확히 주입한다. 외부 command/engine argv를 strace 뒤에 넣는 방식으로 바꾸지 않는다.

```text
strace -f -ttt -yy \
  -e trace=openat,openat2,read,pread64,write,pwrite64,mmap,close \
  -e raw=read,pread64,write,pwrite64,mmap,close \
  -p EXACT_HOST_PID -o ROOT_PRIVATE_0600_SINGLE_TRACE
```

root의 timeout wrapper는 tracer만 종료·detach하고 engine 정상 상태를 재확인한다. kernel/ptrace 권한·지원옵션 문제는 원 오류로 남긴다. 원 trace는 host 안에 유지하고 정제 출력만 비밀 메모리 검사/허용목록을 거쳐 반출한다. decode된 read/write buffer가 들어오면 collector는 해당 행을 폐기하고 `unsafe_decoded_buffer`로 partial을 기록한다. 길이 상한·private 원 trace 잘림도 원자료로 보존한다.

## G0/G1/warm/M4 결합

`source-support` dtype recipe의 같은 image/model, bf16 G0→float16 G1·새leaf/copy0와 actual binding을 쓴다. 같은 Pod restart warm에서는 새CID/start와 같은 G1 root를 확인한다. `read`/`write` 양수 반환은 해당 PID의 숫자 FD I/O와 관측된 open 경로 후보까지만 기록한다. 공유 FD-table의 변경을 완전히 포괄하지 못하므로 모든 후보의 `descriptor_path_verified`와 `actual_cache_read_verified`는 False이다. 실제 cache 경로 소비는 별도의 source-backed 안전 로그/직접 경로 결속 근거가 필요하다. compiler가 의미 있게 재사용한 범위는 source-backed 선택경로와 engine 안전 로그의 cached load/rebuild 원자료를 함께 확인한다. Inventory는 최초 no-follow stat, 열린 FD의 전후 version, 최종 경로 stat를 대조하고 파일·상위 디렉터리가 바뀌면 partial로 남긴다. 파일 존재나 normal HTTP만으로 read/reuse를 만들지 않는다. M4 permission selector만 바꾼 조건의 compiler tuple/root 동일성과 정상 대조를 기록한다.

UID fixture는 별도 filesystem 권한 근거다. 전체 cache key provenance·동일UID 위협·새 framework·전체GPU 인수를 추가 조건으로 만들지 않는다. 실제 수집0인 준비 단계는 UNKNOWN이다.
