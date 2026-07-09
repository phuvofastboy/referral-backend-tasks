# Task: Event order delivered

## Description
- Referral bổ sung field: delivery_status - string
- Cần viết mutation để handle event order delivered (để CRM sẽ call vào)
Input: 
- referral_order_id - uuid
- delivery_status - string
Logic:
- Xử lý update delivery_status theo referral_order_id

- Sau đó viết subscriber và handler cho event order delivered bên referral
- trong handler, xử lý:
  - tìm những order có