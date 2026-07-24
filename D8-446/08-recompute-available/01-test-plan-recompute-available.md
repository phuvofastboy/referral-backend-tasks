# Test Plan — Recompute Available Stock (D8-446)

Verify các thay đổi chuyển tồn reseller từ **counter tự trừ** sang **recompute từ serial items**
(nguồn sự thật = `reseller_inventory_product_item`), kèm view `agent_product_stock_available`.

- **Ngày chạy**: 2026-07-21
- **Môi trường**: local docker (`postgres`, `apache`, `hasura`)
- **Nguyên tắc**: test bằng **code thật** (repository/service methods thật, view thật), trên **product thật**.

---

## 1. Tóm tắt code changes được verify

| Layer | File | Thay đổi |
|---|---|---|
| Service | `AgentStockService` | Bỏ `deductForSubmit()`/`deductForOrder()`. `restoreForOrder()` giờ dựa serial: `releaseByOrder()` + `recomputeAvailableStock()` (không còn `restoreQuantity` cộng counter). |
| Service | `AgentProductStockService::recomputeAvailableStock(User $agent, array $productIds)` | Đếm lại tồn từ serial items (theo product của đơn) rồi `upsertSetBatch` vào `agent_product_stock`. |
| Repo | `ResellerInventoryProductItemRepository` | `countAvailableByReseller(resellerId, productIds)`, `markSoldByOrder(orderId)`, `releaseByOrder(orderId)`. |
| Service | `ReferralOrderProductSerialService` | `validateProductSerials` (strict lúc submit) + `syncFromProducts` (reserve serial). |
| Service | `ResellerInventoryDeliveryService::processDelivery` | Nhập serial items + recompute. |
| Handler | `MarkResellerInventorySoldMessageHandler` (MỚI) | Đơn PAID → `markSoldByOrder` + recompute. |
| Handler | `AddAgentProductStockMessageHandler` | **ĐÃ XÓA** (counter cũ). |
| View | `agent_product_stock_available` | `available_qty = quantity − reserved_qty`. |

---

## 2. API / Handler bị ảnh hưởng

### GraphQL API
| Root field | Resolver | Ảnh hưởng |
|---|---|---|
| `referral_order_create_mutation` | `Mutation/Create/Resolver.php` | Submit `sell_from_inventory`: **KHÔNG** trừ kho CRM; reserve serial qua `syncFromProducts` (strict). |
| `referral_order_update_mutation` | `Mutation/Update/Resolver.php` | Như trên; nhánh cancel gọi `restoreForStatusChange`. |
| `referral_order_update_status_mutation` | `Mutation/UpdateStatus/Resolver.php` | Cancel/reject → `restoreForStatusChange` (release + recompute). |
| `referral_order_update_referral_order_document_mutation` | `Mutation/UpdateReferralOrderDocument/Resolver.php` | Đổi status → `restoreForStatusChange`. |
| `referral_order_order_delivered_mutation` | `Mutation/OrderDelivered/Resolver.php` | Nhập serial + recompute. |
| `agent_product_stock_available` (query) | Hasura view | Field mới `reserved_qty`, `available_qty`. |

### Handler / async
| Thành phần | Ảnh hưởng |
|---|---|
| `ReferralOrderPaidSubscriber` | Hasura paid event → dispatch `MarkResellerInventorySoldMessage`. |
| `MarkResellerInventorySoldMessageHandler` | `markSoldByOrder` + recompute (guard: resell_type=sell_from_inventory, status=paid). |
| `ReferralOrderDocumentUpdateStatusMessageHandler` | Đổi status → `restoreForStatusChange`. |
| `OrderPaidMessageHandler` | Bỏ dispatch `AddAgentProductStockMessage` (handler cũ đã xóa). |

---

## 3. Data test (product thật)

- **Reseller** `lenguyen@gmail.com` = `019de185-20d6-7474-a95b-824fab7850a2` (parent = `1f142afe-352d-68b6-9322-6be3040c5db7`)
- **Product** `888368f0-c91a-46f6-88db-e8753482830f`
- **2 serial items** (unsold): `SN-TEST-1` (`019f596c…f0cbd4`), `SN-TEST-2` (`019f596c…c84e8a`)
- **Reservations thật**: order **2055** (paid) giữ `SN-TEST-1`; order **2057** (sent) giữ `SN-TEST-2`
- **Baseline drift phát hiện**: cache `agent_product_stock.quantity = 0` nhưng thực tế 2 item → view `available_qty = -2` (cache cũ lệch, đúng loại bug mà recompute cần fix).

