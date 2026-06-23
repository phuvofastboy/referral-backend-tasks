# Phase 2 — Implementation Plan: Agent Stock Summary API

**Feature:** API `agent_stock_summary` — trả 4 chỉ số tồn kho/dòng chảy của agent.
**Tài liệu:** [01-requirement.md](01-requirement.md) (Q&A đã chốt) · [../output/tech-spec.md](../output/tech-spec.md) · [../02-plan.md](../02-plan.md)

## ✅ Trạng thái: ĐÃ IMPLEMENT & VERIFY (2026-06-22)

| Step | Trạng thái | File |
|---|---|---|
| 1 | ✅ | `AgentProductStockRepository::sumQuantityByAgent/findStockByAgent`, `ReferralOrderRepository::sumOrderedUnits` |
| 2 | ✅ | `app/graphql/Crm/GetProductsUnitPrice.graphql`, `Service/Crm/CrmProductPriceService` |
| 3 | ✅ | `Service/Stock/AgentStockSummaryService` (+ `AgentStockService::STOCK_HELD_FROM_STATUSES` → public) |
| 4 | ✅ | `GraphQL/Stock/Query/AgentStockSummary/{Input,Output,Resolver}.php` |
| 5 | ✅ | `role_roleuser.yaml` + `hasura:metadata:apply` |
| 6 | ✅ | resolvers-catalog regenerated; ECS skip (config cũ lỗi sẵn) |

**3 điểm OPEN đã chốt khi code:** CRM lỗi → `total_stock_value = null` (degrade); product mất giá CRM → `0`; `BETWEEN` inclusive + UTC nguyên giá trị FE.

**Lỗi gặp & fix:** `#[GraphQL\Input]` cần `default: true` để GraphQLite map class→input type khi dùng làm param; sau khi thêm file mới phải `composer dump-autoload` (classmap authoritative).

**Verify e2e (token phu_vo, qua Symfony `/graphql` lẫn Hasura gateway `:8080`):**
- Test data seed (giữ lại, không xóa): stock 2 row thủ công (27fb88f8 qty5 @100, 1ea8c1a8 qty3 @250); order 2044 → `purchase_to_inventory`+`paid` qty3; order 2045 → `sell_from_inventory`+`paid` qty2.
- Bonus: set order 2044 `paid` qua psql **kích hoạt Hasura event trigger thật** → worker import product 928ec42a qty3 (@120) vào kho.
- Kết quả: `total_stock_unit=11` (5+3+3), `total_stock_value=1610` (500+750+360), `total_purchased_unit=3`, `total_sold_unit=2` — **khớp**.

---

> Hạ tầng `agent_product_stock` + `purchase_to_inventory` + `sell_from_inventory` đã DONE ở các phase trước. Plan này **chỉ** thêm API đọc summary — **không** đụng flow create/paid/deduct.

## Tóm tắt quyết định (từ Q&A)

| Field | Nguồn | Lọc |
|---|---|---|
| `total_stock_unit` | `SUM(agent_product_stock.quantity)` của agent | snapshot (không theo ngày) |
| `total_stock_value` | `Σ quantity × crm_product.unit_price` (CRM realtime) | snapshot |
| `total_purchased_unit` | `SUM(rop.quantity)` đơn `purchase_to_inventory`, status `paid` | `created_at ∈ [start,end]` |
| `total_sold_unit` | `SUM(rop.quantity)` đơn `sell_from_inventory`, status `paid` | `created_at ∈ [start,end]` |

- **Permission:** `ROLE_USER` + `InjectUser`; agent luôn xem chính mình (**không nhận `agent_id`** — lấy từ token).
- Mọi sum quantity **bỏ line `is_shipping_product`**, gộp line trùng product (đối xứng `deductableLines()` / `increaseFromOrder()`).

---

## Step 1 — Repository: aggregation methods

### 1a. `AgentProductStockRepository` (sửa — `app/src/Repository/Stock/`)
- `sumQuantityByAgent(User $agent): int` → `total_stock_unit`.
  DQL: `SELECT COALESCE(SUM(s.quantity), 0) FROM AgentProductStock s WHERE s.agent = :agent`.
