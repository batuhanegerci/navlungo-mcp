# Navlungo Domestic MCP Sunucusu

Claude'un Navlungo yurt içi kargo panelini doğrudan kontrol etmesini sağlar.

## Araçlar

| Araç | Açıklama |
|------|----------|
| `navlungo_create_shipment` | Yeni kargo gönderisi oluştur |
| `navlungo_check_shipment` | Gönderi durumunu sorgula |
| `navlungo_cancel_shipment` | Gönderiyi iptal et |
| `navlungo_check_multiple_shipments` | Toplu gönderi durumu (max 50) |
| `navlungo_list_addresses` | Adres defterini listele |
| `navlungo_list_my_carriers` | Kayıtlı taşıyıcıları listele |
| `navlungo_get_barcode` | Barkod PDF URL'i al |

## Railway Kurulumu

### 1. Yeni Railway Projesi

1. [railway.app](https://railway.app) → New Project → Deploy from GitHub repo
2. Bu klasörü push ettiğin repo'yu seç

### 2. Environment Variables

Railway panelinde **Variables** sekmesine şunları ekle:

```
NAVLUNGO_USERNAME=senin_api_kullanıcı_adın
NAVLUNGO_PASSWORD=senin_api_şifren
```

Opsiyonel (test için):
```
NAVLUNGO_BASE_URL=https://domestic-api-qa.navlungo.com/v2.1
```

### 3. Deploy URL'ini Al

Railway deploy ettikten sonra **Settings → Networking → Public URL** bölümünden URL'yi kopyala.
Örnek: `https://navlungo-mcp-production.up.railway.app`

### 4. Claude'a Ekle

Claude.ai → Settings → Integrations → Add MCP Server:

```
Name: Navlungo
URL: https://navlungo-mcp-production.up.railway.app/mcp
```

## Taşıyıcı ID Referansı

| ID | Taşıyıcı |
|----|----------|
| 1  | Otomatik (kapsam alanına göre) |
| 9  | Sürat Kargo |
| 13 | Aras Kargo |
| 14 | PTT Kargo |
| 18 | Yurtiçi Kargo |

## Örnek Kullanım (Claude'da)

```
"MFYS29970 nolu gönderinin durumu ne?"
"Bugün oluşturulmuş tüm Aras gönderilerini getir"
"Müşteri: Ahmet Yılmaz, adres: Kadıköy İstanbul, sipariş #1042 için PTT kargo gönderisi oluştur"
"MFYS29970 ve MFYS29971 gönderilerini iptal et"
```
