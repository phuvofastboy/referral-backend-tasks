# Test Report — D8-446 (Serial reseller inventory)

Thực thi theo [01-test-plan.md](01-test-plan.md) trên môi trường dev local.
**Kết quả tổng: PASS toàn bộ core (A, B, C, D, E, H). F skip (task 03 Part B đang comment).**

| Ngày | Môi trường | Data mẫu |
|---|---|---|
| 2026-07-10 | local (`https://localhost/graphql`, DB `referral`) | order 2055 `019f1bec-c622-7199-8387-5377c4805f71`, reseller `lenguyen@gmail.com` (`019de185-...`), product `888368f0-...` |

---

## A. Schema & DB structure — ✅ PASS

| Check | Kỳ vọng | Kết quả |
|---|---|---|
| A1 `referral_order.delivery_status` / `stock_imported_at` | đã drop (rỗng) | ✅ rỗng |
| A1 `reseller_inventory_product_item.warehouse_issue_id` | tồn tại, nullable | ✅ `warehouse_issue_id \| YES` |
| A1 index `idx_reseller_inventory_warehouse_issue_id` | có | ✅ có |
| A2a `delivery_status` trên `referral_order_entity_type` | không còn | ✅ `False` |
| A2b `serials` trên `referral_order_product_input` | không còn (đã comment) | ✅ `False` |
| A2c `OrderDelivered` input fields | warehouse_issue_id, referral_order_id, issue_items | ✅ `['warehouse_issue_id','referral_order_id','issue_items']` |

---

## B. OrderDelivered happy path — ✅ PASS

- **B1 trước**: `reseller_inventory_product_item` cho order = **0** (xác nhận paid không tạo item); `agent_product_stock[888368f0]` = **5** (tồn cũ từ logic increment).
- **B2**: gọi mutation (CRM headers `x-hasura-service`+`x-hasura-role: ROLE_HASURA_CRM`, không JWT), 2 issue_items → `{"data":{"referral_order_order_delivered_mutation": true}}`.
- **B3 persist**:
  - 2 row `reseller_inventory_product_item`: `SN-TEST-1`, `SN-TEST-2`, `warehouse_issue_id=985c0b74-...`, `sell_order_id=NULL`. ✅
  - `agent_product_stock[888368f0]` = **2** (recompute SET đè 5 → count item chưa bán + có serial). ✅
- **B4 idempotency**: gọi lại cùng `warehouse_issue_id` → `true`, count vẫn **2** (không nhân đôi), stock vẫn **2**. ✅

---

## C. OrderDelivered validation — ✅ PASS

| Case | Kết quả |
|---|---|
| C1 product_id ngoài order | ✅ error `"Issue item product not in order: <uuid>"`; rollback → 0 row insert |
| C2 order không phải purchase_to_inventory | ✅ error `"Order is not a purchase-to-inventory order"` |
| C3a `issue_items: []` | ✅ error `"This value should not be blank."` (Count/NotBlank) |
| C3b `serial_number: ""` | ✅ error `"This value should not be blank."` |

---

## D. Regression — paid KHÔNG tạo inventory — ✅ PASS

- `debug:messenger | grep -c AddAgentProductStock` = **0** (message/handler đã xóa).
- Các order purchase_to_inventory **paid** khác (2054, 2052, 2051, 2050) đều **0 item** trong `reseller_inventory_product_item`. Chỉ order 2055 có 2 item — do đã qua `OrderDelivered` (test B), không phải do paid. ✅

---

## E. Regression — sell_from_inventory create/update/preview như cũ — ✅ PASS

- **E1**: gửi preview có `serials` trong product → GraphQL từ chối:
  `Field "serials" is not defined by type "referral_order_product_input".` ✅ (field đã comment, không còn trong schema).
- **E2**: `referral_order_preview_order` cho order sell_from_inventory (`019ed4d6-...`, `is_self_delivery:true`, **không** serials) → trả về order bình thường (`total: 360`, `resell_type: sell_from_inventory`), không lỗi liên quan serial. ✅
  - (Lần đầu thiếu shipping_address → lỗi nghiệp vụ chuẩn `"Shipping address is required"`, xác nhận resolver chạy đúng flow cũ, không dính serial.)

---

## H. Hasura list 2 bảng — ✅ PASS

2 bảng đã track trong Hasura metadata (`public_reseller_inventory_product_item.yaml`, `public_referral_order_product_serial.yaml`), permissions ROLE_HASURA_CRM (no filter) + ROLE_USER (filter reseller/creator). List qua `http://localhost:8080/v1/graphql`.

Data test: OrderDelivered tạo 2 item (issue `aaba9515-...`) + 1 `referral_order_product_serial` insert thủ công (link item `019f596c-...7716` ↔ product line).

| Check | Kết quả |
|---|---|
| H1 `reseller_inventory_product_item` list (admin) | ✅ 2 row đủ column (`warehouse_issue_id`, `sell_order_id`…) + relationships lồng đúng: `purchase_order{internal_id:2055,status:paid,resell_type:purchase_to_inventory}`, `reseller{email:lenguyen@gmail.com}`, `referral_order_product_serials[]` |
| H2 `referral_order_product_serial` list (admin) | ✅ 1 row + `referral_order_product{product_id,quantity:1}` + `reseller_inventory_product_item{serial_number,warehouse_issue_id}` lồng đúng |
| H3 permission ROLE_USER = reseller | ✅ count **2** |
| H3 permission ROLE_USER = user khác | ✅ count **0** (filter enforce đúng) |
| H3 permission ROLE_HASURA_CRM | ✅ count **2** (no filter) |

---

## F. B4 markSold khi sell paid — ⏭️ SKIP

Phụ thuộc `referral_order_product_serial` (create/update sell_from_inventory đang tạm comment ở task 03 Part B → không sinh serial). Sẽ verify khi bật lại serial flow.

---

## G. Cleanup — ⚠️ CỐ Ý GIỮ LẠI

Theo yêu cầu, **không cleanup** — giữ data test để tiện kiểm tra thêm:
- 2 item `SN-TEST-1/2` (issue `aaba9515-...`) trong `reseller_inventory_product_item`.
- 1 row `referral_order_product_serial` (SN-TEST-1).
- `agent_product_stock[888368f0]` hiện = **2** (do OrderDelivered recompute, chưa restore).

Dọn sau bằng:
```bash
docker compose exec -T postgres psql -U fastboy -d referral -c "DELETE FROM referral_order_product_serial WHERE serial_number LIKE 'SN-TEST-%';"
docker compose exec -T postgres psql -U fastboy -d referral -c "DELETE FROM reseller_inventory_product_item WHERE serial_number LIKE 'SN-TEST-%';"
docker compose exec -T postgres psql -U fastboy -d referral -c "UPDATE agent_product_stock SET quantity=5 WHERE agent_id='019de185-20d6-7474-a95b-824fab7850a2' AND product_id='888368f0-c91a-46f6-88db-e8753482830f';"
```

---

## Kết luận
Toàn bộ thay đổi D8-446 đang active hoạt động đúng end-to-end:
- OrderDelivered (CRM) insert item + serial + warehouse_issue_id, recompute stock, idempotent, validate chặt.
- Inventory không còn sinh lúc paid.
- delivery_status gỡ sạch; sell_from_inventory create/update/preview về đúng hành vi cũ (không serials).

**Chưa test** (ngoài phạm vi hiện tại): serial assignment flow (task 03 Part B) đang comment; `hasura metadata apply` (không có trigger delivered nữa nên không cần).
