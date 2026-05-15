# Brand Parser Reference

## parse_brand_and_name(text)

Extracts name, brand, replacement period from natural language input.

### Return Value

```python
{
    'name': 'пиджак',           # item name
    'brand': 'Corneliani',      # brand or None
    'replace_months': 3,        # period in months (for DB)
    'replace_days': 90,         # original days if user entered days
    'cleaned_text': 'пиджак Corneliani'  # input without period markers
}
```

### Supported Period Formats

| Input | replace_days | replace_months |
|-------|-------------|----------------|
| `6 мес` | None | 6 |
| `6 месяцев` | None | 6 |
| `30 дн` | 30 | 1 |
| `30 дней` | 30 | 1 |
| `2 года` | None | 24 |
| `1 год` | None | 12 |

### Brand Detection Rules

1. Explicit marker: `бренд X` / `brand X`
2. Pipe separator: `название | бренд X | замена Y мес`
3. Latin after Cyrillic: `пиджак Corneliani` → brand=Corneliani
4. Known brand list: Nike, Adidas, Corneliani, etc.
5. CamelCase / capitalized word after lowercase item name

### Example Inputs

```
"нравится пиджак hemington"          → name='пиджак', brand='Hemington'
"пиджак circolo замена 24 мес"       → name='пиджак', brand='Circolo', replace_months=24
"кроссовки Nike Air Max замена 12 мес" → name='кроссовки', brand='Nike Air Max', replace_months=12
"носки | бренд Nike | замена 6 мес"   → name='носки', brand='Nike', replace_months=6
"стремянка 5 ступеней"                → name='стремянка 5 ступеней', brand=None
```

### Words Excluded from Brand Detection

Size/quantity words: `ступен`, `штук`, `шт`, `литр`, `кг`, `см`, `мм`, `метр`, `размер`

Reaction words (stripped from start): `нравится`, `классно`, `круто`, `хочу`, `запомни`
