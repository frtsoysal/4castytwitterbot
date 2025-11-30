# 🚨 Polymarket New Market Bot

Yeni Polymarket marketleri açıldığında otomatik tweet atan bot.

## Nasıl Çalışır?

1. **Polling**: Her 30 saniyede Gamma API'ye sorgu atar
2. **Karşılaştırma**: `createdAt` timestamp'ına göre yeni marketleri tespit eder
3. **State**: Son görülen market'i `bot_state.json`'da saklar
4. **Image**: Event görselini Gamma API'den alır (`image`, `coverImage` field'ları)
5. **Tweet**: Yeni market bulunca görsel ile birlikte Twitter'a post atar

## Kurulum

### 1. Bağımlılıkları Yükle

```bash
cd twitter_bot
pip install -r requirements.txt
```

### 2. Twitter API Anahtarlarını Al

1. [Twitter Developer Portal](https://developer.twitter.com/en/portal/dashboard)'a git
2. Yeni bir App oluştur
3. **User authentication settings** kısmında:
   - App permissions: "Read and Write" seç
   - Type of App: "Web App, Automated App or Bot" seç
4. Keys and tokens sayfasından al:
   - API Key
   - API Key Secret
   - Access Token
   - Access Token Secret

### 3. Environment Variables

`.env.example` dosyasını `.env` olarak kopyala ve doldur:

```bash
cp .env.example .env
```

```env
X_API_KEY=...
X_API_SECRET=...
X_ACCESS_TOKEN=...
X_ACCESS_SECRET=...
```

### 4. Botu Çalıştır

```bash
# .env dosyasını yükle
export $(cat .env | xargs)

# Botu başlat
python new_market_bot.py
```

## Konfigürasyon

`new_market_bot.py` içindeki ayarlar:

| Ayar | Varsayılan | Açıklama |
|------|-----------|----------|
| `POLL_INTERVAL_SECONDS` | 30 | Kaç saniyede bir sorgu atılacak |
| `MIN_LIQUIDITY` | 0 | Tweet atmak için minimum likidite ($) |
| `ALLOWED_TAGS` | None | Sadece belirli tag'leri filtrele (ör: `["1013"]` for earnings) |
| `FETCH_LIMIT` | 50 | Her sorguda kaç market çekilecek |
| `INCLUDE_IMAGES` | True | Tweet'e event görseli ekle |

### Örnek: Sadece Earnings Marketleri

```python
ALLOWED_TAGS = ["1013"]  # Earnings tag ID
MIN_LIQUIDITY = 5000     # Sadece $5k+ likiditeye sahip olanlar
```

## Tweet Formatı

Tweet event görseli ile birlikte paylaşılır:

```
🚨 New Polymarket Event!

Will AAPL beat Q4 2024 earnings?

💰 Liquidity: $125K
📅 Ends: 2025-01-30

Trade now 👉 https://polymarket.com/event/apple-q4-earnings

[📷 Event görseli otomatik eklenir]
```

### Görsel Kaynakları

Bot şu field'lardan görsel URL'sini alır (öncelik sırasına göre):
- `image`
- `coverImage`
- `banner_image`
- `icon`
- `thumbnail`

## Deploy

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "new_market_bot.py"]
```

### Railway / Render / Fly.io

1. Repo'yu push et
2. Environment variables'ları panel'den ayarla
3. Start command: `python new_market_bot.py`

## State Dosyası

`bot_state.json` içeriği:

```json
{
  "last_seen_created_at": "2025-11-29T10:30:00+00:00",
  "last_seen_market_ids": ["market_123", "market_124"],
  "total_tweets_sent": 42,
  "last_poll_time": "2025-11-29T12:00:00+00:00"
}
```

## Dry Run Mode

Twitter credentials olmadan bot "dry run" modunda çalışır ve tweet'leri sadece loglar.

## Loglar

Tüm aktivite `bot.log` dosyasına yazılır:

```
2025-11-29 12:00:00 [INFO] 🔍 Polling for new markets...
2025-11-29 12:00:01 [INFO] Fetched 50 markets from API
2025-11-29 12:00:01 [INFO] 🆕 Found 2 new market(s)!
2025-11-29 12:00:01 [INFO] 📷 Found image: https://cloudfront.net/...
2025-11-29 12:00:02 [INFO] 📷 Image uploaded, media_id: 1234567890
2025-11-29 12:00:02 [INFO] ✅ Tweet sent! ID: 123456789
```

