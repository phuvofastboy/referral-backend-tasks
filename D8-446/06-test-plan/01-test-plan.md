# Test Plan — D8-446 (Serial reseller inventory)

Verify các thay đổi **đang active** sau task 05 + việc tạm-comment serial ở create/update/preview.

## Phạm vi cần verify
1. Schema/DB: bỏ `delivery_status` + `stock_imported_at`; thêm `warehouse_issue_id`; bỏ field `serials` khỏi `referral_order_product_input`; mutation `OrderDelivered` shape mới.
2. `OrderDelivered` (CRM): insert `reseller_inventory_product_item` + recompute `agent_product_stock`, idempotent theo `warehouse_issue_id`, validate product ∈ order.
3. Regression: order `purchase_to_inventory` **paid KHÔNG còn tạo inventory** (đã gỡ flow paid).
4. Regression: create/update/preview `sell_from_inventory` chạy **như cũ**, KHÔNG nhận `serials`.

## Môi trường / hằng số
- Endpoint: `https://localhost/graphql` (self-signed → `curl -k`).
- DB: `docker compose exec -T postgres psql -U fastboy -d referral`
- Issuer (JWT): `https://localhost` (APP_BASE_URI).
- CRM auth (cho OrderDelivered): **KHÔNG cần JWT** — chỉ 2 header `x-hasura-service: crm` + `x-hasura-role: ROLE_HASURA_CRM` (xem `HasuraServiceAuthenticator`).
- Token ROLE_USER (cho create/update/preview): `python3 .claude/skills/local-test-graphql-api/gen_token.py`.

### Dữ liệu mẫu (có thật trong DB dev lúc viết plan — kiểm tra lại trước khi chạy)
| Thứ | Giá trị |
|---|---|
| Order purchase_to_inventory (paid) | `019f1bec-c622-7199-8387-5377c4805f71` (internal_id 2055) |
| Reseller (created_by) | `019de185-20d6-7474-a95b-824fab7850a2` — `lenguyen@gmail.com` |
| Product của order | `888368f0-c91a-46f6-88db-e8753482830f` (qty 1) |

> Lấy lại dữ liệu tươi nếu DB đã đổi:
> ```bash
> docker compose exec -T postgres psql -U fastboy -d referral -tAc \
>  "SELECT ro.id, ro.internal_id, ro.created_by_id, u.email FROM referral_order ro JOIN \"user\" u ON u.id=ro.created_by_id WHERE ro.resell_type='purchase_to_inventory' AND ro.status='paid' ORDER BY ro.internal_id DESC LIMIT 5;"
> docker compose exec -T postgres psql -U fastboy -d referral -tAc \
>  "SELECT product_id, quantity FROM referral_order_product WHERE referral_order_id='<ORDER_ID>' AND is_shipping_product IS NOT TRUE;"
> ```

### Bước chuẩn bị (bắt buộc)
```bash
docker compose up -d
docker compose exec -T apache php bin/console cache:clear
```

---

## A. Schema & DB structure

### A1. Cột DB
```bash
docker compose exec -T postgres psql -U fastboy -d referral -tAc \
 "SELECT column_name FROM information_schema.columns WHERE table_name='referral_order' AND column_name IN ('delivery_status','stock_imported_at');"
# KỲ VỌNG: rỗng (2 cột đã bị drop)

docker compose exec -T postgres psql -U fastboy -d referral -tAc \
 "SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name='reseller_inventory_product_item' AND column_name='warehouse_issue_id';"
# KỲ VỌNG: warehouse_issue_id | YES

docker compose exec -T postgres psql -U fastboy -d referral -tAc \
 "SELECT indexname FROM pg_indexes WHERE tablename='reseller_inventory_product_item' AND indexname='idx_reseller_inventory_warehouse_issue_id';"
# KỲ VỌNG: có 1 dòng
```

### A2. GraphQL schema (introspection, không cần auth)
```bash
# delivery_status KHÔNG còn trên referral_order_entity_type
curl -sk -X POST https://localhost/graphql -H 'content-type: application/json' \
 -d '{"query":"query{__type(name:\"referral_order_entity_type\"){fields{name}}}"}' \
 | python3 -c "import sys,json; f=[x['name'] for x in json.load(sys.stdin)['data']['__type']['fields']]; print('delivery_status' in f)"
# KỲ VỌNG: False

# referral_order_product_input KHÔNG còn field 'serials' (đã tạm comment)
curl -sk -X POST https://localhost/graphql -H 'content-type: application/json' \
 -d '{"query":"query{__type(name:\"referral_order_product_input\"){inputFields{name}}}"}' \
 | python3 -c "import sys,json; f=[x['name'] for x in json.load(sys.stdin)['data']['__type']['inputFields']]; print('serials' in f)"
# KỲ VỌNG: False

# mutation OrderDelivered input có shape mới
curl -sk -X POST https://localhost/graphql -H 'content-type: application/json' \
 -d '{"query":"query{__type(name:\"referral_order_order_delivered_mutation_input\"){inputFields{name}}}"}' \
 | python3 -m json.tool
# KỲ VỌNG: warehouse_issue_id, referral_order_id, issue_items
```

