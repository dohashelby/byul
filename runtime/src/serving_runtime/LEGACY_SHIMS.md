# 옛 import 경로 껍데기 (임시)

platform·update 시험이 옛 경로(`serving_runtime.mvp.*`, `serving_runtime.bundle_tools`, `serving_runtime.file_info`)를
쓰는 동안만 둔다. 각 파일은 새 모듈을 가리킬 뿐 로직이 없다.

삭제 방법: `src/serving_runtime/mvp/` 폴더, `src/serving_runtime/bundle_tools.py`,
`src/serving_runtime/file_info.py`, `tests/test_legacy_import_shim.py`, 이 파일을 지운다.
그 전에 platform·update 시험의 import 줄을 새 경로로 바꾼다. runtime 코드는 껍데기를 쓰지 않는다.
