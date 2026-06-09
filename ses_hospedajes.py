#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SES.HOSPEDAJES — Esqueleto de envío de partes de viajeros (Fase 2, BORRADOR).

Estado: BORRADOR, sin credenciales reales (a la espera del Ministerio).
Por defecto NO envía nada a la red: solo construye e imprime la petición ("dry-run").

Piezas:
  1) zip_base64()                  Comprime el XML del parte en ZIP y lo pasa a Base64.
  2) construir_soap_comunicacion() Monta el SOBRE SOAP de la operación 'comunicacion'.
  3) enviar_soap()                 POST con Basic Auth (usuario/clave desde el ENTORNO)
                                   al endpoint de PRUEBAS.
  4) consultar_lote()              Esqueleto para leer el resultado de un lote.
  5) consultar_catalogo()          Esqueleto para pedir las tablas de códigos.

Fuentes (todo citado en ../reference/): manual MIR v3.1.2 y FAQ 09/04/2025.
Lo no confirmado va marcado con  # POR CONFIRMAR  para validarlo cuando haya credenciales.

Seguridad:
  - El usuario WS y la contraseña NUNCA se escriben en el código: se leen de variables de
    entorno (ver .env.example y README.md). Tratarlos como credenciales de banca.
  - Nunca producción mientras el entorno de PRUEBAS no esté en verde.

Uso:
    python3 ses_hospedajes.py              # dry-run: construye e imprime, NO envía
    python3 ses_hospedajes.py --enviar     # envía a PRUEBAS (requiere variables de entorno)
