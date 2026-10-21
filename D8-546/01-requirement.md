# D8-546 — `GetLogs` trả rỗng cho log của order / document

## Hiện tượng

Trên dev, FE gọi `GetLogs` qua `https://resell.dev-fastboy.dev/api/graphql` (Hasura), header `x-hasura-role: ROLE_USER`:

```graphql
log(where: {object_id: {_eq: "01a12f69-a29e-7da6-8adb-fc8f73663d58"}}, order_by: {created_at: desc}) { ... }
log_aggregate(where: ...) { aggregate { count } }
```

DB dev có 2 record với `object_id` đó, nhưng API trả `log: []` và `count: 0`.

## Nguyên nhân (đọc từ code local)

1. `app/hasura/metadata/sources/default/tables/public_log.yaml`: permission `ROLE_USER` chỉ cho đọc row nào có relationship `referral` (qua `log.referral_id`) thuộc về user (`user_account_id` hoặc `user.parent_id`). Filter này được thêm ở commit `20be6221` (2026-09-02, `docs/prod-audit-2026-09-02.md` §6). Trước commit đó là `filter: {}`.
2. Log của order và document do `LogMessageHandler` ghi (`app/src/MessageHandler/ReferralOrder/LogMessageHandler.php`). Handler chỉ gán `referral` khi `objectType === Log::LOG_REFERRAL`, nên log `referral_order` / `referral_order_document` luôn có `referral_id = NULL`. `object_id` của chúng là id order/document.
3. Hệ quả: filter loại toàn bộ log order/document, và Hasura không báo lỗi.

Số liệu DB local:

| object_type | rows | có referral_id |
| --- | --- | --- |
| referral | 4289 | 4289 |
| referral_order | 871 | 0 |
| referral_order_document | 1111 | 0 |

**Chưa xác nhận trên dev:** chưa chạy `SELECT object_type, referral_id FROM log WHERE object_id = '01a12f69-…'`.

## Yêu cầu

User thấy log của order/document mà họ đã có quyền xem ở bảng gốc. Quyền phải mirror đúng filter `ROLE_USER` của `referral_order` / `referral_order_document`. Không quay lại `filter: {}`.
