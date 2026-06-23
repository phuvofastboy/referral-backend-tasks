# Task: Cần làm api agent stock summary

## Description
- Viết api get agent stock summary
- Nội dung api như sau

input:
- ~~agent_id~~ → **bỏ** (lấy theo currentUser / `InjectUser`, agent chỉ xem chính mình — xem Q4)
- start_date
- end_date

output (scheme: qualifier + hậu tố `unit`/`value`):

| Field | Loại | Ý nghĩa |
|---|---|---|
| `total_stock_unit` | **snapshot** (hiện tại, không theo date range) | Số unit tồn kho hiện tại = `SUM(agent_product_stock.quantity)` của agent |
| `total_stock_value` | **snapshot** (hiện tại) | Giá trị tồn kho hiện tại (nguồn giá: *chưa chốt*, xem Q&A) |
| `total_purchased_unit` | **flow** (trong `[start_date, end_date]`) | Tổng unit reseller đã mua về kho qua order `purchase_to_inventory` |
| `total_sold_unit` | **flow** (trong `[start_date, end_date]`) | Tổng unit đã bán ra qua order `sell_from_inventory` |

> ⚠ **Lưu ý ngữ nghĩa:** `total_stock_unit` / `total_stock_value` là tồn ròng tích lũy **toàn thời gian** tại thời điểm gọi (không có ledger nên không tính được "tồn tại end_date"). `total_purchased_unit` / `total_sold_unit` mới lọc theo `[start_date, end_date]`. Vì vậy **không** có quan hệ `total_stock_unit = purchased − sold` trong cùng kỳ.
>
> Cặp `purchased` (mua về, `purchase_to_inventory`) ↔ `sold` (bán ra, `sell_from_inventory`) đối xứng nhau.

## Q&A

### Q1 — `total_stock_value` lấy giá từ đâu? ✅ CHỐT

**Quyết định:** Giá lấy **realtime từ CRM qua Hasura**, dùng field **`crm_product.unit_price`** (top-level, KHÔNG phải nested `crm_product.product.unit_price`).

**Công thức:** `total_stock_value = Σ (agent_product_stock.quantity × crm_product.unit_price)` trên toàn bộ stock hiện tại của agent.

**Khảo sát khả thi (đã verify):**
- Query có sẵn: `app/graphql/Crm/GetProductById.graphql` → `crm_product(where: {id: {_in: $productId}})` trả `unit_price`.
- Method có sẵn: `ReferralOrderService::queryProductById(array $ids)` (`ReferralOrderService.php:891`) — nhận list product_id, trả `crm_product` rows.
- Khớp khóa: `agent_product_stock.product_id` (string) = `crm_product.id` (uuid).
- → Không cần CRM query mới; có thể tái dùng hoặc viết query gọn hơn (chỉ `id` + `unit_price`).

**Hệ quả / còn mở (xử lý ở plan):**
- `total_stock_value` phụ thuộc CRM lúc runtime (độ trễ + rủi ro CRM lỗi/timeout). *Xử lý khi CRM fail: chưa chốt (fail / null / 0).*
- Giá là **giá CRM hiện tại**, không phải giá vốn lúc nhập → value đổi theo khi CRM đổi giá.
- Product đã bị xóa khỏi CRM → không có giá → tạm coi 0. *Cần xác nhận.*

### Q2 — `total_sold_unit` đếm gì + lọc theo ngày nào? ✅ CHỐT (revised)

**Status:** chỉ đếm đơn `sell_from_inventory` có **`status = paid`**.

> 🔄 **Đã sửa:** ban đầu chốt "đã trừ kho (submitted+)" theo `STOCK_HELD_FROM_STATUSES`, nay **đổi sang chỉ `paid`** — đối xứng với `total_purchased_unit` (cũng chỉ `paid`), summary chỉ phản ánh giao dịch đã hoàn tất thanh toán.

**Đơn vị đếm:** `SUM(referral_order_product.quantity)` (bỏ line `is_shipping_product`, gộp line trùng product).

**Date field:** lọc theo **`referral_order.created_at`** trong `[start_date, end_date]` (áp dụng cho cả `total_sold_unit` và `total_purchased_unit`).

> ⚠ Lưu ý: dùng `created_at` nên số liệu lọc theo **thời điểm tạo đơn**, không phải thời điểm kho thực sự đổi (submit/paid). Đơn tạo trong kỳ nhưng submit/paid ngoài kỳ vẫn được tính.

### Q3 — `total_purchased_unit` đếm status nào? ✅ CHỐT

**Status:** chỉ đếm đơn `purchase_to_inventory` **đã paid** (đã cộng kho — `stock_imported_at IS NOT NULL` / `status = paid`). Khớp đúng unit thực sự đã vào kho.

> ⚠ **Bất đối xứng có chủ đích:** `sold` đếm submitted+ (kho trừ lúc submit), còn `purchased` chỉ đếm paid (kho cộng lúc paid). Đúng theo cơ chế cộng/trừ kho thực tế của 2 loại đơn.

**Đơn vị đếm:** `SUM(referral_order_product.quantity)` (bỏ shipping), lọc theo `referral_order.created_at` trong `[start_date, end_date]`.

### Q4 — Permission & nguồn `agent_id`? ✅ CHỐT

**Quyết định:** **Agent tự xem** — `#[Roles('ROLE_USER')]` + `#[InjectUser]`.

- Agent chỉ xem được tồn/summary của **chính mình**.
- ✅ **Bỏ hẳn `agent_id` khỏi input** — luôn lấy agent từ `InjectUser` (currentUser). Input chỉ còn `start_date`, `end_date`.
- Chưa hỗ trợ master agent xem agent con / admin xem bất kỳ (out-of-scope phase này).
