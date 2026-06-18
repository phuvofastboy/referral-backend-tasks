# Tech Spec — Agent Purchase Products & Nhập kho riêng của Agent

**Task:** D8-397
**Liên quan:** [01-requirement.md](../01-requirement.md) · [phase1/01-requirement.md](../phase1/01-requirement.md) · [docs/domains/referral-order.md](../../../docs/domains/referral-order.md) · [docs/business/referral-order.md](../../../docs/business/referral-order.md)

> **Cập nhật:** dùng field `resell_type` (thay cho `order_type`/`agent_purchase` ở bản nháp đầu). **Phase 1 đã hoàn thành** (xem mục 2). Tài liệu này mô tả phase 2 (kho agent + strip-down flow).

---

## 1. Mục tiêu

Cho phép **Agent** tự mua sản phẩm để **nhập về kho riêng** của họ, thay vì bán cho khách. Khi đơn `purchase_to_inventory` được thanh toán (`paid`), số lượng sản phẩm được cộng vào tồn kho riêng của agent ở bảng mới `agent_product_stock` (BE là source of truth).

### `resell_type` — phân loại đơn (enum string)

| Giá trị | Ý nghĩa | FE truyền được? |
|---|---|---|
| `sell_via_crm` | Mặc định — đơn bán cho khách qua CRM (đơn thường) | ✅ |
| `purchase_to_inventory` | Agent mua hàng nhập kho riêng (**feature này**) | ✅ |
| `sell_from_inventory` | Bán từ kho agent — **chưa hỗ trợ**, chỉ khai báo sẵn | ❌ (chưa) |

`null` (đơn cũ) ≡ `sell_via_crm` qua `ReferralOrder::getEffectiveResellType()`.

### Khác biệt `purchase_to_inventory` so với đơn thường (`sell_via_crm`)

| Khía cạnh | Đơn thường | `purchase_to_inventory` |
|---|---|---|
| Người mua | Khách (Company / ClientInfo) | Chính Agent (`created_by`) |
| Client info / shipping address | Bắt buộc (XOR) | **Bỏ qua** (strip-down) |
| E-sign document (quote) | Có | **Bỏ** → Pay Now thẳng |
| Commission (`OrderCommission` + `ChangeUserAmount`) | Có | **Bỏ** |
| Trial / Referral | Tùy chọn | **Bỏ** |
| PDF / email biên nhận / pay slip khi paid | Có | **Giữ** (không bỏ) |
| Trừ stock CRM trung tâm | Có (`Crm/ChangeProductStock`) | **Không** |
| Side-effect khi paid | PDF, email, commission, trial | **Cộng `agent_product_stock`** + PDF/email |

> **⚠ Tension cần lưu ý:** curl `CreateOrder` mẫu từ FE dev hiện vẫn truyền `company` + `shipping_address` cho mọi đơn. Theo quyết định strip-down, FE **phải bỏ** các field này khi `resell_type=purchase_to_inventory` (xem [tech-spec-for-fe.md](./tech-spec-for-fe.md)). Cần đồng bộ với FE trước khi bật phase 2.

---

## 2. Trạng thái triển khai

### ✅ Phase 1 — ĐÃ XONG (commit `62737109` + follow-up)

- `ReferralOrder`: consts `RESELL_TYPE_*`, `RESELL_TYPES`, `RESELL_TYPES_SUPPORTED` (chỉ `sell_via_crm` + `purchase_to_inventory`), field `resellType` (varchar 32, nullable, **indexed** `idx_referral_order_resell_type`), helper `getEffectiveResellType()`.
- Migration `Version20260617072022` — `ADD resell_type` + index.
- Create `Input`: field `resell_type` + `Assert\Choice(RESELL_TYPES_SUPPORTED)` (FE không truyền được `sell_from_inventory`).
- Create `Resolver` + `ReferralOrderService::create()`: nhận `resellType`, default `sell_via_crm` nếu null.
- `ReferralOrderEntityType`: expose `resell_type`.
- Hasura remote schema permission SDL (3 role) + metadata applied.
- **CRM sync**: `referralOrderPaidNoti()` + `Crm/UpdateReferralOrder.graphql` gửi `resellType` (required) sang CRM khi paid; chain `OrderPaidMessage`/`OrderPaidMessageHandler`/`ReferralOrderPaidSubscriber` truyền `resell_type` (fallback `sell_via_crm`).