> Verification chạy bằng harness tạm `d8446:verify` (console command wiring service thật; đã **xóa** sau khi test). Các method gọi là **thật**: `recomputeAvailableStock`, `markSoldByOrder`, `releaseByOrder`.

---

## 4. Test cases — ĐÃ CHẠY

| # | Case | Command / Step | Expect | Actual | Kết quả |
|---|---|---|---|---|---|
| TC-1 | Baseline drift (view math trên cache lệch) | `SELECT … FROM agent_product_stock_available` | q=0, reserved=2, avail=**-2** | q=0, reserved=2, avail=-2 | ✅ PASS |
| TC-2 | `recomputeAvailableStock` fix drift | `d8446:verify recompute <R> <P>` | cache 0→**2**; view q=2, reserved=2, avail=**0** | cache=2; view 2/2/0 | ✅ PASS |
| TC-3 | `markSoldByOrder(2055 paid)` | `d8446:verify mark-sold <2055>` | affected=1; `SN-TEST-1`.sell_order_id=2055; `SN-TEST-2` NULL | affected=1; đúng | ✅ PASS |
| TC-4 | recompute sau sold | `d8446:verify recompute <R> <P>` | q=**1** (chỉ SN-TEST-2 unsold); reserved=**1** (item đã sold không tính); avail=0 | q=1, reserved=1, avail=0 | ✅ PASS |
| TC-5 | `releaseByOrder(2055)` (nghịch đảo) + recompute | `d8446:verify release <2055>` → recompute | affected=1; cả 2 item NULL lại; q=2, reserved=2, avail=0 | affected=1; đúng baseline | ✅ PASS |
| TC-6a | API read (admin) field mới | curl Hasura admin | q=2, reserved=2, avail=0 | đúng | ✅ PASS |
| TC-6b | Permission `available_qty > 0` lọc | curl ROLE_USER=lenguyen, product avail=0 | rỗng | `[]` | ✅ PASS |
| TC-7a | Owner thấy row của mình (avail>0) | +1 temp item → curl ROLE_USER=lenguyen | thấy 1 row avail=1 | thấy | ✅ PASS |
| TC-7b | **Parent thấy row con** (`agent.parent_id`) | curl ROLE_USER=parent | thấy row con avail=1 | thấy | ✅ PASS |
| TC-7c | User không liên quan bị chặn | curl ROLE_USER=phu_vo | rỗng | `[]` | ✅ PASS |

**Ý nghĩa nghiệp vụ verified:**
- Recompute lấy đúng số serial `sell_order_id IS NULL` (TC-2/4/5).
- `reserved_qty` chỉ tính item chưa sold trên đơn active (TC-4: sau khi 2055 sold, item đó rớt khỏi reserved).
- `markSold`/`release` đối xứng, net-zero khi hoàn (TC-5).
- Permission row-level: owner + `parent_id`, chặn user ngoài, và filter `available_qty > 0` (TC-6/7).

> Sau khi test: serials về **baseline** (cả 2 NULL). Thay đổi bền duy nhất: `agent_product_stock.quantity` của lenguyen/888368f0 từ **0 → 2** (fix drift — đúng hơn baseline). Temp item TC-7 đã xóa.

---

## 5. Test cases — CHƯA CHẠY (mutation phá data thật → chạy thủ công/CI)

Các method lõi (release, markSold, recompute) đã verify trực tiếp ở mục 4; resolver chỉ delegate mỏng
xuống service. Các case e2e dưới đây verify thêm phần **wiring resolver/handler**, cần môi trường có thể
tạo/hủy đơn thoải mái (dev/CI), không nên chạy trên data thật local.

