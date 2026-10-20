# serving-runtime
BoB serving-engine security runtime solution

- 개발계획서 v1.4(문서 상태: 개발 기준 설계안)가 전달됐으며, 직접 개발할 모듈의 언어는 Python으로 선택했습니다. 연동 규격·실제 환경 등은 미결입니다.
- Codex는 구현·수정·시험을, Claude는 검수를 담당합니다.

## 협업·검수

사용자 요청을 Claude가 정리하고 Codex가 구현·시험합니다. Codex는 작업을 마치면 변경 범위·새 파일·시험 명령과 결과·남은 제약·Git 기준점·커밋 여부를 인계하고, Claude가 대화형 세션에서 직접 검수합니다. 필요한 수정·재시험은 Codex가 맡고 Claude가 다시 검수해 최종 보고합니다.

자동 검수 스크립트는 기본 작업 흐름에서 사용하지 않습니다.