"""

import base64
import io
import os
import ssl
import sys
import zipfile
import urllib.request
import urllib.error
from datetime import date, datetime
from xml.sax.saxutils import escape

from generador_parte import (
    DatosContrato, DatosComunicacion, DatosDireccion,
    DatosPago, DatosParte, DatosPersona,
    generar_xml_parte,
)

# --------------------------------------------------------------------------- #
# 0) Constantes CONFIRMADAS en el manual / ejemplos oficiales
# --------------------------------------------------------------------------- #
# Endpoints (manual v3.1.2, ap. 2.1, pág. 10).
ENDPOINTS = {
    "pre": "https://hospedajes.pre-ses.mir.es/hospedajes-web/ws/v1/comunicacion",  # pruebas
    "pro": "https://hospedajes.ses.mir.es/hospedajes-web/ws/v1/comunicacion",      # producción
}

# POR CONFIRMAR: el manual no especifica la URL del endpoint de catálogo.
# Probamos /catalogo como variante natural de /comunicacion. Si falla, ajustar.
ENDPOINTS_CATALOGO = {
    "pre": "https://hospedajes.pre-ses.mir.es/hospedajes-web/ws/v1/catalogo",
    "pro": "https://hospedajes.ses.mir.es/hospedajes-web/ws/v1/catalogo",
}

# Namespaces confirmados por los ejemplos del manual (Anexos I–III, págs. 70–76):
NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
NS_COMUNICACION = "http://www.soap.servicios.hospedajes.mir.es/comunicacion"  # wrapper comunicacionRequest
NS_ALTA_PARTE = "http://www.neg.hospedajes.mir.es/altaParteHospedaje"         # XML interno de alta PV
NS_CONSULTA = "http://www.neg.hospedajes.mir.es/consultarComunicacion"        # XML interno de consulta por lote
NS_ANULACION = "http://www.neg.hospedajes.mir.es/anularComunicacion"          # XML interno de anulación

DIR = os.path.dirname(os.path.abspath(__file__))

# Certificado intermedio de FNMT-RCM (AC Componentes Informáticos) incluido en el proyecto.
# El servidor del Ministerio no envía la cadena completa en el handshake TLS; Python 3.x
# no puede verificar sin la intermedia porque su bundle propio no incluye la CA raíz FNMT.
# Certificado público, sin secreto alguno. Fuente: http://www.cert.fnmt.es/certs/ACCOMP.crt
_FNMT_CERT = os.path.join(DIR, "certs", "fnmt_accomp.pem")
_SYSTEM_CA = "/etc/ssl/cert.pem"  # macOS; en Linux: /etc/ssl/certs/ca-certificates.crt


def _ssl_context() -> ssl.SSLContext:
    """Contexto SSL que verifica certificados del Ministerio del Interior (firmados por FNMT-RCM)."""
    import tempfile
    if os.path.exists(_FNMT_CERT) and os.path.exists(_SYSTEM_CA):
        bundle = open(_SYSTEM_CA).read() + "\n" + open(_FNMT_CERT).read()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
            f.write(bundle)
            bundle_path = f.name
        try:
            return ssl.create_default_context(cafile=bundle_path)
        finally:
            os.unlink(bundle_path)
    return ssl.create_default_context()


# --------------------------------------------------------------------------- #
# Configuración por hotel — TODO desde el entorno (nada en el código)
# --------------------------------------------------------------------------- #
def cargar_config():
    """Lee la configuración desde variables de entorno.

    Fase 4 (reutilizable): todo lo que cambia por hotel vive en un único sitio (el entorno),
    no en el código. Los secretos (usuario/contraseña) tampoco se escriben aquí.
    """
    return {
        "entorno": os.environ.get("SES_HOSPEDAJES_ENTORNO", "pre").lower(),
        "usuario": os.environ.get("SES_HOSPEDAJES_USUARIO"),                 # SECRETO
        "password": os.environ.get("SES_HOSPEDAJES_PASSWORD"),              # SECRETO
        "codigo_arrendador": os.environ.get("SES_HOSPEDAJES_CODIGO_ARRENDADOR"),          # 10 dígitos
        "codigo_establecimiento": os.environ.get("SES_HOSPEDAJES_CODIGO_ESTABLECIMIENTO"),  # 10 dígitos
        "aplicacion": os.environ.get("SES_HOSPEDAJES_APLICACION", "BORRADOR-SES-Hospedajes"),
    }


def _endpoint(entorno):
    if entorno not in ENDPOINTS:
        raise ValueError(f"Entorno no válido: {entorno!r}. Use 'pre' o 'pro'.")
    return ENDPOINTS[entorno]


# --------------------------------------------------------------------------- #
# 1) Comprimir en ZIP + Base64
# --------------------------------------------------------------------------- #
def zip_base64(xml, nombre_entrada="parte.xml"):
    """Comprime el XML en un fichero ZIP y lo codifica en Base64.

    El manual exige que <solicitud> sea un fichero XML en UTF-8, comprimido con ZIP y
    codificado en Base64 (ap. 3.1, pág. 14; y el error 10111 lo confirma, pág. 69).

    'nombre_entrada' = nombre del fichero DENTRO del .zip.
    # POR CONFIRMAR: si el servicio exige un nombre concreto. Usamos uno genérico.
    """
    if isinstance(xml, str):
        xml = xml.encode("utf-8")  # UTF-8 confirmado (error 10111, pág. 69)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(nombre_entrada, xml)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


# --------------------------------------------------------------------------- #
# 2) Sobre SOAP de la operación 'comunicacion' (alta / consulta / anulación)
# --------------------------------------------------------------------------- #
def _cabecera_authorization(usuario, password):
    """Valor de la cabecera HTTP 'Authorization: Basic ...'.
    token = Base64(usuario:contraseña) (manual ap. 2.2, pág. 10).
    """
    token = base64.b64encode(f"{usuario}:{password}".encode("utf-8")).decode("ascii")
    return "Basic " + token


def construir_soap_comunicacion(cfg, solicitud_b64, tipo_operacion, tipo_comunicacion=None):
    """Monta el sobre SOAP de la operación 'comunicacion'.

    Estructura confirmada por el Anexo I (págs. 70–72). Nota: solo 'comunicacionRequest'
    lleva prefijo de namespace (com:); peticion/cabecera/... van SIN namespace, igual que
    en el ejemplo oficial. Esto además resuelve la antigua duda de nombres de cabecera:
    el ejemplo que funciona usa <codigoArrendador> y <aplicacion> (no 'arrendador'/'aplicación').

    tipo_operacion:     'A' alta | 'C' consulta | 'B' anulación (pág. 15).
    tipo_comunicacion:  'PV','RH','AV','RV' — SOLO en alta (pág. 15).
    """
    for clave in ("codigo_arrendador", "aplicacion"):
        if not cfg.get(clave):
            raise ValueError(f"Falta config obligatoria: {clave} (ver .env.example).")

    linea_tipo_com = ""
    if tipo_operacion == "A":
        if not tipo_comunicacion:
            raise ValueError("En alta (A) hay que indicar tipoComunicacion (p.ej. 'PV').")
        linea_tipo_com = f"<tipoComunicacion>{escape(tipo_comunicacion)}</tipoComunicacion>"

    return (
        f'<soapenv:Envelope xmlns:soapenv="{NS_SOAP}" xmlns:com="{NS_COMUNICACION}">'
        "<soapenv:Header/>"
        "<soapenv:Body>"
        "<com:comunicacionRequest>"
        "<peticion>"
        "<cabecera>"
        f"<codigoArrendador>{escape(cfg['codigo_arrendador'])}</codigoArrendador>"
        f"<aplicacion>{escape(cfg['aplicacion'])}</aplicacion>"
        f"<tipoOperacion>{escape(tipo_operacion)}</tipoOperacion>"
        f"{linea_tipo_com}"
        "</cabecera>"
        f"<solicitud>{solicitud_b64}</solicitud>"
        "</peticion>"
        "</com:comunicacionRequest>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )


# --------------------------------------------------------------------------- #
# 3) Envío HTTP con Basic Auth (solo a PRUEBAS en este borrador)
# --------------------------------------------------------------------------- #
def enviar_soap(soap_xml, cfg, endpoint_override=None):
    """Envía el sobre SOAP por HTTP POST con Basic Auth. Devuelve el cuerpo de la respuesta.

    # POR CONFIRMAR: Content-Type exacto. 'text/xml; charset=utf-8' es lo habitual en SOAP 1.1.
    # POR CONFIRMAR: si el servicio exige cabecera 'SOAPAction' (el manual no la especifica).
    """
    if not cfg.get("usuario") or not cfg.get("password"):
        raise RuntimeError(
            "Faltan credenciales. Define SES_HOSPEDAJES_USUARIO y SES_HOSPEDAJES_PASSWORD "
            "en el entorno (ver .env.example). NUNCA las escribas en el código."
        )
    if cfg["entorno"] == "pro":
        raise RuntimeError("Producción bloqueada en este borrador. Usa 'pre' hasta validar en pruebas.")

    endpoint = endpoint_override or _endpoint(cfg["entorno"])
    datos = soap_xml.encode("utf-8")
    req = urllib.request.Request(endpoint, data=datos, method="POST")
    req.add_header("Content-Type", "text/xml; charset=utf-8")  # POR CONFIRMAR
    req.add_header("Authorization", _cabecera_authorization(cfg["usuario"], cfg["password"]))
    # req.add_header("SOAPAction", "")  # POR CONFIRMAR si hace falta

    try:
        with urllib.request.urlopen(req, context=_ssl_context(), timeout=30) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} del servicio:\n{cuerpo}") from e


def construir_alta_parte(cfg, xml_parte):
    """Empaqueta un alta de parte de viajeros (PV) lista para enviar. Devuelve el sobre SOAP."""
    solicitud_b64 = zip_base64(xml_parte)
    return construir_soap_comunicacion(cfg, solicitud_b64, tipo_operacion="A", tipo_comunicacion="PV")


# --------------------------------------------------------------------------- #
# 4) Consulta de lote (leer el resultado del alta)
# --------------------------------------------------------------------------- #
def construir_xml_consulta_lote(numero_lote):
    """XML interno para consultar un lote (Anexo II, pág. 74). Namespace CONFIRMADO."""
    return (
        f'<con:lotes xmlns:con="{NS_CONSULTA}">'
        f"<con:lote>{escape(numero_lote)}</con:lote>"
        "</con:lotes>"
    )


def consultar_lote(cfg, numero_lote, enviar=False):
    """Esqueleto de consulta de lote.

    Vía CONFIRMADA (Anexo II, págs. 73–74): operación 'comunicacion' con tipoOperacion='C'
    y, en <solicitud>, el XML <con:lotes> comprimido en ZIP+Base64 (igual que el alta).
    En consulta NO se envía tipoComunicacion (pág. 15).

    La respuesta trae, por comunicación: codigoComunicacion (ok) o tipoError+error
    (ap. 3.1.2/3.2.2). Estados de lote (codigoEstado 1–6) en ../reference/codigos-error.md.

    Devuelve el sobre SOAP (str). Solo envía si enviar=True y hay credenciales.
    """
    xml_interno = construir_xml_consulta_lote(numero_lote)
    solicitud_b64 = zip_base64(xml_interno, nombre_entrada="consulta.xml")
    soap = construir_soap_comunicacion(cfg, solicitud_b64, tipo_operacion="C")
    return enviar_soap(soap, cfg) if enviar else soap


def construir_soap_consulta_lote_directa(numero_lote):
    """ALTERNATIVA: operación 'consultaLote' (ap. 3.2). Los códigos de lote viajan SIN
    comprimir, directamente en el mensaje (consultaLoteRequest/codigosLote/lote, Fig.13, pág.40).

    # POR CONFIRMAR: el manual NO incluye un ejemplo SOAP de esta operación, así que el nombre
    # del wrapper y su namespace ('com:consultaLoteRequest' aquí) están por validar.
    """
    return (
        f'<soapenv:Envelope xmlns:soapenv="{NS_SOAP}" xmlns:com="{NS_COMUNICACION}">'
        "<soapenv:Header/>"
        "<soapenv:Body>"
        "<com:consultaLoteRequest>"  # POR CONFIRMAR nombre/namespace del wrapper
        "<codigosLote>"
        f"<lote>{escape(numero_lote)}</lote>"
        "</codigosLote>"
        "</com:consultaLoteRequest>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )


# --------------------------------------------------------------------------- #
# 5) Catálogo (resolverá varios POR CONFIRMAR en cuanto haya credenciales)
# --------------------------------------------------------------------------- #
def construir_soap_catalogo(nombre_tabla=""):
    """Esqueleto de la operación 'catalogo' (ap. 3.5, págs. 63–65).

    Petición: catalogoRequest/peticion/catalogo. Si 'catalogo' va VACÍO, el servicio
    devuelve la LISTA de tablas; con un nombre (TIPO_DOCUMENTO, TIPO_PARENTESCO, SEXO,
    TIPO_PAGO, ...) devuelve códigos+descripciones de esa tabla. Operación SÍNCRONA y
    NO comprimida (no usa zip+base64).

    # POR CONFIRMAR: el manual no trae ejemplo SOAP de esta operación, así que el nombre del
    # wrapper y su namespace ('com:catalogoRequest' aquí) están por validar. Tampoco se sabe
    # si requiere la cabecera con codigoArrendador (el esquema Fig.31 no la muestra).
    """
    contenido = f"<catalogo>{escape(nombre_tabla)}</catalogo>" if nombre_tabla else "<catalogo/>"
    return (
        f'<soapenv:Envelope xmlns:soapenv="{NS_SOAP}" xmlns:com="{NS_COMUNICACION}">'
        "<soapenv:Header/>"
        "<soapenv:Body>"
        "<com:catalogoRequest>"  # POR CONFIRMAR nombre/namespace del wrapper
        "<peticion>"
        f"{contenido}"
        "</peticion>"
        "</com:catalogoRequest>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )


def consultar_catalogo(cfg, nombre_tabla="", enviar=False):
    """Construye (y opcionalmente envía) la consulta de una tabla de catálogo.
    Resuelve los POR CONFIRMAR de códigos (TIPO_DOCUMENTO, TIPO_PARENTESCO, SEXO, TIPO_PAGO).
    Usa ENDPOINTS_CATALOGO (/catalogo) — POR CONFIRMAR si la URL es correcta.
    """
    soap = construir_soap_catalogo(nombre_tabla)
    if not enviar:
        return soap
    endpoint = ENDPOINTS_CATALOGO.get(cfg.get("entorno", "pre"))
    return enviar_soap(soap, cfg, endpoint_override=endpoint)


# --------------------------------------------------------------------------- #
# Modo de ejecución
# --------------------------------------------------------------------------- #
def _ejemplo_parte(cfg) -> DatosParte:
    """Crea un DatosParte de ejemplo con datos ficticios (para dry-run y pruebas).

    codigoEstablecimiento viene de la config (variable de entorno) o usa el placeholder
    si no está definida. En producción, este objeto se construirá desde el PMS/reservas.
    La referencia incluye un timestamp para evitar el error 10121 (lote duplicado):
    el servicio rechaza envíos con contenido ZIP idéntico al de un lote anterior.
    """
    import time as _time
    codigo_est = cfg.get("codigo_establecimiento") or "1234567890"  # PLACEHOLDER
    referencia_unica = f"RES-EJEMPLO-{int(_time.time())}"
    return DatosParte(
        codigo_establecimiento=codigo_est,
        comunicaciones=[
            DatosComunicacion(
                contrato=DatosContrato(
                    referencia=referencia_unica,
                    fecha_contrato=date(2026, 6, 5),
                    fecha_entrada=datetime(2026, 6, 10, 14, 0, 0),
                    fecha_salida=datetime(2026, 6, 12, 11, 0, 0),
                    num_personas=1,
                    num_habitaciones=1,
                    internet=True,
                    pago=DatosPago(
                        tipo_pago="EFECT",           # Efectivo (TIPO_PAGO confirmado 2026-06-09)
                        fecha_pago=date(2026, 6, 5),
                    ),
                ),
                personas=[
                    DatosPersona(
                        nombre="Juan",
                        apellido1="García",
                        apellido2="López",           # obligatorio para NIF (pág.18)
                        tipo_documento="NIF",        # TIPO_DOCUMENTO confirmado: NIF=DNI español
                        numero_documento="00000000T",
                        soporte_documento="999999999",  # FAQ 28 (pág.6): nº de soporte del DNI
                        fecha_nacimiento=date(1990, 1, 1),
                        nacionalidad="ESP",
                        # sexo: XSD=opcional; FAQ 30=exigible si consta en el doc.
                        # SEXO confirmado: H=Hombre, M=Mujer, O=Otro
                        sexo="H",
                        direccion=DatosDireccion(
                            direccion="Calle Mayor 1",
                            pais="ESP",
                            codigo_municipio="28079",  # Madrid (código INE)
                            codigo_postal="28013",
                        ),
                        telefono="600000000",
                        correo="juan.ejemplo@correo.es",
                    )
                ],
            )
        ],
    )


def _dry_run():
    """Construye e IMPRIME las peticiones SIN enviarlas. No requiere credenciales."""
    print("=" * 72)
    print("DRY-RUN — se construyen las peticiones SIN enviar nada a la red")
    print("=" * 72)

    cfg = cargar_config()
    cfg["codigo_arrendador"] = cfg["codigo_arrendador"] or "0000000000"  # PLACEHOLDER

    parte = _ejemplo_parte(cfg)
    xml_parte = generar_xml_parte(parte)

    print(f"\n[1] XML generado por generador_parte.py: {len(xml_parte)} caracteres")
    solicitud_b64 = zip_base64(xml_parte)
    print(f"[1b] ZIP+Base64 de <solicitud>: {len(solicitud_b64)} caracteres")
    print(f"     (primeros 60: {solicitud_b64[:60]}...)")

    print("\n[2/3] Sobre SOAP de 'comunicacion' (alta PV) — esto es lo que se haría POST:\n")
    print(construir_soap_comunicacion(cfg, solicitud_b64, "A", "PV"))

    print("\n[4] Consulta de lote (sobre de ejemplo, lote ficticio):\n")
    print(consultar_lote(cfg, "00000000-0000-0000-0000-000000000000", enviar=False))

    print("\n[5] Catálogo TIPO_DOCUMENTO (esqueleto, POR CONFIRMAR el wrapper):\n")
    print(construir_soap_catalogo("TIPO_DOCUMENTO"))

    print("\n" + "=" * 72)
    print("Nada se ha enviado. Para enviar de verdad a PRUEBAS:")
    print("  1) define las variables de entorno (ver .env.example)")
    print("  2) ejecuta:  python3 ses_hospedajes.py --enviar")
    print("=" * 72)


def _parsear_respuesta_alta(respuesta_xml: str):
    """Extrae (codigo, descripcion, numero_lote) de la respuesta SOAP del alta."""
    import xml.etree.ElementTree as ET
    codigo = descripcion = lote = None
    try:
        raiz = ET.fromstring(respuesta_xml)
        for el in raiz.iter("codigo"):
            if el.text:
                codigo = el.text.strip()
                break
        for el in raiz.iter("descripcion"):
            if el.text:
                descripcion = el.text.strip()
                break
        for el in raiz.iter("lote"):
            if el.text:
                lote = el.text.strip()
                break
    except ET.ParseError:
        pass
    return codigo, descripcion, lote


def _enviar_real():
    """Envía el alta de ejemplo al entorno de PRUEBAS y consulta el resultado del lote.
    Requiere variables de entorno (ver .env.example).
    """
    import time
    cfg = cargar_config()
    if not cfg["usuario"] or not cfg["password"]:
        sys.exit("ERROR: faltan credenciales en el entorno. Ver .env.example.")
    if cfg["entorno"] != "pre":
        sys.exit("ERROR: este borrador solo permite el entorno 'pre' (pruebas).")
    for clave in ("codigo_arrendador", "codigo_establecimiento"):
        if not cfg.get(clave):
            sys.exit(f"ERROR: falta {clave} en el entorno. Ver .env.example.")

    parte = _ejemplo_parte(cfg)
    xml_parte = generar_xml_parte(parte)
    soap = construir_alta_parte(cfg, xml_parte)

    print("Enviando alta a PRUEBAS...")
    respuesta_alta = enviar_soap(soap, cfg)
    codigo, descripcion, numero_lote = _parsear_respuesta_alta(respuesta_alta)

    print(f"\nRespuesta: codigo={codigo} — {descripcion}")
    if codigo != "0":
        print(f"\nXML completo:\n{respuesta_alta}")
        return

    print(f"Lote: {numero_lote}")

    # Poll hasta que codigoEstado salga de 5=Pendiente (o timeout 60 s).
    # Estados finales: 1=tramitado sin errores, 6=tramitado con errores (codigos-error.md)
    import xml.etree.ElementTree as ET
    max_espera = 60
    intervalo = 5
    transcurrido = 0
    while transcurrido < max_espera:
        time.sleep(intervalo)
        transcurrido += intervalo
        print(f"  {transcurrido}s — consultando lote...")
        respuesta_lote = consultar_lote(cfg, numero_lote, enviar=True)
        try:
            raiz = ET.fromstring(respuesta_lote)
            estado = next((el.text for el in raiz.iter("codigoEstado") if el.text), None)
            if estado and estado != "5":
                break
        except ET.ParseError:
            break

    print(f"\nResultado:\n{respuesta_lote}")


def _modo_catalogo():
    """Consulta una o todas las tablas de catálogo contra el entorno de PRUEBAS.

    Uso:
        python3 ses_hospedajes.py --catalogo              # lista todas las tablas
        python3 ses_hospedajes.py --catalogo TIPO_DOCUMENTO
        python3 ses_hospedajes.py --catalogo SEXO
    """
    cfg = cargar_config()
    if not cfg["usuario"] or not cfg["password"]:
        sys.exit("ERROR: faltan credenciales en el entorno. Ver .env.example.")

    args = sys.argv[sys.argv.index("--catalogo") + 1:]
    tabla = args[0] if args and not args[0].startswith("--") else ""

    if tabla:
        print(f"Consultando catálogo '{tabla}' en {cfg['entorno'].upper()}...\n")
    else:
        print(f"Consultando TODAS las tablas de catálogo en {cfg['entorno'].upper()}...\n")

    try:
        respuesta = consultar_catalogo(cfg, tabla, enviar=True)
        print(respuesta)
    except RuntimeError as e:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    if "--catalogo" in sys.argv:
        _modo_catalogo()
    elif "--enviar" in sys.argv:
        _enviar_real()
    else:
        _dry_run()
