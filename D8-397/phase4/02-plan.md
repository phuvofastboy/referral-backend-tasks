# Phase 4 — Plan: `company.is_reseller_inventory`

**Requirement:** [01-requirement.md](01-requirement.md)
**Mục tiêu:** đánh dấu company là **địa chỉ kho riêng của reseller** (người tạo đơn), và ràng buộc đơn `purchase_to_inventory` phải dùng đúng inventory company của reseller đó.

> ⚠ Chỉ là PLAN — chưa code. Chốt plan trước khi implement.

## Quyết định đã chốt (Q&A)

| # | Quyết định |
|---|---|
| 1 | **Thời điểm tạo Company giữ promote-at-paid** (như flow hiện tại) — KHÔNG tạo lúc create. |
| 2 | Unique **1 inventory company / reseller** (theo `created_by`). Truyền address khi đã có → **reject**. |
| 3 | Company truyền vào cho `purchase_to_inventory` mà `is_reseller_inventory=false` → **reject error**. |
| 4 | **Update order enforce như create.** |
| + | **Phải fix `created_by`/`updated_by`** khi tạo company (hiện không set ở promote, nhất là async). |

Tất cả rule chỉ áp khi đơn là **`purchase_to_inventory`** (`$order->isPurchaseToInventory()`).

---

## ⚠ Mâu thuẫn cần lưu ý: promote-at-paid vs uniqueness

Hiện `Company` chỉ được tạo lúc **paid/signed** (promote từ `new_client_info`), ở 3 chỗ:
- `MarkPaid/Resolver.php:101` (sync, có user)
- `ReferralOrderUpdateStatusMessageHandler.php:130` (async, **không** có security user)
- `ReferralOrderDocumentUpdateStatusMessageHandler.php:44` (async, **không** có security user)

Hệ quả khi giữ promote-at-paid:
1. **`created_by` không được set ở 2 handler async** (Blameable cần security token → null trong worker). → uniqueness theo `created_by` sẽ sai. **Bắt buộc set `createdBy` tường minh = `order.getCreatedBy()` tại cả 3 chỗ.**
2. **Race/duplicate:** nếu reseller tạo 2 đơn `purchase_to_inventory` bằng address khi **cả hai chưa paid**, lúc create cả hai đều thấy "chưa có inventory company" → khi paid promote 2 lần → 2 inventory company. → **Promote phải re-check & reuse** company inventory đã có thay vì tạo mới (idempotent). Cân nhắc thêm **unique partial index** `(created_by) WHERE is_reseller_inventory` để chốt DB-level.

→ Plan xử lý cả 2 điểm này (mục 3 & 5).

---

## 1. DB & Entity

**`app/src/Entity/Company/Company.php`** (sửa)
```php
#[ORM\Column(type: 'boolean', options: ['default' => false])]
private bool $isResellerInventory = false;
// + getter/setter
```

**Migration** (`doctrine:migrations:diff` rồi dọn drift Hasura):
- `ALTER TABLE company ADD is_reseller_inventory BOOLEAN DEFAULT false NOT NULL`.
- (Khuyến nghị) **partial unique index**: `CREATE UNIQUE INDEX uniq_company_reseller_inventory ON company (created_by_id) WHERE is_reseller_inventory = true;` — chốt 1/reseller ở DB, chặn race. *(cần `created_by_id` luôn được set — xem mục 3.)*
- `ADD COLUMN` default + index partial → zero-downtime an toàn. Review qua `migration-reviewer`.

## 2. Hasura

- Track cột `is_reseller_inventory` trên bảng `company` (thêm vào `columns` của select permission ROLE_USER) để FE biết company nào là inventory.
- **Select permission `company` (ROLE_USER) — thêm điều kiện `created_by_id = X-Hasura-User-Id`** vào `_or` filter để reseller query được company **do chính họ tạo** (kể cả inventory company chưa gắn đơn paid nào). File `app/hasura/metadata/sources/default/tables/public_company.yaml`:
  ```yaml
  filter:
      _or:
          - created_by_id: { _eq: X-Hasura-User-Id }        # <-- THÊM
          - referral_orders: { created_by_id: { _eq: X-Hasura-User-Id } }
          - referral_orders: { created_by_user: { parent_id: { _eq: X-Hasura-User-Id } } }
  ```
  → `hasura:metadata:apply` sau khi sửa. *(File bị hook `protect-sensitive` chặn — cần mở quyền hoặc sửa thủ công.)*
  > Cột `is_reseller_inventory` chỉ thêm vào `columns` SAU khi migration tạo cột (mục 1), nếu không `metadata:apply` sẽ lỗi.
