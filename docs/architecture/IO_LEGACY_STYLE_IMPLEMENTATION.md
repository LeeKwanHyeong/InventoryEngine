# LEGACY_STYLE Class·Adapter·Feature Producer 로컬 구현

기준일: 2026-10-15. InventoryEngine `inventory_engine_dev`의 추가 모듈이다.
**순수 입력 생성·분류·중립 Adapter 구현 완료 / 실제 PSI 후보 실행·운영 연결 미구현**으로 구분한다.
Legacy 원본 재현이나 정책 성과 우월성을 뜻하지 않는다.

2026-10-19 후속: [명시적 후보 Catalog·공통 PSI 연결](IO_CANDIDATE_CATALOG_PSI.md)을 완료했다.
아래는 Class/Producer 단계의 구현 경계이며, 현재 실행·검증 범위와 후속 작업은 새 문서를 따른다.

## 1. 현재 구현과 유지한 경계

| 구성 | 책임 |
|---|---|
| `classify_inventory/legacy_rules.py::LegacyStyleRules` | 확정한 개발 Profile Hash 검증. 임의 수정 후 재봉인한 Profile도 V1으로 수용하지 않음 |
| `classify_inventory/legacy_features.py::LegacyFeatureProducer` | 전달받은 26주 이력·물리 Lead Time·단가를 검증하고 참조 ROP Feature Snapshot 생성 |
| `classify_inventory/legacy_style.py::LegacyFactorSegmentation` | 검증된 Feature에서 구간·점수·그룹·후보 묶음과 유효 품목 예외 산출 |
| `LegacyStyleSegmentationAdapter` | 원 ABC·XYZ 등급을 별도 보존하며 공통 `SegmentationResult`로 변환 |
| `LegacyStyleCandidateRangeAdapter` | 검증한 분류 결과에 속하는 배정만 공통 `CandidateRangeBinding`으로 변환 |

기존 ABC Class/Adapter·분류 Snapshot·Run/Config Schema·PSI·FSN/운영 Gate는 수정하지 않았다.
새 모듈을 import하거나 호출해도 기존 실행의 분류/전략을 자동 전환하지 않는다.
Adapter는 후보 **범위**를 반환하며 주문·자동 게시 허가를 반환하지 않는다.
DB 읽기/쓰기·새 테이블·CLI/HTTP Route·운영 Snapshot 승인·Runtime 설치/배포는 없다.

개발 숫자와 원본 근거의 구분은 [개발 규칙 V1](IO_LEGACY_STYLE_DEVELOPMENT_RULES.md)을 따른다.
규칙 Profile Hash는 `a3121dfa885f8b4d2e0df54605789ad841661693b0f386329fe4fa60946ecbae` 그대로다.
규칙 JSON의 Wrapper 상태는 정의 당시 기록이며 실행 상태 Registry가 아니다. 원 Golden/규칙 파일을 수정하지 않았다.

## 2. 입력 생성 계약

입력은 내부 `1.0.0` 문서이며 다음 필드만 받는다. 운영 Config/Run 계약의 Version을 변경하지 않는다.

| 필드 | 필수 의미·검증 |
|---|---|
| `context` | 회사·Subsidiary·Site, 판단 주차, Master 기준일, Cut-off, Calendar ID, Master Revision, 원 Config Hash, 원 분류 ID/Hash, 참조 계산 Build Hash |
| `universe` | 품목 ID·UOM. 비어 있거나 중복이면 거부 |
| `calendar` | `yyyyww + start_date` 26개. ISO 월요일로 검증하고 W0 직전 26주와 정확히 일치해야 함 |
| `abc_result` | 기존 ABC 중립 결과 전체. Method/계약 Version·Config Hash·Universe Coverage 검증, 원 등급·미분류 보존 |
| `sources` | `history`, `lead_time`, `unit_cost`. Dataset 없음은 null로 보존하고 품목별 누락 사유로 처리 |
| `item_exceptions` | Scope·품목·묶음·사유·Source Revision·적용 시작/종료일. 기본 빈 목록 |

Source는 ID·Revision·Scope·판단 주차·근거 종류·`available_at`·생성기 정보·Rows를 가진다.
이력 Source는 EA·`UNCENSORED_DEMAND`·마감된 주차 목록을 추가로 보존한다.
Lead Time Source는 DAY, 단가는 EA당 가격임을 명시한다. 품목별 가격에는 통화와 기준일이 있다.
숫자 문자열은 최대 40자의 일반 십진 표기다. bool·지수 표기·NaN/Infinity·범위 밖 수치는 계산에 사용하지 않는다.

`feature_input_binding(request)`는 Offline 입력 작성 단계에서 Source/Context/Universe/Calendar/ABC/예외의
정확한 Hash를 만든다. 생성기는 **별도로 전달된 `expected_binding`**과 대조하며, 값·Revision·Scope를
바꾼 뒤 기존 Binding을 재사용할 수 없다. 이 Helper는 Source 승인 도구가 아니다.
향후 Runtime은 Claim이 고정한 Binding과 실제 설치 Build를 전달해야 하며, 그 연결은 아직 없다.
참조 Build Hash는 생성기에 전달한 고정값과 대조할 뿐 설치 파일을 탐색해 배포 증명서를 발급하지 않는다.

