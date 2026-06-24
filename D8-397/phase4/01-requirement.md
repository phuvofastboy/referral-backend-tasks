# Task: thêm field cho company

## Description
- Cần thêm field `is_reseller_inventory` bool cho company. default = false
- Ý nghĩa đánh dấu address này là address của chính reseller - người tạo order
- Khi user create order (có resell_type = `purchase_to_inventory`) -> nếu có truyền company -> require is_reseller_inventory = true
- Nếu truyền address thì validate đã có sẵn company is_reseller_inventory = true chưa, nếu có rồi thì -> báo lỗi.
- Nếu chưa thì tạo company mới `is_reseller_inventory` = true 

- Cần suggest cho tôi update/review order thì ảnh hưởng thế nào, nên sửa thế nào