- Nếu company expose qua remote-schema Symfony (`company_entity_type`) → thêm field vào SDL `role_roleuser.yaml` (mục 7).

## 3. Fix `created_by`/`updated_by` khi tạo Company (BẮT BUỘC, độc lập)

`CompanyService::create()` hiện không nhận/không set `createdBy`. Blameable chỉ tự set khi có security user → fail ở 2 handler async.

**Đề xuất:** thêm tham số `?User $createdBy = null` vào `CompanyService::create()`; nếu truyền thì `setCreatedBy($createdBy)` tường minh. Tại 3 promote site truyền `$referralOrder->getCreatedBy()`.
- MarkPaid (sync) cũng nên truyền tường minh cho nhất quán (tránh phụ thuộc token).
- Đây là tiền đề cho uniqueness theo `created_by` + partial unique index.

## 4. Repository finder

**`CompanyRepository`** (sửa) — `findResellerInventory(User $reseller): ?Company`
```sql
SELECT c FROM Company c WHERE c.createdBy = :reseller AND c.isResellerInventory = true
```
Dùng cho: validate trùng lúc create/update + reuse lúc promote.

## 5. Validation logic (create + update) — gom 1 chỗ

Thêm `ReferralOrderService::assertResellerInventoryCompany(?Company $company, ?ClientInfo $newClientInfo, User $user): void` (hoặc tên tương tự), gọi từ **Create resolver** và **Update resolver/service** khi `resell_type === purchase_to_inventory`:

```
if (!isPurchaseToInventory) return;            // chỉ áp purchase_to_inventory
if ($company !== null) {
    if (!$company->isResellerInventory()) throw "Company must be your reseller inventory address";
    // (tuỳ chọn) check $company->getCreatedBy() === $user
} elseif ($newClientInfo !== null) {           // truyền address
    if (companyRepo->findResellerInventory($user) !== null) {
        throw "You already have a reseller inventory address, select it instead of entering a new one";
    }
    // chưa có → cho qua; Company sẽ được tạo lúc promote (paid) với is_reseller_inventory=true (mục 6)
}
```

> Lưu ý: vì giữ promote-at-paid, validation lúc create chỉ **chặn trùng dựa trên company đã persist**. Trường hợp race 2 đơn chưa paid → dựa vào reuse-at-promote + partial unique index (mục 3) để chốt.

## 6. Promote sites — set flag + createdBy + reuse

> 🔑 **Quy tắc khi order paid (lưu ý chính):** trước khi tạo company, **PHẢI kiểm tra reseller đã có `company.is_reseller_inventory = true` chưa — nếu CÓ thì reuse (link vào order), CHỈ tạo mới khi CHƯA có.** Tuyệt đối không tạo company inventory thứ 2 cho cùng reseller.

Tại cả 3 chỗ promote (`MarkPaid`, `ReferralOrderUpdateStatusMessageHandler`, `ReferralOrderDocumentUpdateStatusMessageHandler`), khi `order.isPurchaseToInventory()`:
1. **Reuse trước (bắt buộc):** `existing = companyRepo->findResellerInventory($order->getCreatedBy())`.
   - Nếu `existing !== null` → `order->setCompany($existing)`, **KHÔNG tạo mới**, kết thúc.
2. Nếu **chưa có** → `companyService->create(...)` với:
   - `createdBy = $order->getCreatedBy()` (mục 3),
   - `isResellerInventory = true`.
3. (Nên gom logic promote 3 chỗ về 1 helper chung để tránh lệch — hiện đang lặp code 3 nơi; đảm bảo cả 3 đều chạy reuse-check này.)

> Đây là lớp chốt cuối cho uniqueness (kết hợp validation lúc create ở mục 5 + partial unique index ở mục 1). Kể cả khi 2 đơn chưa-paid lọt qua check lúc create, bước reuse này đảm bảo chỉ 1 inventory company được tạo.

> Với đơn KHÔNG phải purchase_to_inventory: promote như cũ, `is_reseller_inventory=false`.

