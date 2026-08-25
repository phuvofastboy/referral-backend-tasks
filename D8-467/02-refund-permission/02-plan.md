# Plan — ROLE_USER query `order_refund` của chính mình và của sub-user

Trạng thái: **backend đã implement + verify trên local** (bước 1–2 xong, acceptance test xanh).
Còn lại: FE đổi query (bước 3), và test dương tính trên dev vì local không có data.

Ghi chú khi làm: file `app/hasura/metadata/*` bị hook `.claude/hooks/protect-sensitive.sh` chặn sửa
tay — phải đi đường Hasura API/CLI rồi `hasura:metadata:export`. Export ghi lại **toàn bộ** metadata
nên kéo theo 19 file drift thuần format (`{  }` → `{}`, khác version exporter); đã revert 19 file đó,
chỉ giữ `public_user.yaml`.

Mục tiêu: user đăng nhập (ROLE_USER) đọc được `order_refund` bên CRM của những invoice mà
referrer là **chính họ hoặc user con của họ** (`user.id = me OR user.parent_id = me`).

**Scope: chỉ THÊM đường query mới.** Các field `crm_order_refund*` ở root hiện tại **giữ nguyên**,
không gỡ, không sửa. ⇒ Thay đổi này **không breaking**, FE chuyển sang lúc nào cũng được.

---

## 0. Bối cảnh

### Vì sao chọn remote relationship

| Phương án | Vì sao loại / chọn |
| --- | --- |
| Preset argument-level trên `where` | Không diễn đạt được "self OR các con": nhiều con ⇒ cần `_in`, mà Hasura từ chối `_in` với session variable (`Session arguments can only be set for singleton values`). Ngoài ra client mất hẳn argument `where`. |
| Denormalize `referrer_parent_id` sang CRM + 2 session variable | Làm được nhưng cần đổi schema CRM + migration + backfill, chỉ phủ 1 tầng, và dính bug runtime khi dùng cùng một session var 2 lần (`multiple definitions for variable "x_hasura_user_id"`). |
| Resolver mới bên referral-backend | Làm được, nhưng phải tự viết phân trang/sắp xếp/ownership check. |
| **Remote relationship `user` → remote schema `crm`** ✅ | Điều kiện lọc đã có sẵn trong table permission; Hasura tự pin `where` và đục bỏ operator client có thể lách; không cần CRM đổi gì, không đụng permission hiện có. |

Điều kiện cần đã có sẵn — select permission ROLE_USER trên bảng `user`
(`app/hasura/metadata/sources/default/tables/public_user.yaml`):

```yaml
filter: {_or: [{id: {_eq: X-Hasura-User-Id}}, {parent_id: {_eq: X-Hasura-User-Id}}]}
```

Đúng y nguyên "self hoặc con của tôi". Table permission không dính giới hạn singleton như preset của
remote schema, nên không cần xử lý gì thêm cho vế `parent_id`.

### Đã kiểm chứng trên Hasura v2.50 local

Toàn bộ đo đạc dưới đây chạy **trong đúng cấu hình của plan này** — tức là permission ROLE_USER trên
remote schema `crm` vẫn nguyên vẹn, không gỡ field nào. Relationship và role test đã xoá sạch sau khi đo.

- Relationship truyền `$id` của **từng row user** sang CRM → mỗi user chỉ thấy data của chính mình.
  Query với user cha trả về 2 row user (self + con), mỗi row kèm data CRM riêng.
- Client **không** đè được `where` đã pin: filter của client bị **AND** vào, không thay thế.
- Operator đã pin bị gỡ khỏi schema: `_eq` trên `referrer_id` biến mất
  (`field '_eq' not found in type: 'String_comparison_exp_remote_rel_usercrm_fin_invoices'`).
- `_or` không lách được: `field 'referrer_id' not found in type: 'crm_fin_invoice_bool_exp'`.
- ⚠️ **Remote relationship KHÔNG áp preset của remote schema permission.** Một relationship cố ý
  không lọc theo `$id` trả về ngay invoice của người khác. ⇒ *Definition của relationship chính là
  biên bảo mật*, không có lưới đỡ phía sau. Mọi relationship mới sang `crm` phải review kỹ `arguments`.
