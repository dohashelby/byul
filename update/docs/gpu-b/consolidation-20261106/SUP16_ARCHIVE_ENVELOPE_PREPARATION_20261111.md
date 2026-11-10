# SUP-16 archive 전송 준비

원 GB12 archive는 14,636,464B이며 기존 읽기 전송이 CLI 연결 시간 초과로 끝났다. 전체 길이로 선할당된 로컬 파일에는 빈 구간이 있어 원 SHA와 다르다. 이를 완성 ZIP이나 복원된 Ref로 처리하지 않는다.

`tools/gpu_b_archive_envelope.py`는 원본을 읽어 새 XZ 전송 JSON을 만들고, 별도 새 파일에 복원하는 B 준비 도구다. AWS 호출·기동·모델 실행은 포함하지 않는다. 현재 상태는 **CPU 준비 검증 완료, 실제 운영 연결 미완료**다.

## 입출력 계약

```python
encoded = encode(source, new_envelope, reference, request_uuid, check_remaining)
decoded = decode(received_envelope, new_destination, reference, encoded, check_remaining)
```

`check_remaining`은 호출자가 기존 기한을 검사하는 함수다. 모듈은 새 기한·재시도 정책을 만들지 않는다. encoder의 실제 반환 영수증은 envelope bytes/SHA, 요청 UUID와 기동 신원을 연결하는 신뢰 입력이다. decoder가 수신 파일에서 영수증을 새로 만들면 전달 무결성 검증을 대체하게 되므로 그렇게 사용하지 않는다.

실제 보관 자료는 모듈의 `ACTUAL_REFERENCE`와 정확히 일치해야 한다. 합성 시험은 `EXPLICIT_SYNTHETIC_CPU`의 자기 파일 경로·bytes·SHA·UUID를 사용한다. 합성 결과를 실제 GB12 증거로 바꾸지 않는다.

- 원본 상한 512MiB, 압축 상한 3MiB, 전체 JSON 상한 4MiB다. 초과하면 전송 적격 입력으로 채택하지 않는다.
- 입력은 NOFOLLOW 디렉터리 FD 탐색, UID1000·일반 파일·0600·단일 링크, 원 bytes/SHA와 읽기 전후 파일·부모 신원을 확인한다.
- encoder는 UID0 또는 UID1000, decoder는 UID1000에서 실행한다. root는 자기 호출이 배타 생성한 새 scratch FD에만 소유권을 설정한다. 기존 부모·원본·목적 파일의 권한은 바꾸지 않는다.
- 새 출력은 O_EXCL scratch와 기존 이름을 덮어쓰지 않는 발행으로 만든다. 실패 시 자기 scratch만 정리한다.
- 압축은 XZ/SHA256 check·8MiB encoder dictionary, 복원은 표준 incremental decoder·64KiB 청크·16MiB liblzma 메모리 제한을 사용한다. 16MiB는 전체 프로세스 RSS 한도가 아니다.
- 복원은 단일 XZ stream의 EOF·unused data, 정확한 해제량과 원 SHA를 확인한다. trailing·연결 stream·초과 해제량을 거부한다. ZIP 내부 CRC는 이 모듈의 완료 판정에 포함하지 않는다.

## 확인한 범위

고정 소스는 22,363B, SHA256 `6de3190f37d333bd1055e6abe706addb2a606405f701cf710712150852e1591f`다. S4의 독립 정적 대조 41개는 불일치0이며 CPU 시험 횟수와 구분한다.

Linux UID1000/Python3.12.3에서 명시 합성 ZIP을 실제 encoder에 넣고 실제 decoder로 새 파일에 복원했다. 1계약의 양성·음성 21분기가 통과했다. bytes 일치, SHA 일치, 변조 전달·요청 혼입·잘못된 압축/복원 SHA·trailing·연결 stream·과대 dictionary·초과 해제량·기존 경로·symlink·기한 예외를 확인했다. FD는 4→4, scratch 잔여0이다. 내부 거부 경로용 합성 신뢰 영수증은 음성 시험으로 명시했고 양성은 실제 encoder 반환값을 사용했다.

root UID0/Python3.12.15의 신규 출력 소유권 경계는 별도 1계약으로 확인했다. 자기 합성 부모 root0/0755와 원본 UID1000/0600을 유지하고 새 출력만 UID/GID1000·0600·단일 링크로 발행했다. 기존 목적 파일 재사용을 거부했고 기존 marker·원본은 유지됐다. decoder를 반복 실행한 결과가 아니다.

시험 요약과 원 영수증 지문은 [CPU 범위 JSON](sup16-archive-envelope-cpu-scope-20261111.json)에 보존한다. 이전 미실행 초안과 수동 XZ parser의 정적 오류는 성공 시험으로 집계하지 않는다. 최종 소스는 직접 Block/Index/Footer parser를 제거했다.

## 실제로 남은 범위

실제 GB12 압축률·guest Python의 lzma 지원·producer 배치/호출·trusted 영수증 반출·기존 GET 연결·전체 전송은 미확인이다. 합성 ZIP의 높은 압축률을 실제 GB12에 적용하지 않는다. 원 전체 bytes/SHA가 일치한 새 복원 파일이 확보된 뒤 ZIP의 첫 전체 CRC와 의미·Refs 대조를 수행해야 한다. 기존 부분 파일과 FAIL·UNKNOWN은 보존한다. 이 준비 작업에서는 AWS/GPU를 추가 호출하지 않았다.

표준 decoder 의미는 [Python 3.12 lzma 문서](https://docs.python.org/3.12/library/lzma.html#lzma.LZMADecompressor)를 따른다.