## 7. GraphQL expose

- `ReferralOrderEntityType` / company output: thêm field `is_reseller_inventory` để FE đọc.
- Input create/update: **không cần field mới** (đã có `company` id + `new_client_info`). `is_reseller_inventory` do BE quản, FE không set trực tiếp.
- Remote-schema SDL `role_roleuser.yaml`: thêm `is_reseller_inventory: Boolean` vào type company tương ứng + `hasura:metadata:apply`.
- **(Open/suggest)** FE cần cách lấy "inventory company của tôi" để truyền `company` id ở các đơn sau → cân nhắc query `my_reseller_inventory_company` hoặc filter qua Hasura company select. Xem mục 9.

## 8. Update / Review (preview) order — ảnh hưởng & đề xuất

Phần user hỏi: "update/review ảnh hưởng thế nào, sửa thế nào".

- **Create** (`referral_order_create_mutation`): thêm gọi `assertResellerInventoryCompany(...)` (mục 5). Resolver đã có `$currentUser`.
- **Update** (`referral_order_update_mutation`): `resell_type` immutable nhưng **company có thể đổi** (service `update()` `setCompany`). Theo Q&A #4 → gọi cùng validation khi `$order->isPurchaseToInventory()` và input có đổi company/new_client_info. Đặt ở Update resolver (có user) trước khi gọi `update()`.
- **Preview** (`referral_order_preview_order`): chỉ tính toán hiển thị, **không persist**, không promote company. Đề xuất:
  - Vẫn nên **validate company is_reseller_inventory** (reject nếu sai) để preview phản ánh đúng lỗi sẽ gặp khi submit — nhất quán UX. Nhưng **không** chặn "address khi đã có inventory company" gắt như create (preview có thể chỉ để xem giá). → **cần chốt** mức độ validate ở preview (mục 9).
  - Preview hiện gọi `create()`/`update()` nội bộ nhưng không persist company → không ảnh hưởng uniqueness.

## 9. Câu hỏi mở (chốt trước khi implement)

1. **Preview có enforce validation inventory company không**, hay chỉ create/update? (đề xuất: chỉ check company sai cờ → reject; bỏ check trùng address).
2. **FE lấy inventory company id bằng cách nào** (query mới vs Hasura company filter)? Ảnh hưởng mục 7.
3. **Có thêm partial unique index** ở DB không (khuyến nghị có, nhưng cần `created_by` luôn set)?
4. Company `is_reseller_inventory=true` **có bị cấm dùng cho đơn KHÁC** purchase_to_inventory không (vd sell_via_crm truyền nhầm inventory company làm địa chỉ khách)? (đề xuất: cấm — reject).
5. Khi reject company sai cờ: cho phép `created_by` của company khác reseller hiện tại không (share inventory)? (đề xuất: company phải thuộc chính reseller).

## 10. Checklist triển khai

- [ ] Entity `Company.isResellerInventory` + getter/setter.
- [ ] Migration ADD column (+ partial unique index nếu chốt) + dọn drift, review.
- [ ] `CompanyService::create()` nhận + set `createdBy` (fix Blameable async).
- [ ] `CompanyRepository::findResellerInventory(User)`.
- [ ] `assertResellerInventoryCompany()` + gọi ở Create & Update resolver.
- [ ] 3 promote site: reuse + set `createdBy` + `isResellerInventory=true` (gom helper).
- [ ] Expose `is_reseller_inventory` (EntityType + Hasura SDL + metadata apply).
- [ ] (Nếu chốt) query lấy inventory company cho FE.
- [ ] Regenerate catalog/docs nếu thêm resolver.
- [ ] Smoke test (mục 11).

## 11. Verify (sau khi implement)

- Reseller chưa có inventory company → tạo `purchase_to_inventory` bằng address → paid → Company tạo với `is_reseller_inventory=true`, `created_by=reseller`.
- Tạo `purchase_to_inventory` lần 2 bằng address (đã có inventory) → **reject**.
- Truyền company id là inventory company → OK; company thường (`is_reseller_inventory=false`) → **reject**.
- Update đơn purchase_to_inventory đổi sang company thường → **reject**.
- Đơn `sell_via_crm`/`sell_from_inventory` → không bị áp rule (trừ khi chốt mục 9.4).
- `created_by` được set đúng ở promote async (qua status handler), không null.
```
