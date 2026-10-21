# Spec — D8-504 / 01: CRM bỏ `product.status = outofstock`, referral bám vào chuỗi đó

> **STATUS: IMPLEMENTED** (§5.1, §5.2) — §5.3 còn chờ CRM chốt. Repo liên quan: `referral-backend` (repo này) +
> `crm-backend`.
> **Thứ tự deploy: referral cùng hoặc TRƯỚC migration của CRM.** Xem §6.

---

## 1. Vấn đề

CRM (task D8-504) đang chuyển "còn hàng hay không" từ **trạng thái** (`product.status = 'outofstock'`)
sang **con số** (`product.stock`, derive từ `stock_quantity.available_qty`). Sub-task 05 của CRM
**xoá hẳn** hằng `Product::STATUS_OUTOFSTOCK`, xoá 3 chỗ tự động ghi status theo tồn kho, và chạy
migration `UPDATE product SET status = 'inactive' WHERE status = 'outofstock'` (64 row trên local).

Referral bám vào **cả ba** thứ vừa đổi. Nguy hiểm nhất là một cổng chặn sẽ **hỏng im lặng** — vẫn
compile, phpstan vẫn xanh, không log, không alert.

### 1.1 Vì sao không vỡ build

`referral:src/Entity/ReferralOrder/ReferralOrderProduct.php:20`

```php
public const STATUS_OUTOFSTOCK = 'outofstock';
```

Đây là **hằng riêng của referral**, chỉ trùng chuỗi với hằng CRM. Nó nằm trong
`LIST_CRM_PRODUCT_STATUS` (`:69`) — tên hằng nói thẳng đây là **bản sao enum status của CRM**.
`grep -rn "LIST_CRM_PRODUCT_STATUS" src/ tests/` chỉ ra đúng một kết quả: chỗ khai báo.

⇒ CRM xoá hằng của họ, referral vẫn build bình thường. Chỉ có điều so sánh sẽ không bao giờ đúng nữa.

---

## 2. Đường coupling 1 — `CrmProductConstraintValidator` (vỡ im lặng)

`referral:src/Validator/Constraints/CrmProductConstraintValidator.php:240`

```php
$isOutOfStock = $product['status'] === ReferralOrderProduct::STATUS_OUTOFSTOCK && !$product['allow_pre_order'];

if (!$skipOutOfStockCheck && $isOutOfStock) {
    $this->context->buildViolation($constraint->outOfStockMessage, ...)->addViolation();

    return false;
}
```

`$product` đến từ `queryProducts()` (`:139`) → `referral:graphql/Crm/GetProductById.graphql` →
`crm_product` — **đọc thẳng bảng `public.product` qua Hasura**, không đi qua resolver nào của CRM.

### 2.1 Hệ quả

1. **Cổng out-of-stock chết lặng.** Sau migration không product nào còn mang `'outofstock'`
   ⇒ `$isOutOfStock` vĩnh viễn `false` ⇒ `outOfStockMessage` không bao giờ bắn. Sản phẩm hết hàng
   thật (`stock <= 0`) sẽ được cho qua.
2. **64 product migrate sang `inactive`** giờ rơi nhánh khác — `:200`, `STATUS_INACTIVE` — với
   message `"{{ product }} is unavailable."` thay vì `outOfStockMessage`, và chịu **cờ skip khác**:
   `skipProductStatusCheck` chứ không phải `skipOutOfStockCheck`.

### 2.2 Ba mutation bị ảnh hưởng khác nhau

| Mutation | Cấu hình `CrmProductConstraint` | Có chạy `isUserAllowToMakeOrder()`? | Ảnh hưởng ở đường này |
|---|---|---|---|
| `referral_order_preview_order` | `isCheckExistenceOnly: true` (`PreviewOrder/Input.php:42`) | **không** | không đổi |
| `referral_order_create_mutation` | existence-only khi draft/deposit; ngược lại chạy đủ, 3 cờ skip theo `resellType === sellFromInventory` (`Create/Input.php:47-60`) | **có** | **mất cổng chặn** |
| `referral_order_update_mutation` | `skipOutOfStockCheck: true` + `skipProductStatusCheck: true` (`Update/Input.php:46`) | có, nhưng 2 cổng đó bị skip | không đổi |

⇒ Ở đường này chỉ `referral_order_create_mutation` bị ảnh hưởng, và bị theo kiểu tệ nhất: mất cổng
chặn chứ không phải báo lỗi.

### 2.3 Điểm may

`referral:graphql/Crm/GetProductById.graphql` **đã select sẵn cả `stock` lẫn `type`**:

```graphql
crm_product(where: {id: {_in: $productId}}) {
    allow_pre_order
    code  id  name  pid
    price_for_agent_and_master  price_for_reseller  pre_order_wait_days
    status
    stock          # <- đã có
    type           # <- đã có
    unit_price
    ...
}
```

