# AStockPick Commercial Runtime

This note records the runtime switches needed by the commercial build. Do not
commit real tokens, QR images, passwords, or payment credentials.

## Membership Upgrade Display

The membership page reads upgrade information from environment variables:

```powershell
$env:LYNX_MEMBERSHIP_WECHAT = "your-wechat-id"
$env:LYNX_MEMBERSHIP_QR_URL = "https://example.com/pay-qr.png"
$env:LYNX_MEMBERSHIP_PRICE_TEXT = "会员版：¥xxx / 月"
```

If `LYNX_MEMBERSHIP_WECHAT` and `LYNX_MEMBERSHIP_QR_URL` are both empty, the
page shows an admin setup reminder instead of fake payment information.

## WeChat Push

Users bind their own ServerChan SendKey or PushPlus token on the membership
page. The backend stores masked status for display and only sends pushes when
the user is an active member.

Optional global fallback tokens for backend-only calls:

```powershell
$env:SERVERCHAN_SENDKEY = "SCT..."
$env:PUSHPLUS_TOKEN = "..."
```

## Paper Trading

Paper trading APIs are disabled in the commercial build by default.

```powershell
$env:LYNX_ENABLE_PAPER_TRADING = "0"
```

Only set `LYNX_ENABLE_PAPER_TRADING=1` for a private/internal sandbox.