→ Hiện tại đơn `purchase_to_inventory` được tạo & lưu, đi luồng **bình thường** (chưa strip-down, chưa cộng kho).

### ☐ Phase 2 — CÒN LẠI (tài liệu này)

Bảng `agent_product_stock` + guard `stock_imported_at` + strip-down flow + skip commission/trial + cộng kho khi paid.

---

## 3. Database changes (phase 2)

### 3.1. Bảng mới `agent_product_stock`

| Cột | Kiểu | Ràng buộc | Ghi chú |
|---|---|---|---|
| `id` | uuid | PK | UUID v7 (`UuidV7Generator`) |
| `agent_id` | uuid | FK → `user(id)` `ON DELETE CASCADE`, NOT NULL | ManyToOne `User` (= `referral_order.created_by`) |
| `product_id` | varchar | NOT NULL | CRM product id (string), **không** phải FK DB |
| `quantity` | integer | NOT NULL, default 0 | Tồn kho cộng dồn |
| `created_at` | timestamp(0) | nullable | Timestampable on create |
| `updated_at` | timestamp(0) | nullable | Timestampable on update |

**Constraint:** `UNIQUE (agent_id, product_id)` (tên `uniq_agent_product_stock_agent_product`) — bắt buộc cho upsert `ON CONFLICT`.

### 3.2. Cột mới trên `referral_order`

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `stock_imported_at` | timestamp(0) nullable | **Guard idempotency** — set sau khi cộng kho; re-process là no-op |

> `resell_type` đã thêm ở phase 1, không lặp lại.

### 3.3. Migration

- Generate bằng `doctrine:migrations:diff` rồi **dọn drift** (bỏ `CREATE SCHEMA hdb_catalog` rác trong `down()` như `Version20260617072022`).
- `CREATE TABLE agent_product_stock` + unique; `ALTER TABLE referral_order ADD stock_imported_at`.
- `ADD COLUMN` nullable + bảng mới rỗng → zero-downtime an toàn.
- Review qua skill `db-migration-safety` / agent `migration-reviewer`.

### 3.4. Hasura metadata

- **Track** `agent_product_stock` (FE query tồn kho qua Hasura nếu cần) + set permission cho role phù hợp.
- Remote schema permission SDL: **không đổi** (agent_product_stock là bảng Hasura-tracked, không phải remote schema Symfony). Nếu expose qua mutation/query Symfony mới thì mới phải thêm SDL.
- Event-trigger `ReferralOrderPaid`: payload `data.new` cần có `resell_type`, `stock_imported_at`, `created_by_id` (mặc định Hasura gửi toàn bộ cột → OK).

---

## 4. Code changes (phase 2)

### 4.1. Entity

**`app/src/Entity/Stock/AgentProductStock.php`** (mới)
- `#[ORM\Entity(repositoryClass: AgentProductStockRepository::class)]`, `#[ORM\Table(name: 'agent_product_stock')]`, `#[ORM\UniqueConstraint(name: 'uniq_agent_product_stock_agent_product', columns: ['agent_id', 'product_id'])]`.
- Fields: `id` (UUID v7), `agent` (ManyToOne `User`, JoinColumn `onDelete: 'CASCADE'`, nullable false), `productId` (string), `quantity` (int), `createdAt`/`updatedAt` (Gedmo Timestampable).

**`app/src/Entity/ReferralOrder/ReferralOrder.php`** (sửa thêm)
```php
#[ORM\Column(type: 'datetime', nullable: true)]
private ?DateTimeInterface $stockImportedAt = null;
// + getStockImportedAt/setStockImportedAt
// + helper: public function isPurchaseToInventory(): bool
//           { return $this->getEffectiveResellType() === self::RESELL_TYPE_PURCHASE_TO_INVENTORY; }
```

### 4.2. Repository

**`app/src/Repository/Stock/AgentProductStockRepository.php`** (mới)
- `upsertIncrement(Uuid $agentId, string $productId, int $quantity): void` — raw SQL `INSERT ... ON CONFLICT (agent_id, product_id) DO UPDATE SET quantity = agent_product_stock.quantity + EXCLUDED.quantity, updated_at = now()`. Atomic, tránh race condition giữa 2 message song song.

### 4.3. GraphQL — Create mutation (strip-down)

**`Input.php`** — thêm GroupSequence group `onPurchaseToInventory` (kích hoạt khi `resellType === purchase_to_inventory`):
- `products` không rỗng.
- **Bỏ** validate `newClientInfo`/`company` XOR, `shippingAddress`, `createReferral`/`createTrial`.
- (Tùy chọn strict) `Assert\Callback` reject nếu gửi kèm `company`/`shipping_address` → để FE biết không cần gửi.