---

## B. OrderDelivered — happy path (core)

Đặt biến (dùng order 2055):
```bash
ORDER=019f1bec-c622-7199-8387-5377c4805f71
PID=888368f0-c91a-46f6-88db-e8753482830f
ISSUE=$(python3 -c "import uuid;print(uuid.uuid4())")   # 1 warehouse issue id mới
echo "issue=$ISSUE"
```

### B1. Trạng thái trước
```bash
docker compose exec -T postgres psql -U fastboy -d referral -tAc \
 "SELECT count(*) FROM reseller_inventory_product_item WHERE purchase_order_id='$ORDER';"
# KỲ VỌNG: 0  (paid KHÔNG tạo item — xác nhận regression task 05)
docker compose exec -T postgres psql -U fastboy -d referral -tAc \
 "SELECT product_id, quantity FROM agent_product_stock WHERE agent_id='019de185-20d6-7474-a95b-824fab7850a2';"
# Ghi lại giá trị hiện tại để so sánh.
```

### B2. Gọi mutation (giao 2 unit, 2 serial)
```bash
curl -sk -X POST https://localhost/graphql \
 -H 'content-type: application/json' \
 -H 'x-hasura-service: crm' \
 -H 'x-hasura-role: ROLE_HASURA_CRM' \
 --data-raw "{\"query\":\"mutation(\$input: referral_order_order_delivered_mutation_input!){referral_order_order_delivered_mutation(input_obj: \$input)}\",\"variables\":{\"input\":{\"warehouse_issue_id\":\"$ISSUE\",\"referral_order_id\":\"$ORDER\",\"issue_items\":[{\"product_id\":\"$PID\",\"serial_number\":\"SN-TEST-1\"},{\"product_id\":\"$PID\",\"serial_number\":\"SN-TEST-2\"}]}}}" \
 | python3 -m json.tool
# KỲ VỌNG: {"data":{"referral_order_order_delivered_mutation": true}}
```

### B3. Verify persist
```bash
docker compose exec -T postgres psql -U fastboy -d referral -tAc \
 "SELECT product_id, serial_number, warehouse_issue_id, sell_order_id FROM reseller_inventory_product_item WHERE purchase_order_id='$ORDER' ORDER BY serial_number;"
# KỲ VỌNG: 2 row, serial SN-TEST-1 / SN-TEST-2, warehouse_issue_id=$ISSUE, sell_order_id NULL

docker compose exec -T postgres psql -U fastboy -d referral -tAc \
 "SELECT quantity FROM agent_product_stock WHERE agent_id='019de185-20d6-7474-a95b-824fab7850a2' AND product_id='$PID';"
# KỲ VỌNG: 2  (recompute = count item chưa bán + có serial; SET đè giá trị cũ)
```

### B4. Idempotency — gọi lại cùng warehouse_issue_id
```bash
# Chạy lại đúng lệnh B2 (cùng $ISSUE)
# KỲ VỌNG: response vẫn true; KHÔNG thêm row; stock giữ 2
docker compose exec -T postgres psql -U fastboy -d referral -tAc \
 "SELECT count(*) FROM reseller_inventory_product_item WHERE warehouse_issue_id='$ISSUE';"
# KỲ VỌNG: 2 (không nhân đôi)
```

---

## C. OrderDelivered — validation (core)

### C1. product_id không thuộc order
```bash
BADPID=$(python3 -c "import uuid;print(uuid.uuid4())")
ISSUE2=$(python3 -c "import uuid;print(uuid.uuid4())")
curl -sk -X POST https://localhost/graphql -H 'content-type: application/json' \
 -H 'x-hasura-service: crm' -H 'x-hasura-role: ROLE_HASURA_CRM' \
 --data-raw "{\"query\":\"mutation(\$input: referral_order_order_delivered_mutation_input!){referral_order_order_delivered_mutation(input_obj: \$input)}\",\"variables\":{\"input\":{\"warehouse_issue_id\":\"$ISSUE2\",\"referral_order_id\":\"$ORDER\",\"issue_items\":[{\"product_id\":\"$BADPID\",\"serial_number\":\"SN-X\"}]}}}" \
 | python3 -m json.tool
# KỲ VỌNG: errors "Issue item product not in order"; KHÔNG insert (rollback do #[Transactional])
docker compose exec -T postgres psql -U fastboy -d referral -tAc \
 "SELECT count(*) FROM reseller_inventory_product_item WHERE warehouse_issue_id='$ISSUE2';"
# KỲ VỌNG: 0
```

### C2. Order không phải purchase_to_inventory
Lấy 1 order sell_via_crm/sell_from_inventory bất kỳ, gọi OrderDelivered → KỲ VỌNG errors "not a purchase-to-inventory order".