- `findStockByAgent(User $agent): array` → list `[productId, quantity]` (chỉ row `quantity > 0`) để tính `total_stock_value`.

### 1b. `ReferralOrderRepository` (sửa — `app/src/Repository/ReferralOrder/`)
- `sumOrderedUnits(User $agent, string $resellType, array $statuses, \DateTimeInterface $start, \DateTimeInterface $end): int`
  — dùng chung cho cả `purchased` và `sold`.
  ```sql
  SELECT COALESCE(SUM(rop.quantity), 0)
  FROM ReferralOrder o JOIN o.referralOrderProducts rop
  WHERE o.createdBy = :agent
    AND o.resellType = :resellType
    AND o.status IN (:statuses)
    AND o.createdAt BETWEEN :start AND :end
    AND (rop.isShippingProduct IS NULL OR rop.isShippingProduct = false)
  ```
  > `resell_type` của 2 loại này luôn được set tường minh ở create → so sánh `= :resellType` an toàn (không cần lo null fallback).

**Done when:** 3 method trả đúng số trên dữ liệu mẫu (psql cross-check).

---

## Step 2 — CRM price lookup (cho `total_stock_value`)

- **Query file mới (gọn):** `app/graphql/Crm/GetProductsUnitPrice.graphql`
  ```graphql
  query GetProductsUnitPrice($productId: [uuid!]) {
      crm_product(where: {id: {_in: $productId}}) { id unit_price }
  }
  ```
  > Tách query gọn thay vì tái dùng `GetProductById` (over-fetch). Dùng `crm_product.unit_price` (top-level — đã chốt Q1, KHÔNG phải nested `product.unit_price`).
- **Helper lấy giá:** thêm vào service mới (Step 3) hoặc `Service/Crm/` một method
  `getUnitPriceMap(array $productIds): array` → `productId => float unitPrice`, gọi `GraphqlClient::queryFromFile('Crm/GetProductsUnitPrice', ['productId' => $ids])`, build map từ `data.crm_product`.
  - Product không có trong response (đã xóa CRM) → coi `unit_price = 0`.
  - `$productIds === []` → return `[]` (không gọi CRM).

**Done when:** truyền list product_id thật → trả map đúng `unit_price`; list rỗng không gọi network.

---

## Step 3 — Service: `AgentStockSummaryService` (mới)

`app/src/Service/Stock/AgentStockSummaryService.php`

```
compute(User $agent, \DateTimeInterface $start, \DateTimeInterface $end): AgentStockSummary (DTO/array)
  total_stock_unit     = agentProductStockRepo->sumQuantityByAgent(agent)
  stockRows            = agentProductStockRepo->findStockByAgent(agent)
  priceMap             = crmPrice->getUnitPriceMap(productIds(stockRows))
  total_stock_value    = Σ row.quantity × (priceMap[row.productId] ?? 0)
  total_purchased_unit = referralOrderRepo->sumOrderedUnits(agent, PURCHASE_TO_INVENTORY, [STATUS_PAID], start, end)
  total_sold_unit      = referralOrderRepo->sumOrderedUnits(agent, SELL_FROM_INVENTORY, STOCK_HELD_FROM_STATUSES, start, end)
```

- Tập status sold: tái dùng `AgentStockService::STOCK_HELD_FROM_STATUSES` (`sent, viewed, signed, pending_payment, paid, decline_payment`) — **nên đổi sang `public const`** để service summary dùng lại (tránh hard-code lặp).
- **Xử lý CRM lỗi (OPEN — chốt trước khi code):** khi `getUnitPriceMap` throw → chọn 1: (a) để API fail; (b) trả `total_stock_value = null` + 3 field còn lại vẫn đúng (degrade); (c) coi value = 0. → Khuyến nghị **(b)**.

**Done when:** unit test/manual: 4 số khớp psql + CRM.

---

## Step 4 — GraphQL layer

Thư mục mới: `app/src/GraphQL/Stock/Query/AgentStockSummary/`

