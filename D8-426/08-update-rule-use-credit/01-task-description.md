# Task: Update rule to use credit

## Description

[Referral] Cần DB cần thay đổi gì
- New table `reseller_inventory_product_item`
    - id
    - reseller_id
    - product_id
    - serial_number
    - purchase_order_id
    - sell_order_id
    - created_at
    - updated_at
- New table `referral_order_product_serial`
    - id
    - referral_order_product_id
    - product_item_id
    - serial_number
    - created_at
    - updated_at

[Referral] Khi Reseller paid order `purchase_to_inventory`: 
- [Referral] stock tăng (agent_product_stock), nhưng chưa gán serial cụ thể
- [CRM] table fin_invoice -> Thêm field `delivery_status` (sync từ tracking number) → Cho phép manager update thủ công
- [Referral] Bắt sự kiện `delivery_status` = `Develivered` → Bắt đầu gán serial_number để reseller bán cho client
- [Referral] Phải đến khi approve phiếu xuất (CRM) + status “Delivered” → thì mới chốt serial bên reseller



## Giải thích thêm
- [Referral] nghĩa là xử lý ở repo referral-backend
- [CRM] nghĩa là xử lý ở repo crm-backend

## Q&A
> Hãy Q&A để clear requirement