### C3. issue_items rỗng / serial rỗng
Truyền `issue_items: []` → KỲ VỌNG validation error (Count min 1). Truyền serial_number `""` → NotBlank error.

---

## D. Regression — paid KHÔNG tạo inventory (core)

Tạo/submit + pay 1 order purchase_to_inventory mới (hoặc dùng order vừa chuyển paid), rồi:
```bash
docker compose exec -T postgres psql -U fastboy -d referral -tAc \
 "SELECT count(*) FROM reseller_inventory_product_item WHERE purchase_order_id='<NEW_PAID_ORDER>';"
# KỲ VỌNG: 0 — inventory chỉ sinh qua OrderDelivered, KHÔNG phải lúc paid.
```
> Nhanh hơn: xác nhận `messenger` không còn `AddAgentProductStockMessage`:
> ```bash
> docker compose exec -T apache php bin/console debug:messenger | grep -c AddAgentProductStock   # KỲ VỌNG 0
> ```

---

## E. Regression — sell_from_inventory create/update/preview như cũ (core)

Mục tiêu: flow không nhận `serials`, vẫn chạy bình thường.

### E1. Field serials bị từ chối
```bash
TOKEN=$(python3 .claude/skills/local-test-graphql-api/gen_token.py \
  --sub lenguyen@gmail.com --iss https://localhost \
  --id 019de185-20d6-7474-a95b-824fab7850a2)

# Gửi preview có 'serials' trong product → KỲ VỌNG lỗi schema (field không tồn tại)
curl -sk -X POST https://localhost/graphql -H 'content-type: application/json' \
 -H "authorization: Bearer $TOKEN" -H 'x-hasura-role: ROLE_USER' \
 --data-raw '{"query":"mutation($input: preview_referral_order_input!){referral_order_preview_order(input_obj:$input){id}}","variables":{"input":{"products":[{"product_id":"'$PID'","quantity":1,"serials":[{"product_item_id":"x"}]}],"resell_type":"sell_from_inventory"}}}' \
 | python3 -m json.tool
# KỲ VỌNG: errors về field 'serials' không hợp lệ trên referral_order_product_input
```

### E2. Preview/update sell_from_inventory KHÔNG serials vẫn chạy
Gọi `referral_order_preview_order` (hoặc update 1 order sell_from_inventory draft có sẵn) với products hợp lệ, KHÔNG có `serials`.
- KỲ VỌNG: trả về order bình thường (không lỗi liên quan serial).
- Lưu ý: preview/update đi qua CRM validate product/stock → dùng product_id + company từ order có thật để tránh fail do CRM (xem skill Bước 2).

---

## F. (Optional) B4 — markSold khi sell order paid

B4 vẫn active nhưng phụ thuộc `referral_order_product_serial` (hiện create/update KHÔNG tạo do đã comment). Test thủ công:
1. Insert 1 `referral_order_product_serial` trỏ 1 item (từ mục B) vào 1 product line của 1 order sell_from_inventory.
2. Cho order đó paid (mutation mark paid / update → paid).
3. Verify: item tương ứng có `sell_order_id` = order đó.
```bash
docker compose exec -T postgres psql -U fastboy -d referral -tAc \
 "SELECT id, sell_order_id FROM reseller_inventory_product_item WHERE serial_number IN ('SN-TEST-1','SN-TEST-2');"
```
> Nếu chưa nối lại serial flow (task 03 Part B đang comment) thì skip mục F.

---

## G. Cleanup (xoá data test)
```bash
docker compose exec -T postgres psql -U fastboy -d referral -c \
 "DELETE FROM reseller_inventory_product_item WHERE serial_number LIKE 'SN-TEST-%';"
# Khôi phục agent_product_stock nếu cần (giá trị cũ đã ghi lại ở B1):
# docker compose exec -T postgres psql -U fastboy -d referral -c \
#  "UPDATE agent_product_stock SET quantity=<OLD> WHERE agent_id='019de185-20d6-7474-a95b-824fab7850a2' AND product_id='888368f0-c91a-46f6-88db-e8753482830f';"
```
> Lưu ý: OrderDelivered đã đè `agent_product_stock` = count item thật. Sau khi xoá item test, recompute sẽ ra 0 cho product đó (giá trị 5 cũ là tồn từ logic increment cũ — không tự khôi phục). Chấp nhận trên dev, hoặc set tay.

---

## Checklist tổng
- [ ] A1 cột DB đúng (drop 2, add 1 + index).
- [ ] A2 schema: `delivery_status` & `serials` biến mất; OrderDelivered shape mới.
- [ ] B happy path: insert đúng số item + serial + warehouse_issue_id; agent_product_stock = count.
- [ ] B4 idempotency: gọi lại không nhân đôi.
- [ ] C validation: product ngoài order / non-purchase / rỗng → reject + rollback.
- [ ] D paid không tạo inventory (+ messenger sạch).
- [ ] E serials bị từ chối; flow sell_from_inventory cũ vẫn chạy.
- [ ] F (optional) markSold khi sell paid.
- [ ] G cleanup.