| # | Case | Cách verify | Expect |
|---|---|---|---|
| TC-8 | Submit `sell_from_inventory` KHÔNG trừ CRM, reserve serial | `referral_order_create/update_mutation` với `products[].serials` (đủ qty) | Đơn sang `sent`; serial được gắn; `crm stock` không đổi; `available_qty` giảm đúng số reserve |
| TC-9 | Submit thiếu serial → chặn | submit `sell_from_inventory` với `count(serials) < quantity` | Throw `Serial count must equal quantity` (validateProductSerials strict) |
| TC-10 | Cancel đơn đã submit → restore | `referral_order_update_status_mutation` status=`cancelled` | `releaseByOrder` + recompute; `reserved_qty` giảm; `available_qty` tăng lại |
| TC-11 | Paid → markSold (async) | Chuyển đơn sang `paid` → `ReferralOrderPaidSubscriber` dispatch → worker consume | serial `sell_order_id` set; `agent_product_stock` recompute; `messenger:consume async_common` |
| TC-12 | Delivery nhập kho + recompute | `referral_order_order_delivered_mutation` | serial items mới `INSERT`; `quantity` tăng đúng số nhập |
| TC-13 | Race: 2 đơn reserve trùng 1 serial | submit đồng thời 2 đơn cùng serial | ⚠️ **Rủi ro đã biết** — chưa có atomic guard; xem `07-*` review. Kỳ vọng hiện tại: có thể double-reserve. |

Lệnh mẫu (skill `local-test-graphql-api`, endpoint `https://localhost/graphql`):
```bash
# gen JWT cho reseller
TOKEN=$(python3 tasks/D8-397/skills/smoke-test-graphql-api/gen_token.py \
  --sub lenguyen@gmail.com --iss https://localhost --id 019de185-20d6-7474-a95b-824fab7850a2)
# cancel (TC-10)
curl -sk -X POST https://localhost/graphql -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -H 'x-hasura-role: ROLE_USER' \
  --data-raw '{"query":"mutation($id:ID!){ referral_order_update_status_mutation(id:$id, input_obj:{status:\"cancelled\"}){ id status } }","variables":{"id":"<ORDER_ID>"}}'
# paid async (TC-11)
docker compose exec worker php bin/console messenger:consume async_common --limit=1 -vv
```

---

## 6. Lệnh tái lập nhanh (read-only, an toàn)

```bash
R=019de185-20d6-7474-a95b-824fab7850a2; P=888368f0-c91a-46f6-88db-e8753482830f
# view hiện tại
docker compose exec -T postgres psql -U fastboy -d referral -tAc \
 "SELECT quantity, reserved_qty, available_qty FROM agent_product_stock_available WHERE agent_id='$R' AND product_id='$P';"
# đối chiếu: unsold items thật
docker compose exec -T postgres psql -U fastboy -d referral -tAc \
 "SELECT count(*) FROM reseller_inventory_product_item WHERE reseller_id='$R' AND product_id='$P' AND sell_order_id IS NULL;"
# đối chiếu: reserved (item unsold đang trên đơn active)
docker compose exec -T postgres psql -U fastboy -d referral -tAc \
 "SELECT count(*) FROM reseller_inventory_product_item i JOIN referral_order_product_serial s ON s.product_item_id=i.id JOIN referral_order_product rop ON rop.id=s.referral_order_product_id JOIN referral_order o ON o.id=rop.referral_order_id WHERE i.reseller_id='$R' AND i.product_id='$P' AND i.sell_order_id IS NULL AND o.status NOT IN ('cancelled','client_rejected','rejected');"
```

---

## 7. Ghi chú & rủi ro

1. **Drift cache tồn tại trong data cũ**: nhiều `agent_product_stock` row lệch với serial thật (do counter cũ). Recompute fix từng product khi có event (submit/paid/cancel/delivery). Cân nhắc chạy 1 lần backfill recompute toàn bộ để dọn drift lịch sử.
2. **Race double-reserve (TC-13)** — rủi ro đã nêu ở review `07`: check (`findReservedProductItemIds`) và act (`INSERT serial`) không nguyên tử, không có unique constraint. Cần `SELECT … FOR UPDATE` trong `validateProductSerials`.
3. **`available_qty` có thể âm** khi cache drift; `ROLE_USER` đã lọc `> 0` nên FE không thấy.
4. Harness `d8446:verify` chỉ tồn tại trong lúc test, **đã xóa**; autoload đã dump lại.
