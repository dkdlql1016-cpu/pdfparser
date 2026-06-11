# 아키텍처 인수인계 가이드 (KOR)

원문(Source of truth)은 `ARCHITECTURE.md`이며, 이 문서는 번역본입니다.

## 용어 기준

이 문서에서는 아래 용어를 고정해서 사용합니다.
- **run**: 문서 버전 1회 처리 단위
- **single-run / diff-run**: 단일 보고서 처리 / 이전-현재 비교 처리
- **semantic map**: diff의 `equal` 세그먼트에서 나온 old/new 단어 ID 대응 맵
- **section index**: `section_id`, 제목, 범위 메타데이터를 가진 구조화 섹션 카탈로그
- **viewer payload**: UI 렌더링용 집계 데이터(`viewer.json` 중심)
- **AI 호출 루프(AI call loop)**: `ai_callers.py`의 모델/툴 반복 실행 루프

## 1) 실제 구동 순서 (파일명 매칭)

아래는 사용자가 PDF를 올리고, diff/리뷰/AI 평가까지 가는 실제 실행 흐름입니다.

1. **문서 업로드 API 호출**
  - `routes/document_routes.py`의 `/api/documents`, `/api/documents/<workspace_id>/runs`
   - 조립 진입점은 `app.py` (`register_blueprints`, deps 주입)

2. **PDF -> Markdown 변환 (`opendataloader-pdf` 라이브러리 사용)**
   - 실행 위치: `document_io_service.py`의 `run_opendataloader_to_markdown()`
   - 파이프라인 오케스트레이션: `document_pipeline_service.py`

3. **Markdown diff 생성 (텍스트 레벨 diff)**
   - 실행 위치: `document_io_service.py`의 `run_diff_extract()`
   - 실제 diff 엔진 스크립트: `document_diff_extract.py`
   - 산출물: markdown 텍스트 비교 결과인 `diff/segments.json` 세그먼트(`equal`, `delete`, `add`)
   - 주의: 이 단계는 **PDF 좌표 매핑 전** 단계입니다. 먼저 텍스트 diff를 만듭니다.

4. **텍스트 diff 세그먼트를 PDF 단어 JSON 좌표에 매핑**
   - `document_diff_analysis_service.py`
   - 하이라이트/변경점 계산, move suppression, 정렬 리포트 생성
   - 매핑 대상은 `current/words.json` / `previous/words.json` (PDF 단어를 JSON으로 추출한 결과)입니다.
   - 즉, 3번 텍스트 diff를 PDF 단어 인덱스/바운딩박스/페이지 정보에 연결하는 단계입니다.

5. **semantic map + section index + viewer payload 산출**
   - semantic: `document_semantic.py`
   - 인덱스: `document_index_service.py`
  - 결과는 `documents/workspaces/<workspace_id>/runs/<run_id>/compare/viewer.json` 등으로 저장
   - `semantic_map`: diff의 `equal` 세그먼트에 속한 old/new 단어 대응 관계(`old_word_id` <-> `new_word_id`)와 line/segment 메타데이터
   - `section index`: section_id, 제목, 시작/끝 범위 메타데이터를 가진 구조화된 섹션 카탈로그(리뷰/AI 컨텍스트에서 활용)

6. **리뷰 생성/수정/조회**
   - HTTP: `routes/document_routes.py` (`/reviews` 계열)
   - 앵커 투영/리뷰 매핑: `document_review_projection_service.py`

7. **AI 평가 실행**
   - HTTP: `routes/assessment_routes.py` (`/assess`, `/change-assess`)
   - 오케스트레이션: `assessment_service.py`
   - AI 호출 루프: `ai_callers.py`
   - 툴 스키마: `ai_runtime_utils.py`
   - 툴 실제 로직: `section_context_utils.py`

8. **스냅샷/저장/내보내기**
   - 스냅샷: `document_snapshot_service.py`, `routes/snapshot_routes.py`
   - PDF export: `document_export_service.py`

---

## 2) 상위 도메인별 설명

## 2-1. `document_` 계열

