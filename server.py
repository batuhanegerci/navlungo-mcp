"""
Navlungo Domestic MCP Server
Navlungo yurt içi kargo API'si için MCP sunucusu.
"""

import json
import os
import time
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

BASE_URL = os.getenv("NAVLUNGO_BASE_URL", "https://domestic-api.navlungo.com/v2.1")
NAVLUNGO_USERNAME = os.getenv("NAVLUNGO_USERNAME", "")
NAVLUNGO_PASSWORD = os.getenv("NAVLUNGO_PASSWORD", "")

CARRIER_NAMES = {
    1:  "Otomatik / Kapsam Alanına Göre",
    9:  "Sürat Kargo",
    10: "HepsiJet",
    11: "Kolay Gelsin",
    12: "Scotty",
    13: "Aras Kargo",
    14: "PTT Kargo",
    16: "HepsiJet XL",
    18: "Yurtiçi Kargo",
}

STATUS_NAMES = {
    1:  "Teslim Alınacak",
    2:  "Teslim Edildi",
    3:  "Teslim Edilecek",
    4:  "Dağıtıma Çıktı",
    5:  "Tekrar Sevk",
    6:  "Dağıtım Planlandı",
    7:  "İade Edilecek",
    9:  "İade Edildi",
    10: "İptal",
    14: "Ön İzleme",
    16: "Teslim Alındı",
    17: "Transfer Aşamasında",
    18: "Şubede Beklemede",
    19: "Tazmin Değerlendiriliyor",
    20: "Tazmin Tamamlandı",
    21: "Depoya İade Edildi",
}

# ---------------------------------------------------------------------------
# Token cache (8 saatlik geçerlilik)
# ---------------------------------------------------------------------------

_token_cache: dict = {"token": None, "expires_at": 0.0}

TOKEN_TTL_SECONDS = 7 * 3600  # 8 saatten biraz kısa tutalım


async def get_token() -> str:
    """Mevcut token geçerliyse döndür, değilse yenile."""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{BASE_URL}/auth/api",
            json={"username": NAVLUNGO_USERNAME, "password": NAVLUNGO_PASSWORD},
            headers={"Content-Type": "application/json"},
        )
        data = resp.json()

    if not data.get("status"):
        raise ValueError(f"Token alınamadı: {data.get('error', 'Bilinmeyen hata')}")

    _token_cache["token"] = data["data"]["access_token"]
    _token_cache["expires_at"] = now + TOKEN_TTL_SECONDS
    return _token_cache["token"]


def _headers_with_token(token: str) -> dict:
    return {
        "Content-Type": "application/json",
        "X-localization": "tr",
        "Authorization": f"Bearer {token}",
    }


def _handle_error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        err = body.get("error") or body.get("message") or resp.text
        return f"Hata {resp.status_code}: {err}"
    except Exception:
        return f"Hata {resp.status_code}: {resp.text[:300]}"


# ---------------------------------------------------------------------------
# MCP Sunucusu
# ---------------------------------------------------------------------------

mcp = FastMCP("navlungo_mcp")


# ---------------------  Gönderi Oluşturma  ---------------------------------

class CreateShipmentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    reference_id: Optional[str] = Field(
        default=None,
        description="Shopify sipariş no gibi kendi referans numaranız (örn: 'TML-1042')"
    )
    carrier_id: int = Field(
        ...,
        description="Taşıyıcı ID. Aras=13, PTT=14, Yurtiçi=18, Otomatik=1",
    )
    post_type: int = Field(
        default=2,
        description="Teslimat türü: 1=Aynı Gün, 2=Standart (varsayılan: 2)",
        ge=1, le=2,
    )
    cod_payment_type: Optional[int] = Field(
        default=None,
        description="Kapıda ödeme türü: 1=Nakit, 2=Kredi Kartı. Kapıda ödeme yoksa boş bırakın.",
    )
    sender_address_id: int = Field(
        ...,
        description="Adres defterindeki gönderici adres ID'si (navlungo_list_addresses ile öğrenin)",
    )
    recipient_name: str = Field(..., description="Alıcı adı soyadı", min_length=2)
    recipient_phone: str = Field(
        ..., description="Alıcı telefonu, format: +90 5XX XXX XX XX"
    )
    recipient_address: str = Field(..., description="Alıcı açık adresi")
    recipient_city: str = Field(..., description="Alıcı şehri (örn: 'İstanbul')")
    recipient_district: str = Field(..., description="Alıcı ilçesi (örn: 'Kadıköy')")
    recipient_email: Optional[str] = Field(default=None, description="Alıcı e-posta (opsiyonel)")
    recipient_post_code: Optional[str] = Field(default=None, description="Posta kodu (opsiyonel)")
    desi: float = Field(..., description="Paketin desi/ağırlığı (kg)", gt=0)
    package_count: int = Field(default=1, description="Paket adedi", ge=1)
    cod_amount: Optional[float] = Field(
        default=None,
        description="Kapıda tahsil edilecek tutar (TL). Kapıda ödeme varsa zorunlu.",
    )
    note: Optional[str] = Field(default=None, description="Gönderi notu (opsiyonel)")