**`Resolver.php`** — khi `resellType === purchase_to_inventory`:
- Validate `currentUser->isAgent()` → nếu không, throw `GraphQLException`.
- Bỏ resolve `company`/`clientInfo`/`shippingAddress`.
- Bỏ `createReferral`/`createTrial`.
- Force `isPayNow = true`.
- **Không** tạo `ReferralOrderDocument`.
- Giữ resolver mỏng, gom rẽ nhánh vào service.

### 4.4. Service `ReferralOrderService`

**`create()`** (`:114`) — khi `purchase_to_inventory`:
- Không snapshot `creatorCommissionRate`/`creatorCommissionAmount`.
- Không gọi `updateCrmProductStock()` (không trừ CRM).
- `applyCrmProductData()` (`:193`): vẫn lấy insider price + tính total, nhưng **bỏ** nhánh commission (`:481-482`) và **bỏ** giảm stock. Dùng `$order->isPurchaseToInventory()` để rẽ nhánh.

### 4.5. Async message — cộng kho khi paid

**`app/src/Message/ReferralOrder/AddAgentProductStockMessage.php`** (mới) — `implements AsyncMailMessageInterface` → route `async_common`; payload `?string $orderId`.

**`app/src/MessageHandler/ReferralOrder/AddAgentProductStockMessageHandler.php`** (mới):
1. Load `ReferralOrder` theo `orderId`; null → return.
2. Guard: `if (!$order->isPurchaseToInventory()) return;`
3. **Guard idempotency**: `if ($order->getStockImportedAt() !== null) return;`
4. Guard status: `if ($order->getStatus() !== STATUS_PAID) return;`
5. Mỗi `ReferralOrderProduct` (bỏ `isShippingProduct`): `repo->upsertIncrement($order->getCreatedBy()->getId(), productId, quantity)`.
6. `$order->setStockImportedAt(now())` + flush.
- Bước 5+6 atomic trong 1 transaction (handler trên transactional middleware).

### 4.6. EventSubscriber `ReferralOrderPaidSubscriber` (sửa)

Trong nhánh `newStatus === STATUS_PAID && oldStatus !== STATUS_PAID`:
```php
if (($newData['resell_type'] ?? null) === ReferralOrder::RESELL_TYPE_PURCHASE_TO_INVENTORY) {
    $this->bus->dispatch(new AddAgentProductStockMessage($newData['id']));
    // BỎ commission: không dispatch OrderPaidMessage commission-amount + ChangeUserAmountMessage
    // GIỮ: referralOrderPaidNoti (CRM sync resellType) + PDF + email biên nhận
}
```
- **Skip commission**: không dispatch `ChangeUserAmountMessage`; trong `OrderPaidMessageHandler` không tính commission cho đơn này.
- **Skip trial**: `CreateTrialFromReferralOrderMessage` xuất phát từ `ClientConfirmOrderTrialSubscriber` (khách confirm) — đơn purchase_to_inventory không có bước này nên tự nhiên không trigger; vẫn double-check.
- **Giữ** `referralOrderPaidNoti` (đã gửi `resellType` sang CRM — phase 1), PDF, email/pay slip.

> **⚠ Lưu ý phối hợp với CRM sync (phase 1):** `OrderPaidMessageHandler` hiện luôn gọi `referralOrderPaidNoti(..., resellType)`. Phải đảm bảo nhánh skip-commission **không** vô tình bỏ luôn `referralOrderPaidNoti`. Tách rõ: CRM-noti + PDF + email = giữ; commission + trial = skip.

> **Hai lớp idempotency:** (1) subscriber guard `oldStatus === paid` chặn duplicate event; (2) handler guard `stock_imported_at !== null` + upsert atomic chặn double-add khi message retry.

---

