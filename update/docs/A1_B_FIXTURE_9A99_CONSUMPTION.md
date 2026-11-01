# B fixture 확정본 소비와 A-1 원본 검사 보수

B가 PR154 댓글6545166216에서 `9a99d7045464df32b4a91053513cb1d0695ab315`를 확정했다. 부모는 공개 PR171 `dd5077c`이고 `test_b_lif07_native_capture.py` 한 파일의4줄만 바꿨다. 원 SHA5376fc3e에서 확정 SHA9818506b로 바뀌었으며 역사 c39 gate의 정확 Git bytes/SHA와 원 assert를 유지한다. A-1은 이 원본을 재작성하지 않았다.

이 소스의 정확 CI41213357922는 Python3.12.3에서10012통과·2실패·19제외, 다른4검사는 성공했다. fixture14오류는 없어졌다. 남은 하나는 A-1의 원 B1608파일 검사가 현재 파일도 영구히 원본이라고 가정한 오류다. 다른 하나는 새 gate00979cf6/common df6255e 조합이 아직 B의 승인 목록에 없어 public native main이 거부되는 기존 실패다.

A-1 검사만 최소 보수한다. B160 원8개 승인 hash는 바꾸지 않고 고정 B160 commit의 역사 bytes로 모두 확인한다. 현재7파일은 원 승인값과 같아야 하며, 바뀐 fixture1파일은 B9a99d70의 고정 bytes 및 검토한 literal SHA9818506b와 같아야 한다. 다른 파일·미승인 교체는 허용하지 않는다. Git/blob 부재에는 fallback이 없다. 기존 새 A fullscope 정상 허용·B 미승인 조합의 수집 전 거부·세 정확 B 조합 검사는 유지한다.

guard·팩·의존성·공통 gate·B helper858/c9는 그대로다. A-1의 기존 Linux3.12.3 동일 제안108검증과 DD guard의 새 선정114검증·Windows/Linux 설치284파일/4사례/24Ref 근거를 재사용한다. 이 소비를 새 B 조합 승인이나 CI5 전체 성공·실제 serving/worker/GPU 실증으로 처리하지 않는다. 적용 순서는 DD 다음 B9a99d70, 그 다음 이 A-1 검사 보수다. 공유 PR171·기존 두 커밋만 공개한 브랜치는 수정하지 않는다. 새 PR은 만들지 않는다.
