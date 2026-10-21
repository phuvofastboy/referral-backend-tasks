# Plan: cho `ROLE_USER` đọc log của order / document qua manual relationship

## Context

Từ commit `20be6221` (2026-09-02), filter `ROLE_USER` trong `public_log.yaml` chỉ đi qua relationship `referral` (`log.referral_id`). Log có `object_type = referral_order | referral_order_document` do `LogMessageHandler` ghi, và luôn có `referral_id = NULL`. Vì vậy query `GetLogs` lọc theo `object_id` của order/document trả về rỗng, dù DB có row.

Số liệu local: 871 row `referral_order` + 1111 row `referral_order_document`, tất cả `referral_id` null. Commit trên ghi prod chỉ có 247 row như vậy.

Mục tiêu: user thấy log của order/document mà họ đã có quyền xem ở bảng gốc (mirror đúng filter `ROLE_USER` của `referral_order` / `referral_order_document`), không mở lại lỗ `filter: {}`.

**Chặn kỹ thuật đã xác nhận:** `log.object_id` là `VARCHAR(512)`, còn PK của order/document là `UUID`. Join trực tiếp lỗi `operator does not exist: character varying = uuid`. Đã chốt hướng: thêm cột `log.object_uuid UUID NULL` để map relationship.

## Các bước

### 1. Migration: thêm cột + backfill

File mới `app/migrations/Version2026102XXXXXXX.php`, viết tay, không dùng `migrations:diff` (cùng lý do drift ORM3/DBAL4 đã ghi ở `Version20260811040000`).
- `up()`:
  - `ALTER TABLE log ADD object_uuid UUID DEFAULT NULL`. Cột nullable không default nên chỉ đổi metadata, không rewrite bảng.
  - Backfill: `UPDATE log SET object_uuid = object_id::uuid WHERE object_uuid IS NULL AND object_type IN ('referral_order','referral_order_document') AND object_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'`. Chạy một lệnh là đủ vì prod chỉ khoảng vài trăm row. Lệnh idempotent.
- `down()`: `ALTER TABLE log DROP object_uuid`.
- **Không thêm index.** Permission filter đi theo chiều log → order/document, tức lookup theo PK `id` của bảng đích, vốn đã có index. Còn `log` thì FE đã lọc qua `idx_log_object_id`.

### 2. Entity + nơi ghi

`app/src/Entity/Log/Log.php`:
- Thêm `#[ORM\Column(type: 'uuid', nullable: true)] private ?Uuid $objectUuid = null;` kèm getter. `Symfony\Component\Uid\Uuid` đã được import.
- Trong `setObjectId()`, set luôn `objectUuid = Uuid::isValid($objectId) ? Uuid::fromString($objectId) : null`. Như vậy chỉ có một điểm ghi duy nhất. `LogFactory::factory()` và `LogMessageHandler` không cần sửa vì đều đi qua `setObjectId()`.

### 3. Hasura metadata (làm qua Console/API rồi export; hook chặn sửa yml trực tiếp)

Trên Hasura local (`http://localhost:8080`), sau khi đã `doctrine:migrations:migrate` và reload metadata để Hasura thấy cột mới:

a. **Không** thêm `object_uuid` vào `columns` của `ROLE_USER`. Cột chỉ dùng nội bộ cho relationship; FE vẫn lọc bằng `object_id`, schema FE không đổi.

b. Thêm 2 object relationship (manual) trên bảng `log`:
```yaml
- name: log_referral_order
  using:
    manual_configuration:
      column_mapping: { object_uuid: id }
      insertion_order: ~
      remote_table: { name: referral_order, schema: public }
- name: log_referral_order_document
  using:
    manual_configuration:
      column_mapping: { object_uuid: id }
      insertion_order: ~
      remote_table: { name: referral_order_document, schema: public }
```

c. Sửa `filter` của `ROLE_USER` trên `log` thành `_or` gồm 3 nhánh. Lưu ý: Hasura **không** tự áp permission của bảng đích khi traverse relationship, nên phải chép lại filter sở hữu của order:
```yaml
filter:
  _or:
    - referral:                       # nhánh cũ, giữ nguyên
        _or:
          - user_account_id: { _eq: X-Hasura-User-Id }
          - user: { parent_id: { _eq: X-Hasura-User-Id } }
    - _and:
        - object_type: { _eq: referral_order }
        - log_referral_order: &order_owner
            _or:
              - created_by_id: { _eq: X-Hasura-User-Id }
              - _and:
                  - created_by_user: { parent_id: { _eq: X-Hasura-User-Id } }
                  - status: { _neq: draft }
              - owner_id: { _eq: X-Hasura-User-Id }
    - _and:
        - object_type: { _eq: referral_order_document }
        - log_referral_order_document:
            referral_order: *order_owner   # array rel → "tồn tại order thoả"
```
- Nguồn để chép filter: `public_referral_order.yaml` (ROLE_USER) và `public_referral_order_document.yaml` (ROLE_USER). Đổi một chỗ thì phải đổi cả chỗ kia.
- Điều kiện `object_type` để chặn nhầm loại, phòng trường hợp sau này có loại log khác cũng mang UUID trỏ trùng.
- Làm bằng Console (Data → log → Relationships / Permissions), hoặc gọi metadata API `pg_create_object_relationship` + `pg_drop_select_permission`/`pg_create_select_permission` bằng curl với admin secret.

