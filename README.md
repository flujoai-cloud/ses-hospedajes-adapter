# scripts/ — Esqueleto de envío (Fase 2, BORRADOR)

Borrador **sin credenciales reales** (a la espera del Ministerio). Por defecto **no envía nada**:
solo construye e imprime las peticiones. Todo lo no verificado está marcado `# POR CONFIRMAR`.

## Ficheros
- `ejemplo_parte_viajeros.xml` — XML de ejemplo de un alta de parte (un viajero ficticio),
  con los campos documentados en `../reference/` y comentarios en cada `POR CONFIRMAR`.
- `ses_hospedajes.py` — módulo con las 5 piezas:
  1. `zip_base64()` — ZIP + Base64 de `<solicitud>`.
  2. `construir_soap_comunicacion()` — sobre SOAP (alta / consulta / anulación).
  3. `enviar_soap()` — POST con Basic Auth (credenciales desde el entorno) a **pruebas**.
  4. `consultar_lote()` — leer el resultado del lote.
  5. `consultar_catalogo()` — pedir las tablas de códigos.
- `.env.example` — plantilla de variables de entorno (sin valores). Copiar a `.env`.

## Cómo probar ahora (sin credenciales)
```sh
python3 scripts/ses_hospedajes.py
```
Imprime el ZIP+Base64, el sobre SOAP del alta, el de consulta de lote y el de catálogo. No hay red.

## Cuando lleguen las credenciales de pruebas
```sh
cp scripts/.env.example scripts/.env      # y rellenar .env (NO se versiona)
set -a; source scripts/.env; set +a
python3 scripts/ses_hospedajes.py --enviar # envía a PRE (pruebas)
```

## Seguridad
- Usuario WS y contraseña **solo** en variables de entorno; nunca en el código ni en git.
- `.env` está excluido en el `.gitignore` de la raíz del proyecto.
- Producción está bloqueada en el código hasta validar en pruebas.

## Pendiente de validar contra el entorno de pruebas (`POR CONFIRMAR`)
- `Content-Type` exacto y si hace falta cabecera `SOAPAction`.
- Nombre/namespace del wrapper SOAP de `consultaLote` (directa) y de `catalogo` (no hay ejemplo en el manual).
- Si el `.zip` exige un nombre de fichero interno concreto.
- Códigos de catálogo (TIPO_DOCUMENTO, TIPO_PARENTESCO, SEXO, TIPO_PAGO): los dará la operación `catalogo`.
- Ambigüedad `apellido2` NIF vs NIF/NIE; umbral de "mayor de edad".
- Generación del XML por reserva (sustituir el `codigoEstablecimiento` placeholder y los datos reales).