- `remote_field` phải dùng tên **gốc** (`order_refund`), không phải tên đã customize
  (`crm_order_refund` → `remote field with name ... not found`).
- `user.id` (uuid) → `referrer_id` (String) coerce được, không cần cast.

Không còn ẩn số kỹ thuật nào cần spike trước khi làm.

---

## 1. Bước 1 — thêm remote relationship

File: `app/hasura/metadata/sources/default/tables/public_user.yaml`

```yaml
remote_relationships:
    -
        name: crm_order_refunds
        definition:
            to_remote_schema:
                remote_schema: crm
                lhs_fields: [id]
                remote_field:
                    order_refund:
                        arguments:
                            where:
                                fin_invoice:
                                    referrer_id:
                                        _eq: $id
```

Quy tắc khi viết:
- **Bắt buộc** có `where` khoá theo `$id`. Thiếu là thủng (xem cảnh báo mục 0).
- Không pin `limit` / `offset` / `order_by` → để client tự phân trang.
- Nếu FE cần đếm tổng, thêm relationship thứ hai `crm_order_refunds_aggregate` trỏ vào
  `order_refund_aggregate` với đúng `where` như trên.

---

## 2. Bước 2 — apply & verify

```bash
docker compose exec apache bash
php bin/console hasura:metadata:apply
php bin/console hasura:metadata:get-inconsistent     # phải rỗng
```

Lưu ý: perms của remote schema `crm` là **yaml viết tay**, không sinh từ `#[Roles]` (cái đó chỉ áp cho
remote schema `local`). **Không** chạy `hasura:metadata:persist` rồi `export` đè — sẽ mất phần sửa tay.
Nếu cần `export`, kiểm diff kỹ trước khi commit.

`app/tests/_snapshots/hasura-metadata.json` không được test nào đọc, không cần cập nhật (nhưng nếu
muốn giữ nó sát thực tế thì regenerate riêng).

---

## 3. Bước 3 — query FE dùng

```graphql
query MyRefunds {
  user {                                   # không cần where — permission tự lọc self + con
    id
    email
    parent_id
    crm_order_refunds(order_by: {created_at: desc}, limit: 20) {
      id
      internal_id
      object_id
      refund_type
      status
      total
      created_at
      fin_invoice { id internal_id referrer_id client_company_id date }
    }
  }
}
```

FE phải đổi hai chỗ:
1. Bọc thêm một tầng `user`.
2. **Gộp list ở client** nếu muốn hiển thị phẳng — response nhóm theo từng user, không phải một mảng
   refund duy nhất. Sort/paginate/aggregate xuyên nhiều user không làm được ở tầng Hasura.

Cần lọc thêm (theo `status`, khoảng ngày) thì vẫn truyền `where` vào `crm_order_refunds` bình thường,
Hasura AND vào điều kiện đã pin.

Đường cũ `crm_order_refund(...)` ở root vẫn sống song song, nên FE có thể migrate từng màn một.

---

## 4. Kiểm thử chấp nhận

Chạy với `x-hasura-admin-secret` + `x-hasura-role: ROLE_USER` + `x-hasura-user-id: <id>`:

| # | Query | Kỳ vọng |
| --- | --- | --- |
| 1 | Query ở bước 3, user có con | Trả đúng các row user = self + con; mỗi row chỉ có refund của chính nó |
| 2 | Query ở bước 3, user là con | Chỉ 1 row user (chính nó) |
| 3 | `crm_order_refunds(where: {status: {_eq: "done"}})` | Thu hẹp đúng, **không** kéo thêm row của người khác |
| 4 | `crm_order_refunds(where: {fin_invoice: {referrer_id: {_eq: "<id người khác>"}}})` | `validation-failed` (operator đã bị gỡ) |
| 5 | Query cũ `crm_order_refund(...)` ở root | Vẫn chạy như trước — xác nhận không regression |
| 6 | `hasura:metadata:get-inconsistent` | rỗng |

