#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py — Adaptador HTTP (Flask) entre n8n y el servicio SES.HOSPEDAJES.

Expone endpoints REST simples para que n8n no tenga que manejar SOAP ni ZIP+Base64.
Las credenciales se leen desde variables de entorno (nunca en el código).

Endpoints:
    GET  /health               — comprueba configuración
    POST /parte/alta           — envía un alta PV, devuelve número de lote
    GET  /parte/lote/<numero>  — consulta el estado/resultado de un lote

Ver docker-compose.ses.yml para el despliegue junto a n8n.
"""

import os
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime

from flask import Flask, jsonify, request

# Cuando corre en Docker la estructura es plana (/app/); en local ajusta el path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generador_parte import (
    DatosComunicacion, DatosContrato, DatosDireccion,
    DatosPago, DatosParte, DatosPersona,
    ErrorValidacion, generar_xml_parte,
)
from ses_hospedajes import (
    _parsear_respuesta_alta, cargar_config,
    construir_alta_parte, consultar_lote, enviar_soap,
)

app = Flask(__name__)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _d(s):
    return datetime.strptime(s, "%Y-%m-%d").date() if s else None


def _dt(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S") if s else None


def _json_a_parte(data: dict, cfg: dict) -> DatosParte:
    """Convierte el body JSON en un DatosParte listo para generar el XML."""
    personas = []
    for p in data.get("personas", []):
        personas.append(DatosPersona(
            nombre=p["nombre"],
            apellido1=p["apellido1"],
            apellido2=p.get("apellido2"),
            tipo_documento=p["tipo_documento"],
            numero_documento=p["numero_documento"],
            soporte_documento=p.get("soporte_documento"),
            fecha_nacimiento=_d(p["fecha_nacimiento"]),
            nacionalidad=p.get("nacionalidad"),
            sexo=p.get("sexo"),
            telefono=p.get("telefono"),
            telefono2=p.get("telefono2"),
            correo=p.get("correo"),
            parentesco=p.get("parentesco"),
            direccion=DatosDireccion(
                direccion=p["direccion"],
                pais=p.get("pais", "ESP"),
                codigo_municipio=p.get("codigo_municipio"),
                nombre_municipio=p.get("nombre_municipio"),
                codigo_postal=p.get("codigo_postal"),
            ),
        ))

    pago_raw = data.get("pago") or {}
    pago = DatosPago(
        tipo_pago=pago_raw.get("tipo_pago") or data.get("tipo_pago", "OTRO"),
        fecha_pago=_d(pago_raw.get("fecha_pago")),
        medio_pago=pago_raw.get("medio_pago"),
        titular=pago_raw.get("titular"),
        caducidad_tarjeta=pago_raw.get("caducidad_tarjeta"),
    )

    contrato = DatosContrato(
        referencia=data["referencia"],
        fecha_contrato=_d(data["fecha_contrato"]),
        fecha_entrada=_dt(data["fecha_entrada"]),
        fecha_salida=_dt(data["fecha_salida"]),
        num_personas=int(data.get("num_personas") or len(personas)),
        num_habitaciones=data.get("num_habitaciones"),
        internet=data.get("internet"),
        pago=pago,
    )

    codigo_est = data.get("codigo_establecimiento") or cfg.get("codigo_establecimiento")
    return DatosParte(
        codigo_establecimiento=codigo_est,
        comunicaciones=[DatosComunicacion(contrato=contrato, personas=personas)],
    )


# ─── endpoints ───────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    cfg = cargar_config()
    return jsonify({
        "status": "ok",
        "entorno": cfg.get("entorno", "pre"),
        "credenciales": bool(cfg.get("usuario") and cfg.get("password")),
        "arrendador": bool(cfg.get("codigo_arrendador")),
        "establecimiento": bool(cfg.get("codigo_establecimiento")),
    })


@app.route("/parte/alta", methods=["POST"])
def alta_parte():
    """Envía un alta PV al Ministerio. Devuelve el número de lote (proceso asíncrono).

    Body JSON mínimo:
    {
      "referencia": "RES-001",
      "fecha_contrato": "2026-06-15",
      "fecha_entrada": "2026-06-20T14:00:00",
      "fecha_salida": "2026-06-22T11:00:00",
      "tipo_pago": "TARJT",
      "personas": [{
        "nombre": "Juan", "apellido1": "García", "apellido2": "López",
        "tipo_documento": "NIF", "numero_documento": "00000000T",
        "soporte_documento": "999999999", "fecha_nacimiento": "1990-01-01",
        "sexo": "H", "telefono": "600000000",
        "direccion": "Calle Mayor 1", "codigo_municipio": "28079",
        "codigo_postal": "28013", "pais": "ESP"
      }]
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Body JSON requerido"}), 400

    cfg = cargar_config()
    try:
        parte = _json_a_parte(data, cfg)
        xml = generar_xml_parte(parte)
        soap = construir_alta_parte(cfg, xml)
        respuesta = enviar_soap(soap, cfg)
        codigo, descripcion, lote = _parsear_respuesta_alta(respuesta)
        return jsonify({
            "ok": codigo == "0",
            "codigo": codigo,
            "descripcion": descripcion,
            "lote": lote,
        })
    except ErrorValidacion as e:
        return jsonify({"ok": False, "error": "validacion", "detalle": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    except KeyError as e:
        return jsonify({"ok": False, "error": f"Campo obligatorio ausente: {e}"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": "interno", "tipo": type(e).__name__, "detalle": str(e)}), 500


@app.route("/parte/lote/<numero_lote>", methods=["GET"])
def get_lote(numero_lote):
    """Consulta el estado de un lote. Llamar ~15 s después del alta.

    codigoEstado: 1=ok, 4=en proceso, 5=pendiente, 6=con errores en comunicaciones.
    'procesado' es true cuando el estado ya es final (no 4 ni 5).
    """
    cfg = cargar_config()
    try:
        respuesta = consultar_lote(cfg, numero_lote, enviar=True)
        raiz = ET.fromstring(respuesta)

        def _texto(tag):
            el = next(raiz.iter(tag), None)
            return el.text.strip() if el is not None and el.text else None

        estado = _texto("codigoEstado")
        comunicaciones = []
        for rc in raiz.iter("resultadoComunicacion"):
            com = {child.tag: child.text for child in rc}
            comunicaciones.append(com)

        return jsonify({
            "lote": numero_lote,
            "codigoEstado": estado,
            "descEstado": _texto("descEstado"),
            "fechaProcesamiento": _texto("fechaProcesamiento"),
            "procesado": estado not in (None, "4", "5"),
            "ok": estado == "1",
            "comunicaciones": comunicaciones,
        })
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
