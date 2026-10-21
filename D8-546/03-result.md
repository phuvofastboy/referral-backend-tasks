# D8-546 — Kết quả implement (2026-10-21, branch `tasks/D8-546/add-log-permission`)

## Thay đổi

| File | Nội dung |
| --- | --- |
| `app/migrations/Version20261021000000.php` | `ADD object_uuid UUID` + backfill cho `referral_order` / `referral_order_document`; `down()` drop cột |
| `app/src/Entity/Log/Log.php` | cột `objectUuid`; `setObjectId()` set luôn `objectUuid` khi là UUID hợp lệ; getter |
| `app/hasura/metadata/sources/default/tables/public_log.yaml` | 2 manual relationship + filter `ROLE_USER` 3 nhánh (export từ Hasura local) |
| `app/tests/MessageHandler/ReferralOrder/LogMessageObjectUuidDbTest.php` | log document có `object_uuid` và `referral_id` null; object_id không phải UUID → `object_uuid` null |
| `docs/prod-audit-2026-09-02.md` §6.3 | ghi chú cập nhật D8-546 |

## Verify local

- Backfill: 871/871 `referral_order`, 1111/1111 `referral_order_document`; `referral` không đụng tới.
- Migration down → up chạy được, backfill chạy lại ra đúng 1982 row.
- `hasura:metadata:get-inconsistent`: consistent.
- Hasura `ROLE_USER` (dùng admin secret + `x-hasura-role` / `x-hasura-user-id` để giả lập user):

| Case | Kỳ vọng | Thực tế |
| --- | --- | --- |
| order log, user tạo order | 17 | 17 |
| document log, user tạo order | 12 | 12 |
| document của user khác | 0 | 0 |
| order / document, user không liên quan | 0 | 0 |
| document, cấp trên, order `draft` | 0 | 0 |
| document, cấp trên, order `paid` | 2 | 2 |
| referral log, chủ referral (nhánh cũ) | 1178 | 1178 |
| referral log, user không liên quan | 0 | 0 |
| select `object_uuid` | lỗi field not found | lỗi field not found |

- `bin/ci-checks`: PHPStan `[OK]`. PHPUnit 671 test, 3 failure: `BackfillMissingTrialDbTest` ×2 và `LazyRootFieldsTest`. Cả 3 **cũng đỏ trên `develop` sau khi stash thay đổi** của task này, nên là lỗi sẵn của môi trường local, không do D8-546.
- `lint:container --resolve-env-vars`: exit 0. `check-generated-docs.sh`: in sync (ERD không đổi).

## Còn lại

- Commit + PR (1 PR). Trước khi merge: kiểm thứ tự migrate job và `hasura:metadata:apply` trên ArgoCD dev.
- Sau deploy dev:
  - `hasura:metadata:get-inconsistent`.
  - Chạy lại câu UPDATE backfill một lần, cho log do worker cũ ghi trong lúc rollout.
  - Gọi lại `GetLogs` với `object_id 01a12f69-a29e-7da6-8adb-fc8f73663d58`, kỳ vọng 2 record.