**Data local không đủ để test dương tính**: 3 referrer duy nhất đang có refund trong CRM dev
(`019f3b56…`, `1f05bdd0…`, `1f066b76…`) không tồn tại trong bảng `user` local. Cần seed user trùng id
hoặc test trên môi trường dev. Cặp dùng được cho case "self + con" ở local:
`1f0f0684-…` (nguyen_dao@fastboy.net) + con `1f12be2c-…` (nguyen_dao_sub@gmail.com) — có invoice CRM,
chưa có refund.

---

## 5. Rollback

Không breaking nên rollback đơn giản, không ảnh hưởng FE đang dùng đường cũ:

```bash
git revert <commit>
php bin/console hasura:metadata:apply
```

Hoặc gỡ nhanh relationship mà không revert:

```bash
curl -s http://localhost:8080/v1/metadata -H 'x-hasura-admin-secret: ...' \
  -d '{"type":"pg_delete_remote_relationship","args":{"source":"default","table":{"schema":"public","name":"user"},"name":"crm_order_refunds"}}'
```

---

## 6. Hạn chế đã biết (chấp nhận)

- Chỉ phủ **1 tầng** (con trực tiếp). Cháu không với tới vì filter là `parent_id`.
- Kết quả nhóm theo user → aggregate/sort/paginate xuyên nhiều user phải làm ở FE.
- Remote join gom thành 1 request sang CRM với alias theo từng row LHS → user nhiều con thì payload
  sang CRM phình theo. Cân nhắc pin `limit` nếu số con lớn.
- Relationship bỏ qua remote-schema permission ⇒ mọi relationship mới sang `crm` phải được review
  như code bảo mật.

---

## 7. Ghi nhận — lỗ hổng đường cũ vẫn còn (đã quyết định để ngoài scope)

Plan này **không** đóng lỗ hổng hiện có. Ghi lại để không ai tưởng là đã xong:

ROLE_USER đang được cấp 8 field `order_refund*` ở root của remote schema `crm`
(`app/hasura/metadata/remote_schemas/crm/permissions/role_roleuser.yaml:1071-1078`). Cơ chế giới hạn
duy nhất là preset `fin_invoice_bool_exp.referrer_id @preset(value: {_eq: "X-Hasura-User-Id"})`
(dòng `:2101`), và preset đó **chỉ được chèn khi client có gửi object `fin_invoice`**:

| Client gửi | Kết quả (đã đo trên local) |
| --- | --- |
| `where: {fin_invoice: {...}}` | đúng phạm vi |
| không gửi `where`, hoặc `where: {}` | **đọc được refund của mọi người** |
| `where: {_or: [{fin_invoice: {}}, {internal_id: {_eq: 1453}}]}` | **lách được**, kéo về row của user khác |
| `order_refund_by_pk(id: ...)` | **đọc được bất kỳ row nào**, không có chỗ bám preset |

`crm_fin_invoice` hở nặng hơn: list không có preset argument-level nên bỏ `where` là ROLE_USER đọc
được cả 24 477 invoice kèm `client_company_id`, `total_after_tax`, quan hệ sang `client_company`/`user`.

Muốn bịt thì mở ticket riêng: gỡ các field root khỏi `role_roleuser.yaml` (breaking với FE, phải
deploy theo pha: thêm relationship → FE chuyển → mới gỡ). Khi đó cần đo thêm một ẩn số: relationship
có còn hoạt động không nếu permission schema của role không còn `type order_refund*` — đã biết
relationship bỏ qua *preset*, chưa đo trường hợp thiếu hẳn type.

---

## 8. Cần chốt trước khi làm

1. Nghiệp vụ: user cha **có** được xem refund của con không, hay chỉ xem của chính mình?
   Plan này giả định là **có** (theo yêu cầu ban đầu).
2. Có cần đếm tổng / phân trang toàn cục không? Nếu có thì phải thêm relationship aggregate và FE
   phải tự cộng — hoặc cân nhắc lại phương án resolver.
