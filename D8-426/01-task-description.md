# Task: Thanh toán đơn hàng bằng Deposit Balance

## Description
- Cần bổ sung tính năng mới cho phép user thanh toán order bằng Deposit Balance (Sẽ thêm field mới table user.credit)
- table order bổ sung field credit_use -> số tiền muốn dùng từ credit
- Khi create/update order (mutation creat/update order) -> user có thể nhập số tiền muốn sử dụng trong credit để trừ vào order
- Nếu order total = \$100, user.credit = \$30, user có thể nhập credit_use tối đa \$30, \$70 còn lại sẽ charge thêm qua payment gateway cho đủ
- Lưu ý chỉ cho phép dùng credit này khi resell_type = purchase_to_inventory
- 

## Thông tin thêm
- Thêm field `credit_use` cho referral_order
- Cho nhập số tiền `credit_use` để giảm tiền cho order
- Trả về FE order (totalAfterTax) → để show thông tin
- Xử lý Pay now → nếu totalAfterTax = 0 thì mark done + trừ user.credit
- Nếu totalAfterTax > 0 thì FE redirect để client charge, tạo transaction hold user.credit lại
- Listen order paid event từ CRM → mark order done → transaction done → trừ user.credit
- Lưu ý user.credit hiện chưa tạo (những cộng vào credit sẽ để đó, xử lý sau)


Tài liệu tham khảo
- docs/domains/referral-order.md