### 4a. `Input.php` — `#[GraphQL\Input(name: 'agent_stock_summary_input')]`
- `startDate: \DateTimeInterface`, `endDate: \DateTimeInterface` (`#[Assert\NotNull]`, `endDate >= startDate`).
- **Không có `agentId`** — agent luôn xem chính mình, lấy từ `#[InjectUser]` (chốt: bỏ `agent_id`).

### 4b. `Output.php` — `#[GraphQL\Type(name: 'agent_stock_summary_output')]`
- Fields: `total_stock_unit: Int`, `total_stock_value: Float`, `total_purchased_unit: Int`, `total_sold_unit: Int` (+ `factory()` như `Query/List/Output.php`).

### 4c. `Resolver.php`
```php
#[GraphQL\Query(name: 'agent_stock_summary_query', outputType: 'agent_stock_summary_output')]
#[Roles('ROLE_USER')]
public function __invoke(AgentStockSummaryInput $input, #[InjectUser] User $user): Output
```
- **Permission:** không cần check `agent_id` — luôn dùng `$user` (InjectUser) làm agent → agent chỉ thấy kho của chính mình.
- Gọi `AgentStockSummaryService::compute($user, $input->startDate, $input->endDate)` → `Output::factory(...)`.

**Done when:** query qua `/graphql` (Symfony) trả 4 field; truyền `agent_id` lạ → bị từ chối.

---

## Step 5 — Hasura remote schema permission SDL

Query Symfony expose ra FE qua Hasura remote schema → **phải** khai báo trong SDL permission, nếu không ROLE_USER không gọi được.

- Sửa `app/hasura/metadata/remote_schemas/local/permissions/role_roleuser.yaml`:
  - Thêm vào `type Query { ... }`: `agent_stock_summary_query(input_obj: agent_stock_summary_input!): agent_stock_summary_output`.
  - Thêm `type agent_stock_summary_output { total_stock_unit: Int total_stock_value: Float total_purchased_unit: Int total_sold_unit: Int }`.
  - Thêm `input agent_stock_summary_input { start_date: DateTime! end_date: DateTime! }` (**không** có `agent_id`).
- Apply: `hasura:metadata:apply` (không sửa tay path hook-protected) → reload metadata.

**Done when:** query qua Hasura gateway (token ROLE_USER) trả đúng; role khác không thấy field.

---

## Step 6 — Verify & docs

1. `vendor/bin/ecs check --fix` + `vendor/bin/rector process` (nếu chạy được) cho file mới.
2. Smoke test qua skill [`skills/smoke-test-graphql-api`](../skills/smoke-test-graphql-api/SKILL.md): gen token agent → query summary; cross-check psql.
3. Regenerate catalog: `python3 scripts/extract_resolvers.py > docs/api/resolvers-catalog.md` (resolver mới) → `check-generated-docs.sh` pass.
4. (Optional) Cập nhật `docs/domains/referral-order.md` mục stock/summary.

---

## Thứ tự thực thi

`Step 1 → 2 → 3 → 4 → 5 → 6` (tuần tự; Step 1 & 2 độc lập có thể song song).

## OPEN — chốt trước/khi code (không chặn bắt đầu Step 1)

1. **CRM lỗi khi tính `total_stock_value`**: fail / null (degrade) / 0. → đề xuất **null**.
2. ~~`agent_id` trong input~~ → ✅ **CHỐT: bỏ `agent_id`**, luôn lấy theo `InjectUser` (currentUser).
3. **Product đã xóa CRM**: `unit_price = 0` (đã đề xuất, cần xác nhận).
4. **`start_date`/`end_date` inclusive** 2 đầu (`BETWEEN`) + timezone: dùng nguyên giá trị FE gửi (UTC như phần còn lại của hệ thống).

## Ngoài scope

- Master agent / admin xem summary agent khác (Q4 chốt: chỉ self).
- Breakdown theo từng product (chỉ trả tổng).
- Ledger chuyển động kho (để tính "tồn tại end_date") — không có, không làm.