⇒ Sửa được ngay ở tầng PHP, **không** cần đụng Hasura select permission.

---

## 3. Đường coupling 2 — `crm_check_product_stock` (CRM đổi hành vi, chặt hơn)

`referral:src/Service/ReferralOrder/ReferralOrderService.php:1567` `queryCheckProductStock()` →
`referral:graphql/Crm/CheckProductStock.graphql` → `crm:src/GraphQL/Referral/CheckProductStock/Resolver.php`.

Gọi ở `:329` (trong `applyCrmProductData()`, `:260`) và `:728`.
`applyCrmProductData()` được gọi từ `create():222` và `update():1771`; `PreviewOrder/Resolver`
gọi `orderService->create()` (`:189`) hoặc `->update()` (`:168`).

⇒ **Cả ba mutation đều đi qua đường này.**

### 3.1 CRM đã đổi gì (sub-task 04)

- Thêm check `Product::STATUS_ACTIVE` tường minh, áp cho mọi product type.
- `$qtyForCheck = $useNewStock ? $availableQty : (int) $product->getStock()` — khi
  `setting_using_product_stock` OFF **hoặc** `enable_stock_management = false`, trước đây chỉ check
  `STATUS_ACTIVE` rồi cho qua; **giờ validate thật** và có thể throw
  `'%s is inactive or out of stock'` / `'%s stock not enough to make order'`.
- Field `stock` trong response giờ là `$qtyForCheck`, không còn luôn là `available_qty`.

### 3.2 Đo tác động

`referral:ReferralOrderService:326` đã loại "stockless" line (ADR-0019) nên chỉ `device` /
`gift_card` mới tới được `check_product_stock`. Đếm trên DB CRM trong đúng tập đó:

| | count |
|---|---|
| `device` / `gift_card` đang `active` | 79 |
| … `enable_stock_management = false` | 49 |
| … **và `COALESCE(stock,0) <= 0` ⇒ giờ throw** | **20** |
| … trong đó `stock IS NULL` (throw thuần do bẫy NULL) | **15** |

⇒ 20 product làm preview/create/update của referral **lỗi cứng** ở chỗ trước đây đi lọt. 15 trong số
đó chỉ vì `(int) null = 0` — "chưa từng nhập kho" bị hiểu thành "hết hàng".

### 3.3 Không đổi

`is_pre_order` trong response vẫn được CRM trả như cũ, nên
`referral:ReferralOrderService:581` → `ReferralOrderProduct::STATUS_PRE_ORDER` giữ nguyên hành vi.

---

## 4. Đường coupling 3 — `crm_product_update_stock` (no-op → ghi thật)

`crm:ProductService::changeProductStock()` nhánh `'resell'`: trước return **im lặng** khi
`setting_using_product_stock` OFF; sub-task 03 đổi thành return chỉ khi **cả hai** cờ ON.

Referral gọi `updateCrmProductStock()` từ:
- `referral:src/GraphQL/ReferralOrder/Mutation/Create/Resolver.php:249`
- `referral:src/GraphQL/ReferralOrder/Mutation/Update/Resolver.php:304`
- `referral:src/MessageHandler/ReferralOrder/ReferralOrderUpdateStatusMessageHandler.php:142`

⇒ Khi setting OFF, đơn resell **thực sự trừ** `product.stock` bên CRM — trước đây là no-op hoàn toàn.
Không cần sửa gì ở referral, nhưng phải biết để không tưởng là bug khi thấy số nhảy.

---

## 5. Thay đổi đề xuất

### 5.1 `src/Validator/Constraints/CrmProductConstraintValidator.php:240`

```php
$isOutOfStock = $product['type'] === ReferralOrderProduct::PRODUCT_TYPE_DEVICE
    && ($product['stock'] ?? 0) <= 0
    && !$product['allow_pre_order'];
```

**Dùng `PRODUCT_TYPE_DEVICE`, KHÔNG phải `['device','gift_card']` của CRM.** Referral có từ vựng
riêng: `SELLABLE_PRODUCT_TYPES = [device, service, merchant]` (`:50`) — `gift_card` **không** bán
được ở đây, và docblock `classifyProductLines` (`referral:ReferralOrderService:1831`) nói thẳng
caveat: "holds stock" trong repo này là `device`, còn CRM là `[device, gift_card]`; `gift_card` đọc
ra stockless ở referral trong khi CRM vẫn giữ stock cho nó, và **không reachable hôm nay**. Viết
`['device','gift_card']` sẽ đẻ ra khái niệm "giữ stock" thứ hai, lệch với ADR-0019.

Khớp đúng luật CRM đã chốt ở sub-task 05:
- **lọc type** — chỉ Shippable Line mới có khái niệm hết hàng;
- **`NULL` = hết hàng** — cùng quy ước với `crm:ValidateInvoiceApplyPromotionConstraintValidator`;
- **`<= 0`** chứ không phải `= 0` — `product.stock` giữ nguyên số âm từ sub-task 01 của CRM
  (đo được `min = -257`).

