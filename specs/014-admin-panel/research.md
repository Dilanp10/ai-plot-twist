# Research — Módulo 014: Admin Panel

## R-001 · Resend Python SDK

**Respuesta:** SDK oficial `resend` en PyPI. Instalación: `uv add resend`.

```python
import resend
resend.api_key = os.environ["RESEND_API_KEY"]
resend.Emails.send({
    "from": "AI Plot Twist <onboarding@resend.dev>",
    "to": [os.environ["ADMIN_EMAIL"]],
    "subject": "🏆 Idea ganadora lista para generar",
    "html": "<p>...</p>",
})
```

Free tier: 3000 emails/mes, 100/día. Más que suficiente para 1 email/día.

## R-002 · Presigned URLs en R2 (boto3)

R2 es compatible con S3 API. El cliente boto3 ya configurado puede generar presigned PUT URLs:

```python
url = s3_client.generate_presigned_url(
    "put_object",
    Params={"Bucket": bucket, "Key": key, "ContentType": "video/mp4"},
    ExpiresIn=900,  # 15 minutos
)
```

El browser hace `PUT <url>` con el archivo — no pasa por la API. CORS del bucket R2 debe
permitir PUT desde el origen de la PWA (ya configurado para GET, hay que agregar PUT).

## R-003 · JWT admin scope

Reusar `JWT_SECRET` existente. Diferenciar tokens admin del token de usuario con claim `scope`:

- Token usuario: `{"sub": "<device_id>", "scope": "user"}`
- Token admin: `{"sub": "admin", "scope": "admin", "exp": +8h}`

La dependency `verify_admin_token` verifica `scope == "admin"` además de la firma.
