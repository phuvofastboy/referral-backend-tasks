# Task 07 — Allow select serials: Test Report

Ngày test: 2026-07-14. Môi trường: local docker (`https://localhost/graphql`).
Reseller test: `lenguyen@gmail.com` (`019de185-20d6-7474-a95b-824fab7850a2`),
product `888368f0-c91a-46f6-88db-e8753482830f`.

## Thay đổi (bật lại + validate)

| File | Thay đổi |
|---|---|
| `GraphQL/ReferralOrder/ReferralOrderProductInput.php` | Bỏ comment field `serials: [referral_order_product_serial_input!]` |
| `Service/ReferralOrder/ReferralOrderProductSerialService.php` | Thêm param `bool $requireExactQuantity` cho `validateProductSerials()` + `syncFromProducts()`. Strict → `count === quantity`; soft → `count <= quantity` |
| `GraphQL/.../Mutation/Create/Resolver.php` | Bỏ comment `syncFromProducts(..., $isSubmitOrder)` |
| `GraphQL/.../Mutation/Update/Resolver.php` | Bỏ comment `syncFromProducts(..., $isSubmitOrder)` |
| `GraphQL/.../Mutation/PreviewOrder/Resolver.php` | Bỏ comment `validateProductSerials(...)` (soft) |

Điểm chặn strict = `$isSubmitOrder` (`status === STATUS_SENT`) — cổng đồng bộ duy nhất trước
khi charge (paid luôn kéo theo submit). Async paid event chỉ markSold, không validate lại.

## Kết quả

| # | Case | Kỳ vọng | Kết quả |
|---|---|---|---|
| Schema | Introspect `referral_order_product_input` | có field `serials` (LIST of NON_NULL) | ✅ PASS |
| Reserved | Create chọn item đang reserved ở order khác (SN-TEST-1 ở order 2055 paid) | Lỗi "already reserved in another order" | ✅ PASS |
| A | Create **draft** qty=1 + 1 serial (SN-TEST-2) | Soft OK; persist 1 `referral_order_product_serial` | ✅ order 2057 draft, SN-TEST-2 persisted |
| B | Update **sent** (submit) + 0 serials | Strict FAIL "Serial count (0) must equal quantity (1)"; rollback | ✅ error; status vẫn `draft`; stock vẫn 2 (deduct rolled back) |
| C | Update **sent** (submit) + 1 serial (SN-TEST-2) | Strict OK; status=sent; serial persisted; stock 2→1 | ✅ status `sent`, SN-TEST-2 persisted, stock=1 |
| D | Paid → `markSoldByOrder` set `sell_order_id` | Item của order được set sell_order_id | ✅ Wiring `ReferralOrderPaidSubscriber:157` (sell_from_inventory → `MarkResellerInventorySoldMessage`) còn nguyên; subquery match đúng SN-TEST-2 cho order 2057. Logic không đổi so task 06 (đã verify end-to-end paid ở đó) |

## Requirement coverage

1. **Create/update/preview cho chọn serials** → ✅ field bật lại; Test A/C persist; preview validate soft.
2. **Pay validate qty khớp serials** → ✅ Test B/C (strict `count === quantity` khi submit).
3. **Chặn select item reserved ở order khác** → ✅ Test "Reserved" (`findReservedProductItemIds`, loại trừ order hiện tại khi edit qua `excludeOrderId`).
4. **Paid → update product_item.sell_order_id** → ✅ wiring + `markSoldByOrder` (unchanged, verified task 06).

## Ghi chú

- `php -l` sạch trên 5 file sửa. `vendor/bin/ecs` không chạy được do config ECS dùng
  `ContainerConfigurator` cũ (pre-existing tooling issue, không liên quan thay đổi này).
- Với `sell_from_inventory`, trước điểm validate serial (Update Resolver ~L308) chỉ có
  `getShippingFee` (CRM read-only) và `deductForSubmit` (giảm AgentProductStock trong DB).
  `updateCrmProductStock` (CRM write) bị **skip**. Nên strict fail → transaction rollback sạch,
  không để side-effect CRM kẹt (Test B xác nhận stock trở lại 2).
- Dữ liệu test còn để lại (order 2057 `sent`, serial SN-TEST-2). Chưa cleanup.