@mcp.tool(
    name="navlungo_create_shipment",
    annotations={
        "title": "Navlungo Gönderi Oluştur",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def navlungo_create_shipment(params: CreateShipmentInput) -> str:
    """
    Navlungo Domestic üzerinden yeni bir kargo gönderisi oluşturur.
    Başarılı olursa gönderi numarası ve barkod URL'ini döndürür.

    Args:
        params: Gönderi bilgileri (alıcı, taşıyıcı, desi, kapıda ödeme vb.)

    Returns:
        str: Gönderi numarası, barkod URL ve takip linki içeren JSON
    """
    token = await get_token()

    post_entry: dict = {
        "carrier_id": params.carrier_id,
        "post_type": params.post_type,
        "cod_payment_type": params.cod_payment_type if params.cod_payment_type else "",
        "sender": {"addressId": params.sender_address_id},
        "recipient": {
            "name": params.recipient_name,
            "phone": params.recipient_phone,
            "address": params.recipient_address,
            "country": "tr",
            "city": params.recipient_city,
            "district": params.recipient_district,
            "email": params.recipient_email or "",
            "post_code": params.recipient_post_code or "",
        },
        "post": {
            "desi": params.desi,
            "package_count": params.package_count,
            "price": params.cod_amount if params.cod_amount else "",
            "note": params.note or "",
        },
        "barcode_format": "pdf-A5",
    }
    if params.reference_id:
        post_entry["reference_id"] = params.reference_id

    payload = {"posts": [post_entry]}

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{BASE_URL}/post/create",
            json=payload,
            headers=_headers_with_token(token),
        )

    if resp.status_code not in (200, 201):
        return _handle_error(resp)

    data = resp.json()
    carrier_name = CARRIER_NAMES.get(params.carrier_id, f"ID:{params.carrier_id}")

    result = {
        "durum": "✅ Gönderi oluşturuldu",
        "post_number": data.get("post_number"),
        "reference_id": data.get("reference_id"),
        "taşıyıcı": carrier_name,
        "barkod_url": data.get("barcode_url"),
        "takip_url": data.get("tracking_url"),
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------  Gönderi Sorgulama  ---------------------------------

class CheckShipmentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    post_number: str = Field(
        ...,
        description="Navlungo gönderi numarası (örn: 'MFYS29970') veya kendi reference_id'niz",
        min_length=3,
    )


@mcp.tool(
    name="navlungo_check_shipment",
    annotations={
        "title": "Navlungo Gönderi Sorgula",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def navlungo_check_shipment(params: CheckShipmentInput) -> str:
    """
    Bir gönderinin mevcut durumunu, teslimat loglarını ve barkod bilgisini getirir.

    Args:
        params: Sorgulanacak gönderi numarası veya reference_id

    Returns:
        str: Gönderi durumu, statü, log geçmişi ve barkod URL içeren JSON
    """
    token = await get_token()

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{BASE_URL}/post/check/{params.post_number}",
            headers=_headers_with_token(token),
        )

    if resp.status_code != 200:
        return _handle_error(resp)

    data = resp.json().get("data", {})
    status_obj = data.get("status", {})
    status_code = status_obj.get("status_code")
    status_name = STATUS_NAMES.get(status_code, f"Kod:{status_code}")

    logs = [
        {
            "tarih": log.get("created_at"),
            "durum": log.get("action_result"),
        }
        for log in data.get("logs", [])
    ]

    result = {
        "post_number": data.get("post_number"),
        "reference_id": data.get("reference_id"),
        "durum": f"{status_code} - {status_name}",
        "teslim_tarihi": status_obj.get("delivered_date"),
        "taşıyıcı": data.get("post", {}).get("carrier_name"),
        "kapıda_ödeme_tutarı": data.get("post", {}).get("post", {}).get("price"),
        "barkod_url": data.get("barcode"),
        "takip_url": data.get("tracking_url"),
        "taşıyıcı_takip_no": data.get("carrier_tracking_code"),
        "alıcı": {
            "ad": data.get("post", {}).get("recipient", {}).get("name"),
            "şehir": data.get("post", {}).get("recipient", {}).get("city"),
            "ilçe": data.get("post", {}).get("recipient", {}).get("district"),
        },
        "log_geçmişi": logs,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------  Gönderi İptal  ------------------------------------

class CancelShipmentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    post_number: str = Field(
        ...,
        description="İptal edilecek Navlungo gönderi numarası (örn: 'MFYS29970')",
        min_length=3,
    )


@mcp.tool(
    name="navlungo_cancel_shipment",
    annotations={
        "title": "Navlungo Gönderi İptal Et",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def navlungo_cancel_shipment(params: CancelShipmentInput) -> str:
    """
    Bir kargo gönderisini iptal eder. Yalnızca 'Teslim Alınacak' veya 'Ön İzleme'
    durumundaki gönderiler iptal edilebilir.

    Args:
        params: İptal edilecek gönderi numarası

    Returns:
        str: İptal sonucu ve güncel gönderi bilgisi
    """
    token = await get_token()

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{BASE_URL}/post/cancel",
            json={"post_number": params.post_number},
            headers=_headers_with_token(token),
        )

    if resp.status_code != 200:
        return _handle_error(resp)

    data = resp.json()
    result = {
        "durum": "✅ Gönderi iptal edildi" if data.get("status") else "❌ İptal başarısız",
        "mesaj": data.get("message"),
        "post_number": data.get("data", {}).get("post_number"),
        "reference_id": data.get("data", {}).get("reference_id"),
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------  Adres Defteri Listeleme  ---------------------------

@mcp.tool(
    name="navlungo_list_addresses",
    annotations={
        "title": "Navlungo Adres Defteri",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def navlungo_list_addresses() -> str:
    """
    Navlungo adres defterindeki tüm kayıtlı adresleri listeler.
    Gönderi oluşturmak için gereken sender_address_id bu listeden alınır.

    Returns:
        str: Adres listesi (id, ad, şehir, ilçe, adres türü)
    """
    token = await get_token()

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{BASE_URL}/address-book",
            headers=_headers_with_token(token),
        )

    if resp.status_code != 200:
        return _handle_error(resp)

    data = resp.json()
    addresses = data.get("data", data) if isinstance(data, dict) else data

    if not isinstance(addresses, list):
        addresses = []

    result = []
    for addr in addresses:
        result.append({
            "id": addr.get("id"),
            "ad": addr.get("name") or addr.get("title"),
            "tür": addr.get("address_type"),
            "şehir": addr.get("city"),
            "ilçe": addr.get("district"),
            "adres": addr.get("address"),
            "telefon": addr.get("phone"),
        })

    return json.dumps(
        {"toplam": len(result), "adresler": result},
        ensure_ascii=False,
        indent=2,
    )


# ---------------------  Taşıyıcılarım  ------------------------------------

@mcp.tool(
    name="navlungo_list_my_carriers",
    annotations={
        "title": "Navlungo Kayıtlı Taşıyıcılarım",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def navlungo_list_my_carriers() -> str:
    """
    Navlungo hesabınıza tanımlı aktif taşıyıcıları listeler.
    Gönderi oluştururken kullanılacak carrier_id değerleri bu listeden alınır.

    Returns:
        str: Taşıyıcı listesi (id, adı, hizmet türleri)
    """
    token = await get_token()

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{BASE_URL}/carriers/my-carriers",
            headers=_headers_with_token(token),
        )

    if resp.status_code != 200:
        return _handle_error(resp)

    data = resp.json()
    carriers = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(carriers, list):
        carriers = []

    result = []
    for c in carriers:
        result.append({
            "carrier_id": c.get("id") or c.get("carrier_id"),
            "ad": c.get("name") or c.get("carrier_name"),
            "hizmetler": c.get("services") or c.get("post_types"),
            "aktif": c.get("is_active", True),
        })

    return json.dumps(
        {"toplam": len(result), "taşıyıcılar": result},
        ensure_ascii=False,
        indent=2,
    )


# ---------------------  Barkod Getir  -------------------------------------

class GetBarcodeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    post_numbers: list[str] = Field(
        ...,
        description="Barkod alınacak gönderi numaraları listesi (örn: ['MFYS29970', 'MFYS29971'])",
        min_length=1,
    )
    barcode_format: str = Field(
        default="pdf-A5",
        description="Barkod formatı: 'pdf-A5' (varsayılan) veya 'pdf-A4'",
    )


@mcp.tool(
    name="navlungo_get_barcode",
    annotations={
        "title": "Navlungo Barkod Al",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def navlungo_get_barcode(params: GetBarcodeInput) -> str:
    """
    Bir veya birden fazla gönderi için PDF barkod URL'lerini getirir.

    Args:
        params: Barkod alınacak gönderi numaraları listesi ve format

    Returns:
        str: Her gönderi için barkod PDF URL'leri
    """
    token = await get_token()

    payload = {
        "post_numbers": params.post_numbers,
        "barcode_format": params.barcode_format,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{BASE_URL}/barcode/get-barcode",
            json=payload,
            headers=_headers_with_token(token),
        )

    if resp.status_code != 200:
        return _handle_error(resp)

    data = resp.json()
    barcodes = data.get("data", data) if isinstance(data, dict) else data

    result = {
        "durum": "✅ Barkodlar hazır",
        "barkodlar": barcodes,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------  Çoklu Gönderi Sorgulama  --------------------------

class CheckMultipleInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    post_numbers: list[str] = Field(
        ...,
        description="Sorgulanacak gönderi numaraları listesi (max 50)",
        min_length=1,
        max_length=50,
    )


@mcp.tool(
    name="navlungo_check_multiple_shipments",
    annotations={
        "title": "Navlungo Toplu Gönderi Sorgula",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def navlungo_check_multiple_shipments(params: CheckMultipleInput) -> str:
    """
    Birden fazla gönderinin durumunu tek sorguda getirir (detaylı).
    Günlük operasyonel kontrol için kullanışlıdır.

    Args:
        params: Sorgulanacak gönderi numaraları listesi (max 50)

    Returns:
        str: Her gönderi için durum, alıcı ve teslimat özeti
    """
    token = await get_token()

    payload = {"post_numbers": params.post_numbers}

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{BASE_URL}/post/check-multiple",
            json=payload,
            headers=_headers_with_token(token),
        )

    if resp.status_code != 200:
        return _handle_error(resp)

    data = resp.json()
    items = data.get("data", []) if isinstance(data, dict) else []

    result = []
    for item in items:
        status_code = item.get("status", {}).get("status_code")
        result.append({
            "post_number": item.get("post_number"),
            "reference_id": item.get("reference_id"),
            "durum": f"{status_code} - {STATUS_NAMES.get(status_code, '?')}",
            "taşıyıcı": item.get("post", {}).get("carrier_name"),
            "alıcı": item.get("post", {}).get("recipient", {}).get("name"),
            "alıcı_şehir": item.get("post", {}).get("recipient", {}).get("city"),
            "teslim_tarihi": item.get("status", {}).get("delivered_date"),
        })

    return json.dumps(
        {"toplam": len(result), "gönderiler": result},
        ensure_ascii=False,
        indent=2,
    )


# ---------------------------------------------------------------------------
# Çalıştır
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount

    port = int(os.getenv("PORT", "8000"))
    mcp_app = mcp.streamable_http_app()

    app = Starlette(
        routes=[
            Mount("/mcp", app=mcp_app),
        ]
    )

    uvicorn.run(app, host="0.0.0.0", port=port)