## 5. Luồng end-to-end (`purchase_to_inventory`)

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent (Frontend)
    participant API as referral-backend
    participant CRM as Hasura/CRM
    participant PG as Payment Gateway
    participant H as Hasura (event trigger)
    participant MQ as RabbitMQ (async_common)
    participant HND as AddAgentProductStockMessageHandler
    participant DB as PostgreSQL

    A->>API: referral_order_create_mutation(resell_type=purchase_to_inventory, products, status=sent)
    API->>API: isAllowedToMakeOrder + isAgent() + validate products
    Note over API: strip-down: KHÔNG company/shipping/document,<br/>KHÔNG commission/trial, KHÔNG trừ CRM stock,<br/>force isPayNow
    API->>CRM: Crm/GetProductTax (insider price)
    API->>DB: persist ReferralOrder(resell_type=purchase_to_inventory, isPayNow=true)
    API-->>A: ReferralOrder { id, total, resell_type }
    A->>CRM: Thanh toán (thẻ agent)
    CRM->>PG: Charge
    PG-->>CRM: Settled
    CRM->>API: referral_order_update_status_by_transaction_mutation → paid
    API->>DB: status = paid
    H->>MQ: ReferralOrderPaid (data.new.resell_type=purchase_to_inventory)
    Note over MQ: skip commission/trial fan-out;<br/>giữ CRM-noti + PDF + email
    MQ->>HND: AddAgentProductStockMessage(orderId)
    HND->>HND: guard isPurchaseToInventory + stock_imported_at==null + status==paid
    loop mỗi ReferralOrderProduct (bỏ shipping)
        HND->>DB: upsert agent_product_stock (agent_id, product_id) quantity += qty
    end
    HND->>DB: set referral_order.stock_imported_at = now()
    API->>CRM: referralOrderPaidNoti(resellType) [vẫn chạy]
```

---

## 6. Checklist triển khai (phase 2)

- [ ] Entity `AgentProductStock` + Repository `upsertIncrement`.
- [ ] `ReferralOrder`: thêm `stockImportedAt` + helper `isPurchaseToInventory()`.
- [ ] Migration: create table + unique + `stock_imported_at` (diff + dọn drift, review `migration-reviewer`).
- [ ] Hasura: track `agent_product_stock` + permission.
- [ ] `Input`: GroupSequence `onPurchaseToInventory` (skip client/shipping/trial).
- [ ] `Resolver`: rẽ nhánh strip-down (isAgent, force payNow, no document).
- [ ] `ReferralOrderService::create` + `applyCrmProductData`: skip commission/CRM-stock cho purchase_to_inventory.
- [ ] `AddAgentProductStockMessage` + handler.
- [ ] `ReferralOrderPaidSubscriber`: dispatch stock message + skip commission/trial, **giữ** CRM-noti/PDF/email.
- [ ] Regenerate catalog: `extract_async_messages.py` (message mới).
- [ ] Đồng bộ FE: bỏ company/shipping khi purchase_to_inventory (xem FE spec).
- [ ] Cập nhật docs `docs/domains/referral-order.md` + `docs/business/referral-order.md`.

---

## 7. Rủi ro & Edge cases

1. **Idempotency double-add** (cao nhất) — 2 lớp (mục 4.6). Test: duplicate `ReferralOrderPaid` + retry message → quantity cộng đúng 1 lần.
2. **Race condition** 2 message song song cùng `(agent, product)` → `ON CONFLICT` atomic xử lý.
3. **Refund (`paid → cancelled`)** — repo **không** auto-hoàn stock CRM. Đơn purchase_to_inventory bị cancel sau paid **chưa** có cơ chế trừ ngược `agent_product_stock`. → **Ngoài scope**, TODO nếu business cần.
4. **Shipping product** trong line items — handler bỏ qua `isShippingProduct=true` khi cộng kho.
5. **Vô tình bỏ CRM-noti** khi skip commission (mục 4.6) — phải tách rõ CRM-noti khỏi commission.
6. **Tension FE payload** — FE đang gửi company/shipping; cần đổi trước khi bật strip-down (nếu không, Input strict sẽ reject hoặc backend ignore).
7. **`sell_from_inventory`** — chưa hỗ trợ; `RESELL_TYPES_SUPPORTED` đã chặn FE truyền. Không xử lý logic gì ở phase này.

---

## 8. Câu hỏi mở (confirm với business / FE / CRM)

- **FE strip-down**: FE đồng ý bỏ company/shipping/e-sign khi `purchase_to_inventory`? (mục 1 tension).
- **Email/pay slip cho agent**: quyết định giữ — gửi cho ai (agent)? Nội dung template có cần khác đơn bán khách không?
- **Refund agent_purchase**: có trừ lại `agent_product_stock` khi `paid → cancelled`? (đề xuất out-of-scope).
- **CRM dùng resellType làm gì**: CRM đã nhận `resellType` (phase 1) — xác nhận CRM không tự quản inventory agent (BE là source of truth).
