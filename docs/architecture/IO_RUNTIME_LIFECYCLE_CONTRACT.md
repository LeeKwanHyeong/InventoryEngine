# InventoryEngine Runtime Lifecycle Contract

기준일: 2026-09-08
계약 버전: `inventory-engine-execution-request-v1 / 1.0.0`

## 목적과 소유권

Platform은 권한과 Project Scope를 검증하고 Planning Cycle의 단일 Site Attempt를 원자적으로
Claim한다. Claim Transaction에서 생성한 `engine_run_id`, `attempt_no`, `site_row_version`과
봉인된 입력 Binding을 InventoryEngine Runtime에 전달한다.

InventoryEngine Runtime은 Run ID를 다시 만들거나 최신 Forecast·Master를 탐색하지 않는다.
전달받은 Claim을 검증해 영속 실행 대기열에 접수하고, Worker가 계산 단계 Event와 최종
Publication을 Platform API로 반환한다.

```text
Engine Studio
  -> Platform Claim UoW
  -> inventory-engine-execution-request-v1
  -> Inventory Runtime admission
  -> durable submission/worker
  -> running stage events
  -> one terminal succeeded/failed event
  -> publish result snapshot with latest site_row_version CAS
```

## 실행 요청

요청은 다음 값을 포함하는 닫힌 계약이다.

- Platform이 확정한 `engine_run_id`, `attempt_no`, `site_row_version`
- Tenant·Project·Planning Cycle·Site Execution 식별자
- 단일 Company/Subsidiary/Plant/Site Scope
- Configuration ID·Revision·Hash
- Demand Forecast, Inventory Position, Inventory Policy, Calendar, Master Binding
- 선택적으로 승인된 Inventory Network Binding
- Request·Correlation·Idempotency 식별자와 Hash

Inventory Policy Binding의 Snapshot ID/Hash는 Configuration Revision/Hash와 정확히 같아야
한다. 필수 Binding 누락, 중복, 잘못된 UUID/Hash, 알 수 없는 필드, 1MB 초과 요청은 접수 전에
거부한다. 같은 의미의 Binding 순서는 Canonical Hash에 영향을 주지 않는다.

## Event와 Publication

- Worker 접수와 입력 준비, PSI, 보충 계산 등 중간 Event는 `running`만 사용한다.
- 첫 `succeeded` Event가 Platform Run을 Terminal 상태로 바꾸므로 결과 Artifact가 봉인된
  마지막 단계에서 한 번만 보낸다.
- 실패 시 안정된 오류 Code와 비민감 Payload만 `failed` Event에 기록한다.
- 매 Event Receipt의 최신 `site_row_version`을 다음 CAS 기준으로 사용한다.
- 성공 Event 뒤 Result Snapshot ID/Hash를 Publish하고 `effective_run_id`가 현재 Run인지
  검증한다. Publish 전에는 Site의 유효 결과로 노출하지 않는다.

## 통신과 보안

- Platform -> Runtime Endpoint는 배포 설정의 고정 URL만 사용한다.
- Runtime -> Platform Callback도 배포 설정의 고정 Base URL과 고정 Lifecycle Path만 사용한다.
- Production 양방향 통신은 HTTPS와 Bearer Token을 필수로 한다.
- Platform Callback Token은 `engine_run.execute` Project 권한을 가진 Service Principal의
  Access Token이어야 한다.
- Callback URL, Token, 원본 민감 데이터는 실행 요청이나 Event Payload에 싣지 않는다.

## 장애와 재시도

Platform Claim은 Runtime Dispatch 전에 Commit된다. Dispatch 실패 시 API는 `503`으로
닫히지만 Claim을 삭제하거나 새 Run으로 바꾸지 않는다. 같은 Idempotency 요청으로 재호출하면
동일 Claim/Run을 재사용해 Dispatch를 재시도한다.

Runtime 접수 Adapter는 `engine_run_id` 기준 멱등성과 내구성을 제공해야 한다. 같은 Request의
재접수는 `replayed=true`, 다른 Payload의 같은 Run ID는 `409`가 되어야 한다. Worker나 Callback
실패 후 재개할 때도 이미 기록된 Event Receipt와 Row Version을 대사해 중복 계산 또는 잘못된
Effective Run 승격을 막는다.

## 현재 구현과 경계

구현 완료:

- 닫힌 Runtime Request/Receipt 검증
- Platform HTTP Dispatch Transport와 Production HTTPS/Auth Guard
- Runtime FastAPI 접수 Endpoint와 고정 Platform Event/Publish Callback Client
- 단계 Event, Terminal Event, Publish CAS Worker Orchestration
- 양쪽 실제 계약을 연결한 ASGI Mock/Offline 통합 테스트

아직 구현하지 않음:

- Runtime 영속 Queue/Worker Adapter
- 실제 `prepare_inventory -> PSI -> recommend -> artifact seal` Handler 조립
- Migration 074 개발 DB 적용과 실제 Lifecycle DB Write E2E
- Runtime 배포, Service Principal 발급·Secret 주입, Scheduler 활성화

후자의 DB 변경과 배포는 별도 승인 대상이다.