Source와 품목 Row는 정렬 후 Hash를 계산한다. Source 원 숫자 표기는 보존하므로 `10`과 `10.0` 원문을
서로 다른 Source로 게시했다면 같은 Hash라고 가정하지 않는다. 가격 통화·기준일·생성기 Version도 Hash에 포함된다.
원 분류 ID/Hash는 입력 포인터로 보존한다. 전체 운영 분류 Header의 승인 검증을 새로 대신하는 경로가 아니다.

## 3. 생성·분류·재검증

```python
rules = LegacyStyleRules.from_document(rules_document)
producer = LegacyFeatureProducer(rules, reference_calculator_build_hash=installed_build_hash)
features = producer.produce(request, expected_binding=separately_pinned_binding)
classifier = LegacyFactorSegmentation(rules, reference_calculator_build_hash=installed_build_hash)
detailed = classifier.classify(features)
neutral = LegacyStyleSegmentationAdapter(classifier).segment(
    detailed, configuration_hash=detailed.to_dict()["configuration_hash"]
)
```

- Feature Snapshot은 원 입력·Binding·규칙 Hash·구현 Version·참조 통계/정책/계산 근거·모든 사유를 담는다.
  `LSF-<입력 Binding/규칙/구현 Hash>` ID와 별도 Content Hash를 생성한다.
- 참조 평균·표준편차·ROP는 기존 순수 계산기를 Decimal 정밀도 40으로 재사용한다.
  커버리지의 분자·분모는 추가 정밀도로 보존하고 구간 비교는 정확한 유리수로 수행한다.
  0.5에 극도로 가까운 값도 중간 반올림 때문에 상위 구간으로 이동하지 않는다.
- Class는 Snapshot의 Hash만 확인하지 않는다. 보존된 입력에서 Feature를 다시 계산해 대조한다.
  Adapter도 결과를 다시 대조하므로 Feature/그룹/Version을 바꾸고 새 Hash를 붙인 문서는 거부한다.
- 새 내부 설정 Hash는 `base_configuration_hash + rules_profile_hash + item_exceptions_hash`의 정규화 Hash다.
  원 Config/ABC Hash를 덮어쓰지 않는다. 결과 객체는 불변이며 직렬화 결과를 바꿔도 원 객체는 유지된다.

품목 예외의 적용 시점은 W0 월요일이며 기간은 `[effective_from, effective_to)`다.
동일 품목의 겹치는 예외는 거부하고 맞닿는 기간은 허용한다. 적용해도 원 인자 그룹을 보존한다.
Feature 미분류 품목에는 예외를 적용하지 않으며 ABC 미분류를 운영 적격으로 바꾸지 않는다.
기존 승인 업무 Override의 주문 처리 우선순위는 전혀 수정하지 않는다.

## 4. 검증 범위

- 원 Golden의 그룹 27건·경계 15건·참조 ROP 7건·누락/오류 12건을 실제 Class/Producer에 적용했다.
  경계 Primitive 시험과 봉인 입력→참조 ROP→Class→Adapter 시험을 구분한다.
- 품목 예외의 시작/종료·중복/인접 기간·Scope·고아/누락 품목, 단가 91/92일, 0일/잘못된 Lead Time,
  26주 마감 누락·미래 Source·미확약 근거·단위·미지원 수요 의미를 검증한다.
- Hash·Version·입력 교체·재봉인 변조, 입력 순서/호출자의 Decimal Context 차이·방어적 복사를 검증한다.
- 수작업 수치 기대값을 먼저 통과시킨 뒤 새 Feature/분류/중립 결과/후보 범위의 지문을 테스트에 고정했다.
  기존 ABC/PSI 기대 Hash를 새 결과로 재생성하지 않았다.
- Gate Golden의 BLOCK/REVIEW는 기존 실제 정책 탐색 경로에서 전후 결과 Hash·주문/게시 제한과
  Baseline 보존을 확인한다. 기존 미분류 3품목의 합성 0이력 사례도 미분류·원 사유로 보존한다.

최종 건수·정적 검사와 선행 파일 무변경 대사는 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)에 기록한다.
실제 DB·ERP Source 검증, 성과 비교, 운영 적격 승인 또는 대량 처리 성능 시험은 아니다.

## 5. 다음 작업

**후속 완료 — 명시적 Offline 후보 Catalog와 공통 PSI 연결 · InventoryEngine**

- Adapter는 범위를 반환하며 후속 별도 Catalog가 그룹을 AX/CZ로 위장하지 않고 32개 요청 후보와
  정확한 88개 합집합을 공통 Guard/PSI로 전달한다. 기존 Runtime/입장 Gate는 유지했다.
- 범위 Adapter의 호출별 대사는 유지하고, 새 Catalog 실행은 한 번 검증한 전체 묶음을 품목별로 조회한다.
- 현재 다음 작업은 중립 후보 결과의 선정·평가 연결이다. 그 후 새 미사용 기간의 같은 Source·비용·
  서비스/종료조건으로 ABC·XYZ/LEGACY_STYLE/넓은 참조를 비교한다. 두 단계는 직렬이다.

Legacy 원본 자료 확보는 별도 병행 가능하다. DB 적용·운영 연결·배포·Commit/Push는 수행하지 않았다.
`oma-backend`의 입력/계산/변환 책임 분리와 `oma-qa`의 오류·변조·호환 회귀 검증을 적용했다.