### 5.2 `src/Entity/ReferralOrder/ReferralOrderProduct.php`

Xoá `STATUS_OUTOFSTOCK` (`:20`) và `LIST_CRM_PRODUCT_STATUS` (`:69`) — bản sao enum CRM, sau 5.1 thì
không còn ai dùng, và để lại thì nó tiếp tục nói dối về enum bên CRM.

Giữ nguyên `STATUS_PRE_ORDER`, `STATUS_ORDERED`, `STATUS_UNAVAILABLE` — đó là status **dòng đơn** của
referral, không liên quan tới enum product của CRM.

### 5.3 Cần CRM chốt — bẫy NULL ở `check_product_stock`

15 trong 20 ca throw mới ở §3.2 sinh từ `(int) $product->getStock()` với `stock = NULL`. Sub-task 05
của CRM đã chốt "NULL = hết hàng" cho `ValidateInvoiceApplyPromotionConstraintValidator`, nên giữ
nguyên là **nhất quán** — nhưng đây là 15 product `device`/`gift_card` chưa bao giờ nhập kho sẽ chặn
đơn resell. Chốt trước khi deploy: giữ, hay đổi `check_product_stock` sang
`null !== $stock && $stock <= 0`.

---

## 6. Thứ tự deploy

`referral-backend` phải lên **cùng hoặc trước** migration `crm:Version20260903102900`.

Ngược lại sẽ có cửa sổ mà `'outofstock'` đã biến mất khỏi dữ liệu CRM trong khi referral vẫn dò đúng
chuỗi đó ⇒ mất cổng chặn của `referral_order_create_mutation` mà **không có lỗi nào để phát hiện**.

Lưu ý đây là chiều **ngược** với D8-491 (CRM trước, referral sau).

---

## 7. Ngoài scope

- `AgentProductStock` / `ResellerInventory` — kho riêng của referral cho `sellFromInventory`, không
  đọc `product.stock` của CRM. `Create/Input.php` đã bật cả 3 cờ skip cho `resellType` này.
- `crm_product_update_stock` — không sửa gì ở referral (§4), chỉ ghi nhận đổi hành vi.

---

## 8. Verification

### 8.1 Static — ĐÃ CHẠY, XANH

- `php -l` 3 file sửa — sạch.
- `vendor/bin/phpstan analyse --no-progress` → **`[OK] No errors`** (level 0 là cổng chặn regression).
- `php bin/console lint:container --resolve-env-vars` → **OK**, exit 0.
- `./scripts/test.sh tests/Validator/Constraints` → **28 test / 43 assertion, OK**.
- Full suite: 549 test / 1335 assertion, **2 failure ở `tests/Command/Referral/BackfillMissingTrialDbTest.php`**.
  Đã `git stash` toàn bộ thay đổi rồi chạy lại đúng file đó → **fail y hệt** ⇒ có sẵn trên
  branch, không phải do thay đổi này. (Lệch với baseline "xanh tuyệt đối" ghi trong CLAUDE.md.)
- `queryProducts()` để `protected` (`:130`) chính là seam test dùng — không double Hasura client.

### 8.2 Ma trận cổng out-of-stock — `referral_order_create_mutation`

Product `device`, `allow_pre_order = false`, user reseller, `resellType` thường:

| `stock` bên CRM | Kỳ vọng |
|---|---|
| `10` | qua |
| `0` | chặn, `outOfStockMessage` |
| `NULL` | chặn |
| `-5` | chặn |
| type `service`, `stock = 0` | **qua** — không track stock |

Thêm: `allow_pre_order = true` + `stock = 0` → qua (pre-order cứu, giữ nguyên luật cũ).

### 8.3 Không kích nhầm

- `referral_order_preview_order`: `isCheckExistenceOnly: true` ⇒ product `stock = 0` vẫn preview được.
- `referral_order_update_mutation`: `skipOutOfStockCheck: true` ⇒ không đổi hành vi.
- `resellType = sellFromInventory`: cả 3 cờ skip bật ⇒ không đụng kho CRM.

### 8.4 Cross-repo — `check_product_stock` (cần cả 2 stack)

- Chọn 1 trong 20 product ở §3.2 (`device`, `esm = false`, `stock <= 0`) → preview đơn resell chứa nó
  ⇒ phải nhận lỗi từ CRM, và lỗi phải **đọc được** phía referral (không bị nuốt).
- Bật `setting_using_product_stock` + `enable_stock_management` cho product đó → quay lại validate
  theo `available_qty`.

### 8.5 Cross-repo — `product_update_stock`

- Setting OFF: tạo 1 đơn resell → `product.stock` bên CRM **thực sự giảm** (hành vi mới, §4).
- Setting ON + `esm = true`: `crm:UpdateStock/Resolver.php:78-81` vẫn `continue` ⇒ referral không ghi
  `stock`, chỉ warehouse sync ghi.
