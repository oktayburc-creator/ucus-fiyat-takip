# Uçuş Fiyat Takip

İstanbul (IST/SAW) – Bakü (GYD) hattında 19 Ekim 2026 gidiş ve 30 Ekim 2026 dönüş için uçuş fiyatlarını günlük kontrol eder. Hedef havayolları: AJet (VF), Pegasus (PC), Türk Hava Yolları (TK) ve Azerbaijan Airlines / AZAL (J2).

## Alarm kuralları

- En düşük toplam fiyat 10.000 TL veya altına inerse Telegram bildirimi gönderir.
- Fiyat, daha önce kaydedilen en düşük fiyata göre en az %5 düşerse Telegram bildirimi gönderir.
- Eşik oluşmazsa sadece `state.json` dosyasındaki geçmiş kaydı günceller.
- Takip tarihi geçtikten sonra işlem yapmadan çıkar.

## Gerekli GitHub Secrets

Repository → Settings → Secrets and variables → Actions → New repository secret yolundan şunları ekleyin:

- `TELEGRAM_BOT_TOKEN`: BotFather tarafından verilen güncel bot tokenı.
- `TELEGRAM_CHAT_ID`: Telegram sohbet kimliği. Bu proje için mevcut değer `5878587143`.
- `AMADEUS_CLIENT_ID`: Amadeus for Developers API key.
- `AMADEUS_CLIENT_SECRET`: Amadeus for Developers API secret.
- `AMADEUS_BASE_URL`: Test için `https://test.api.amadeus.com`, gerçek üretim verisi için `https://api.amadeus.com`.

## Zamanlama

GitHub Actions görevi her gün `06:00 UTC` saatinde çalışır; bu Türkiye saatinde `09:00`'dır. Ayrıca Actions ekranından manuel olarak da çalıştırılabilir.

## Veri kaynağı hakkında önemli not

Mevcut ilk sürüm Amadeus Flight Offers Search API kullanır. Amadeus Self-Service Flight Offers Search dokümantasyonunda bazı düşük maliyetli taşıyıcıların kapsama dahil olmayabileceği belirtilmektedir. Bu nedenle AJet veya Pegasus için sonuç gelmemesi, o havayolunda uçuş olmadığı anlamına gelmeyebilir. Bu iki taşıyıcı için tam kapsama ulaşmak amacıyla ikinci bir veri sağlayıcısı veya doğrudan havayolu entegrasyonu eklenmelidir.

## Güvenlik

Bot tokenını veya Amadeus secret değerini kod dosyalarına yazmayın. Yalnızca GitHub Secrets içinde saklayın.