d. `php bin/console hasura:metadata:export` → commit `app/hasura/metadata/sources/default/tables/public_log.yaml`. Export sẽ xoá comment trong yml, nên lý do thay đổi phải ghi ở doc (bước 5). Chạy `hasura:metadata:get-inconsistent`, kết quả phải rỗng.

### 4. Test

- DbTest mới theo pattern `tests/MessageHandler/Referral/LogReferralEmptyChangeDbTest.php`: gọi `LogMessageHandler` với `LogMessage(true, $data, objectType: Log::LOG_REFERRAL_ORDER_DOCUMENT)`, rồi assert `log.object_uuid` = id của document và `referral_id` null. Thêm một case `object_id` không phải UUID → `object_uuid` null.
- `./scripts/test.sh`, baseline phải xanh toàn bộ. Chạy thêm `bin/ci-checks` (PHPStan + schema).

### 5. Doc / catalog

- `python3 scripts/extract_erd.py > docs/erd.md` (entity đổi), rồi `bash scripts/check-generated-docs.sh`.
- Thêm một đoạn vào `docs/prod-audit-2026-09-02.md` §6: filter `log` giờ có thêm 2 nhánh order/document qua `object_uuid`, kèm lý do không join được bằng `object_id`.
- Sửa comment `// Hasura expose log ... filter rỗng` trong `Log.php`, vì comment này đã lỗi thời.

## Thứ tự deploy / rủi ro

- Làm **1 PR** (chốt ở Q&A). Migration phải chạy **trước** khi Hasura apply metadata có relationship tới `object_uuid`, nếu không sẽ có metadata inconsistent. Trước khi merge phải kiểm thứ tự migrate job và metadata apply trên ArgoCD dev. Sau deploy chạy `hasura:metadata:get-inconsistent`. Nếu inconsistent thì chỉ cần `hasura:metadata:apply` lại sau khi migrate xong, vì cột mới nullable nên app cũ vẫn chạy bình thường.
- Pod cũ (worker) còn chạy trong lúc rollout có thể ghi log mới mà `object_uuid` null. Sau khi rollout xong, chạy lại câu backfill ở bước 1 một lần. Câu này idempotent và rẻ.
- Phạm vi quyền chỉ **mở rộng** đúng bằng quyền user đã có ở bảng order/document, không lộ thêm dữ liệu. Vẫn nên cho agent `hasura-permission-reviewer` soát diff yml.

## Verify end-to-end

1. Local: `docker compose exec fpm php bin/console doctrine:migrations:migrate`, sau đó `dbal:run-sql "SELECT object_type, count(*), count(object_uuid) FROM log GROUP BY 1"`. Với 2 loại order/document thì `count(object_uuid)` phải bằng `count(*)`.
2. Lấy một document có log, cùng `created_by_id` của order chứa nó. Tạo JWT cho user đó (skill `local-test-graphql-api`) rồi gọi `GetLogs` qua `https://localhost/api/graphql` với header `x-hasura-role: ROLE_USER` → phải ra đúng số row của `SELECT ... WHERE object_id = '<doc id>'`.
3. Gọi lại với JWT của user không liên quan → `log: []`, `count: 0`.
4. Log `object_type = referral` vẫn đọc được như trước, để chắc nhánh cũ không vỡ.
5. Sau deploy dev: chạy lại đúng curl `GetLogs` user đã gửi, với `object_id 01a12f69-…` → phải ra 2 record.

## Q&A (đã chốt 2026-10-21)

| # | Câu hỏi | Quyết định | Lý do |
| --- | --- | --- | --- |
| 1 | `object_id` (VARCHAR) không join được với PK UUID. Xử lý thế nào? | Thêm cột `log.object_uuid UUID NULL` | Không rewrite / lock bảng, giữ nguyên `object_id` nên FE không đổi. Không chọn `ALTER COLUMN object_id TYPE uuid` vì rewrite cả bảng + ACCESS EXCLUSIVE lock, và kiểu GraphQL đổi sang `uuid`. |
| 2 | Ai được xem log order/document? | Mirror đúng filter `ROLE_USER` của `referral_order`: người tạo, `owner_id`, hoặc cấp trên (`created_by_user.parent_id`) khi order ≠ draft | Không lộ thêm dữ liệu so với bảng gốc. |
| 3 | Mấy PR? | 1 PR (migration + entity + metadata) | Xem rủi ro thứ tự deploy ở trên. |
| 4 | Expose `object_uuid` cho `ROLE_USER`? | Không | Chỉ dùng nội bộ cho relationship. |
| 5 | Xác nhận trên DB dev trước khi làm? | Không cần | Kết luận từ code + DB local đã đủ (log order/document local 100% `referral_id` null). Bước verify 5 sau deploy dev sẽ kiểm lại. |
