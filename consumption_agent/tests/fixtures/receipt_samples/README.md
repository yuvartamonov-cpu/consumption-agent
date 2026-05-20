Sanitized receipt fixtures for the Photo/OCR pipeline.

These files are modeled after real OCR failure modes, but contain no personal
data, order numbers, fiscal IDs, phone numbers, addresses, or payment details.

| File | Scenario |
|---|---|
| `ozon_delivery_ocr.sample` | Ozon receipt with courier delivery line |
| `service_fee_text.sample` | Service-fee separation |
| `samokat_ofd.sample` | Samokat OFD receipt, free (0,00) delivery dropped |
| `yandex_market.sample` | Yandex Market, courier delivery as first-class line |
| `blurry_photo.sample` | Degraded OCR → weak receipt → Vision fallback |

Consumed via `process_source(path, input_type="text_file")`. See
`tests/test_photo_fixtures.py`.