### 공통 설명
문서 업로드부터 변환, diff, 리뷰 앵커 처리, 스냅샷까지 **문서 중심 워크플로우**를 담당합니다.

### 하위 파일
- `document_io_service.py`
  - PDF 단어 추출/페이지 렌더링, OpenDataLoader 변환, diff 스크립트 실행
- `document_pipeline_service.py`
  - single-run / diff-run 전체 파이프라인 오케스트레이션
- `document_diff_extract.py`
  - markdown 기반 세그먼트 diff 생성기(실행 스크립트)
- `document_diff_analysis_service.py`
  - 세그먼트-PDF 정렬, 하이라이트/변경점 계산
- `document_semantic.py`
  - diff의 `equal` 세그먼트 기준 semantic map 및 run 리뷰 저장 유틸
- `document_index_service.py`
  - markdown/PDF section index 생성 및 마커 주입
- `document_review_projection_service.py`
  - 리뷰 앵커 생성/투영, run 간 리뷰 마이그레이션
- `document_review_migration_service.py`
  - 이전 리뷰 carry-forward, old/new 앵커 remap 보조
- `document_snapshot_service.py`
  - 스냅샷 생성/카운트/보관 개수 정리
- `document_export_service.py`
  - 리뷰 기반 주석 PDF export
- `document_assessment_context_service.py`
  - AI 평가용 컨텍스트/그룹 구성(문서 쪽 컨텍스트 계산)

## 2-2. `assessment_` + `ai_` 계열

### 공통 설명
리뷰/변경점을 AI로 평가하고, verdict/근거를 구조화해서 저장하는 **AI 평가 계층**입니다.

### 하위 파일
- `assessment_service.py`
  - 평가 실행 전체 오케스트레이션(대상 선정, 저장, 상태 갱신)
- `assessment_prompt_builders.py`
  - 모델 입력 프롬프트 생성(단건/배치)
- `prompts/ai_assessment_system.md`
  - 리뷰 단위 AI 평가 동작/정책을 정의하는 시스템 프롬프트
- `prompts/change_ai_assessment_system.md`
  - 변경 단위 리스크 평가 동작/정책을 정의하는 시스템 프롬프트
- `assessment_normalizers.py`
  - 모델 출력 형식 정규화/정책 적용
- `ai_callers.py`
  - Anthropic/OpenAI 호출 + AI 호출 루프(툴 호출 포함) 실행
- `ai_runtime_utils.py`
  - 툴 스키마 정의 및 공통 runtime helper
- `ai_config_utils.py`
  - 모델/키 설정 로딩, 키 체크 보조
- `prompts/ai_assessment_system_Kor.md`
  - 리뷰 단위 시스템 프롬프트의 한국어 번역본
- `prompts/change_ai_assessment_system_Kor.md`
  - 변경 단위 시스템 프롬프트의 한국어 번역본

### AI가 실제로 호출하는 tool 상세
- `read_section(file, section_id)`
  - 목적: 특정 구조 섹션 본문을 정확히 읽기
  - 사용 시점: 섹션 ID를 이미 알고, 해당 구역 원문 근거를 확인할 때
  - 반환: `file(previous/current)` + `section_id`의 본문 텍스트
- `search_markdown(file, query)`
  - 목적: 정확한 위치가 불명확할 때 문맥 탐색
  - 사용 시점: 유사 문장/주변 문맥 근거가 필요할 때
  - 반환: 히트 라인 주변 컨텍스트(`line`, `context`)
- `keyword_search_markdown(file, keyword, case_sensitive?, whole_word?, max_hits?)`
  - 목적: 키워드 전수 점검(총 개수 + 위치)
  - 사용 시점: 전역 치환 확인 등, 전체 개수와 위치 정보가 필요한 경우
  - 반환: `total_count`, `matches`(line/column/section), `section_counts`, `truncated`
- `submit_verdict(...)`
  - 목적: 리뷰 단건 평가 최종 제출
- `submit_verdicts(items=[...])`
  - 목적: 리뷰 배치 평가 최종 제출
- `submit_change_review(...)`
  - 목적: 변경 단건 리스크 평가 최종 제출
