---
name: local-test-graphql-api
description: >
  Hướng dẫn 1 số lệnh thường dùng trên project referral
---

## Lệnh generate user token - Môi trường Dev

```shell
kubectl -n dev-referral exec -i svc/backend-apache -c apache -- \
    php bin/console lexik:jwt:generate-token <user-email>
```