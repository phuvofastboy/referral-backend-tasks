# D8-397 — Smoke Test: qty-based reseller discount (create/update order)

**Feature:** với đơn `resell_type = purchase_to_inventory`, nếu **tổng quantity theo `product_id`** đạt `resellerDiscountMinQty` thì **ghi đè `unit_price` = `resellerDiscountPrice`**; không đủ thì fallback về agent/reseller price. `salePrice` giữ logic cũ. Resell type khác không đổi.

**Môi trường:** Local docker — Symfony `https://localhost/graphql` (JWT tự craft, Istio không verify chữ ký), Hasura `http://localhost:8080` → CRM **dev** remote schema. Postgres `referral`.
**Ngày:** 2026-06-19

---

## Setup / Precondition

- **User test:** `phu_vo@fastboy.net` (type=`agent`, id `1f0e616a-8a04-6c62-8f29-63301b77a039`) → `useInsiderPrice = true` → unit price gốc = `priceForAgentAndMaster`.
- **Company:** `1f11c6b3-162f-6380-ad5a-4da07bc7ebd2` (Phu Vo Bussiness 2) — tồn tại local.
- **Product:** `441cc48e-75ae-4bec-a726-54e9b4a405c8` `[PRODUCT02]`, từ CRM dev:

  | field | value |
  |---|---|
  | priceForAgentAndMaster | 300 |
  | priceForReseller | 400 |
  | **resellerDiscountMinQty** | **3** |
  | **resellerDiscountPrice** | **100** |
  | taxed | true (nhưng agent insider → no tax) |

- **Công cụ:**
  - `php bin/console app:crm:get-product-tax -p <id>` — verify CRM trả 2 field discount.
  - `tasks/D8-397/smoke_order.py {create|update} --resell-type --quantity [--quantity2] [--id]` — craft token + gọi mutation.
  - Verify persist: `SELECT ... FROM referral_order_product WHERE referral_order_id=...`.

> Token & cache: `cache:clear` đã chạy trước test. Token sinh bằng `gen_token.py` (iss=`https://localhost`).

---

## Tổng hợp kết quả

| # | Test case | resell_type | qty | unit_price kỳ vọng | Thực tế | Kết quả |
|---|---|---|---|---|---|---|
| A | Create, đủ ngưỡng | purchase_to_inventory | 3 (≥3) | 100 (discount) | 100, total 300 | ✅ PASS |
| B | Create, dưới ngưỡng | purchase_to_inventory | 2 (<3) | 300 (fallback agent) | 300, total 600 | ✅ PASS |
| C | Create, resell type khác | sell_via_crm | 3 | 300 (flow cũ, không discount) | 300, total 900 | ✅ PASS |
| D | Update vượt ngưỡng | purchase_to_inventory | 2→3 | 100 (discount áp khi update) | 100, total 300 | ✅ PASS |
| E | Gộp 2 line cùng product | purchase_to_inventory | 2 + 1 = 3 | cả 2 line = 100 | line(2)=100, line(1)=100 | ✅ PASS |

**5/5 PASS.** `card_type` luôn `null` cho `purchase_to_inventory` (đúng task trước). `sale_price` luôn `null` (gate cũ false do card_type null).

---

## Chi tiết

### TC-A — Create `purchase_to_inventory`, qty=3 (≥ minQty) ⭐
- **Cmd:** `smoke_order.py create --resell-type purchase_to_inventory --quantity 3`
- **Order:** internal_id 2044, id `019edf91-a847-7d3f-ab8d-b8ab10e91dcd`, `card_type=null`.
- **DB `referral_order_product`:** `qty=3, unit_price=100, total_before_discount=300, sale_price=NULL`.
- **Phán định:** discount áp đúng — `unit_price` = `resellerDiscountPrice` (100), không phải agent price 300. ✅

### TC-B — Create `purchase_to_inventory`, qty=2 (< minQty)
- **Order:** internal_id 2045, id `019edf92-4465-7c6f-b14c-b8b2942167ec`.
- **DB:** `qty=2, unit_price=300, total=600, sale_price=NULL`.
- **Phán định:** không đủ ngưỡng → fallback `priceForAgentAndMaster` (300). ✅

### TC-C — Create `sell_via_crm`, qty=3 (control case)
- **Order:** internal_id 2046, id `019edf92-cb91-746b-b12e-2d3e6ad9f233`.
- **DB:** `qty=3, unit_price=300, total=900, sale_price=NULL`.
- **Phán định:** dù qty đủ ngưỡng, resell type khác → **không** áp discount, flow cũ nguyên vẹn. ✅

### TC-D — Update `purchase_to_inventory`, qty 2→3 ⭐
- **Cmd:** `smoke_order.py update --id 019edf92-4465-7c6f-b14c-b8b2942167ec --resell-type purchase_to_inventory --quantity 3` (đơn TC-B).
- **DB sau update:** `qty=3, unit_price=100, total=300`.
- **Phán định:** update path cũng áp discount khi vượt ngưỡng (300→100). ✅

### TC-E — Gộp quantity theo product_id ⭐
- **Cmd:** `smoke_order.py create --resell-type purchase_to_inventory --quantity 2 --quantity2 1` (2 line cùng product).
- **Order:** internal_id 2047, id `019edf94-96e0-7525-9f73-c2d97d1209ee`.
- **DB:** 2 dòng — `qty=2 → unit_price=100`, `qty=1 → unit_price=100`.
- **Phán định:** tổng qty (2+1=3) ≥ minQty → **cả 2 line** ăn discount (đúng quyết định Q2 "tổng theo product_id"). ✅

---

## Verify CRM contract
`app:crm:get-product-tax -p 441cc48e-...` trả về `list_product` có đúng 2 key camelCase **`resellerDiscountMinQty`**, **`resellerDiscountPrice`** (cùng cấp `priceForReseller`) → khớp constant `CRM_KEY_RESELLER_DISCOUNT_*` trong code. CRM dev đã expose sẵn.

---

## Chưa cover / lưu ý

1. **Submit (`status=sent`) + tax (Avalara):** test chạy ở `status=draft`. Product `taxed=true` nhưng agent insider → no tax, nên chưa exercise nhánh tax tính trên giá đã giảm (builder Avalara). Cần test với user reseller thường (không insider) + đơn submit để xác nhận tax base = discount price.
2. **User reseller thường (non-insider):** product này `allowForReseller=null` → reseller sẽ rơi vào nhánh unitPrice=0/unavailable. Cần product có `allowForReseller=true` để test fallback `priceForReseller`.
3. **Pay → cộng kho:** test này chỉ verify giá ở create/update; chưa chạy luồng paid → `agent_product_stock` (đã cover ở test-report.md Phase 4).
4. **Thiếu field discount (null):** product `b3705419...` có 2 field = null → đã verify fallback không lỗi (qua `app:crm:get-product-tax`), nhưng chưa tạo đơn với product đó.