- `submit_change_reviews(items=[...])`
  - 목적: 변경 배치 리스크 평가 최종 제출

## 2-3. `routes` 계열 (HTTP 계층)

### 공통 설명
요청 파싱/검증/응답 포맷 담당. 비즈니스 로직은 서비스로 위임합니다.

### 하위 파일
- `routes/document_routes.py`: 문서/런/리뷰/저장/export API
- `routes/assessment_routes.py`: AI 평가/forward/migrate API
- `routes/snapshot_routes.py`: 스냅샷 API
- `routes/file_manager_routes.py`: 파일 매니저 API

## 2-4. `*_utils` 계열

### 공통 설명
특정 도메인 규칙 없이 여러 계층에서 재사용하는 공용 유틸입니다.

### 하위 파일
- `storage_utils.py`
  - 경로 계산(`document_run_dir` 등) + JSON read/write + UTC 시각
- `text_utils.py`
  - 토큰/공백/문서 제목 정규화, 프롬프트 길이 trim
- `section_context_utils.py`
  - markdown 섹션 추출/탐색 + AI tool 구현

---

## 3) 엔트리포인트와 조립

- `app.py`
  - Flask 엔트리포인트
  - 블루프린트 등록
  - 각 블루프린트에 deps 주입(컴포지션 루트)

---

## 4) 데이터 저장 구조

- `documents/workspaces/<workspace_id>/file_manager/meta.json`
  - 문서 메타데이터, 제목, 저장 상태, run 목록(`runs[]`)
- `documents/workspaces/<workspace_id>/file_manager/reviews.json`
  - 문서 레벨의 canonical 리뷰 스레드/앵커
- `documents/workspaces/<workspace_id>/runs/<run_id>/compare/viewer.json`
  - UI용 파생 번들(하이라이트, 변경점, semantic map, section 복사본, alignment report)
- `documents/workspaces/<workspace_id>/runs/<run_id>/compare/current/`
  - 현재본 원본 산출물: `source.pdf`, `source.md`, `words.json`, `sections.json`
- `documents/workspaces/<workspace_id>/runs/<run_id>/compare/previous/`
  - diff run의 이전본 원본 산출물: `source.pdf`, `source.md`, `words.json`, `sections.json`
- `documents/workspaces/<workspace_id>/runs/<run_id>/compare/diff/segments.json`
  - PDF 좌표가 없는 raw markdown diff 세그먼트(`equal/delete/add`)
- `documents/workspaces/<workspace_id>/runs/<run_id>/analysis/review_assessment.json`
  - 리뷰 단위 AI 평가 결과/메타
- `documents/workspaces/<workspace_id>/runs/<run_id>/analysis/change_assessment.json`
  - 변경 단위 AI 평가 결과/메타
- `documents/workspaces/<workspace_id>/runs/<run_id>/_cache/`
  - 버릴 수 있는 중간 산출물(`opendataloader/`, 마커 제거 `diff_md/`)
- `documents/workspaces/<workspace_id>/snapshots/<snapshot_id>/snapshot.json`
  - 스냅샷 메타데이터(카운트/모드/원본 run)
- `documents/workspaces/<workspace_id>/snapshots/<snapshot_id>/...`
  - `_cache/`를 제외한 durable run 산출물을 같은 구조로 고정 복사
- `RUN_LAYOUT.md`
  - 산출물 의미, 필수 여부, source of truth 규칙을 설명하는 표준 문서

### chars API 런타임 계약

- `/chars/<side>`는 서버가 제공하는 페이지 범위 기반 API입니다. (`page`, `page_start`, `page_end`)
- 응답은 `current/words.json` 또는 `previous/words.json`에서 요청 시점에 생성됩니다.
- 크기 가드는 다음 환경변수로 제어됩니다.
  - `CHARS_MAX_WORDS` (기본값 `50000`)
  - `CHARS_MAX_ESTIMATED_COUNT` (기본값 `250000`)
- 임계치 초과 시 `413`을 반환합니다.
- 상세 계약은 `CHARS_API_CONTRACT.md`를 참고하세요.

---
