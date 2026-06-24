# API Docs — Reseller Address (`is_reseller_inventory` + profile `owner_phone`)

Commit `68cf9574`. Hai nhóm thay đổi: (1) `company.is_reseller_inventory` đánh dấu địa chỉ kho riêng của reseller, ràng buộc trên đơn `purchase_to_inventory`; (2) profile user thêm `owner_phone` cho đủ bộ giống company address. Các field không liên quan đã được lược bớt khỏi ví dụ.

---

## 1. Company `is_reseller_inventory`

Cờ `is_reseller_inventory = true` nghĩa company đó là **địa chỉ kho riêng của reseller** (người tạo đơn), mỗi reseller chỉ nên có 1. BE quản cờ này — FE không set trực tiếp, chỉ đọc/lọc.

### 1.1 Lấy inventory company của tôi (Hasura)

Reseller query được company **do chính mình tạo** (kể cả chưa gắn đơn paid nào) nhờ permission theo `created_by_id`.

```graphql
query MyInventoryCompany {
  company(where: { is_reseller_inventory: { _eq: true } }) {
    id
    name
    address
    owner_phone
    business_phone
    is_reseller_inventory
    created_by_id
  }
}
```

Response:
```json
{
  "data": {
    "company": [
      {
        "id": "9cf2d7bb-0f73-4c8d-8841-1a9ebe60937b",
        "name": "LeNguyen Warehouse",
        "address": "Kho HCM",
        "owner_phone": "+84932698741",
        "business_phone": "+84932698741",
        "is_reseller_inventory": true,
        "created_by_id": "019de185-20d6-7474-a95b-824fab7850a2"
      }
    ]
  }
}
```

---

## 2. Tạo / sửa đơn `purchase_to_inventory`

Khi `resell_type = "purchase_to_inventory"`, company gắn vào đơn **bắt buộc là inventory company của chính reseller**. Truyền `company` (id) hoặc `new_client_info` (địa chỉ mới) như đơn thường, BE validate theo bảng dưới.

### 2.1 Mutation (create / update / preview dùng chung pattern)

```graphql
mutation CreateOrder($input: referral_order_create_mutation_input!) {
  referral_order_create_mutation(input_obj: $input) {
    id
    status
    resell_type
    company {
      id
      is_reseller_inventory
    }
  }
}
```

Variables (chỉ field liên quan — thực tế vẫn gửi products/shipping… như đơn thường):
```json
{
  "input": {
    "status": "draft",
    "resell_type": "purchase_to_inventory",
    "company": "9cf2d7bb-0f73-4c8d-8841-1a9ebe60937b",
    "products": [ { "product_id": "…", "quantity": 1, "referral_order_product_shipping_product": null } ],
    "shipping_address": { "address": "…", "city": "…", "country": "VN", "name": "…", "phone": "+84…", "postal_code": "70000", "state": "…" },
    "is_self_delivery": false
  }
}
```

> `referral_order_update_mutation(id, input_obj)` và `referral_order_preview_order(input_obj)` validate y hệt. `resell_type` immutable trên update (lấy theo đơn).

### 2.2 Quy tắc validate & lỗi trả về

| Tình huống | Kết quả |
|---|---|
| `purchase_to_inventory` + `company` là inventory company của mình | ✅ OK |
| `purchase_to_inventory` + `company` **không** phải inventory (`is_reseller_inventory=false`) | ❌ `Company must be your reseller inventory address for a purchase-to-inventory order.` |
| `purchase_to_inventory` + inventory company **của reseller khác** | ❌ `Reseller inventory company must belong to you.` |
| `purchase_to_inventory` + `new_client_info` (địa chỉ mới) nhưng đã có inventory company | ❌ `You already have a reseller inventory address. Select it instead of entering a new one.` |
| `purchase_to_inventory` + `new_client_info`, chưa có inventory company | ✅ OK (inventory company sẽ được tạo khi đơn ký/paid) |
| Đơn **khác** (`sell_via_crm` / `sell_from_inventory`) + dùng inventory company | ❌ `Reseller inventory company cannot be used for this order type.` |

Response lỗi (chuẩn GraphQL):
```json
{ "errors": [ { "message": "Company must be your reseller inventory address for a purchase-to-inventory order." } ] }
```

---

## 3. Profile `owner_phone`

Profile user thêm `owner_phone` (song song `business_phone`) để đủ bộ giống company address. Chỉ lưu BE + đọc qua Hasura, không sync CRM.

### 3.1 Cập nhật

```graphql
mutation UpdateProfile($input: user_update_mutation_input!) {
  user_update(input_obj: $input) { id }
}
```

Variables:
```json
{ "input": { "owner_phone": "+84932698741" } }
```

- Gửi `owner_phone: ""` → xoá về `null`.
- Sai định dạng số → reject (`AssertPhoneNumber`).
- `user_update` chỉ trả `id`; đọc giá trị qua Hasura (mục 3.2).

### 3.2 Đọc

```graphql
query MyProfile {
  user(where: { id: { _eq: "<my-user-id>" } }) {
    owner_phone
    business_phone
  }
}
```

Response:
```json
{ "data": { "user": [ { "owner_phone": "+84932698741", "business_phone": null } ] } }
```

---

## Tóm tắt field mới

| Field | Type | Nơi dùng |
|---|---|---|
| `company.is_reseller_inventory` | `Boolean` | đọc qua Hasura `company` + trong `company { … }` của đơn; filter được |
| `user.owner_phone` | `String` (E164) | input `user_update_mutation_input`; đọc qua Hasura `user` |
