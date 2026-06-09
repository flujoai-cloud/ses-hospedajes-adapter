#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generador_parte.py — Modelo de datos y generador de XML para partes de viajeros.

Separa la estructura de datos (dataclasses) de la lógica de envío (ses_hospedajes.py).
Permite construir el XML de un parte desde cualquier fuente: PMS, hoja de cálculo, entrada manual.

Fuentes: manual MIR v3.1.2 (reference/xsd-partes-viajeros.md) + FAQ 09/04/2025.
Todo lo marcado # POR CONFIRMAR depende de validación contra el entorno de pruebas.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

NS_ALTA_PARTE = "http://www.neg.hospedajes.mir.es/altaParteHospedaje"

# Letra correcta para cada dígito de NIF (tabla estándar del DNI español).
_NIF_LETRAS = "TRWAGMYFPDXBNJZSQVHLCKE"
_NIF_RE = re.compile(r"^\d{8}[A-Za-z]$")
_NIE_RE = re.compile(r"^[XYZxyz]\d{7}[A-Za-z]$")


# ─────────────────────────────────────────────────────────────────────────────
#  Modelo de datos (pág. 16–19, 67–68, manual MIR v3.1.2)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DatosPago:
    """Bloque <pago> dentro de <contrato>. Manual pág. 68."""
    # tipoPago: catálogo TIPO_PAGO — POR CONFIRMAR los códigos (operación catalogo)
    tipo_pago: str                          # String(5), obligatorio
    fecha_pago: Optional[date] = None       # AAAA-MM-DD, opcional
    medio_pago: Optional[str] = None        # String(50), opcional
    titular: Optional[str] = None           # String(100), opcional
    caducidad_tarjeta: Optional[str] = None # String(7) MM/AAAA, opcional (pág.68; cambiado en v3.1.1)


@dataclass
class DatosDireccion:
    """Bloque <direccion> dentro de <persona>. Manual pág. 67.
    Orden de elementos confirmado por la tabla del manual (top→bottom = wire order).
    """
    direccion: str                          # String(100), obligatorio
    pais: str                               # ISO 3166-1 Alfa-3, obligatorio
    # codigoMunicipio: obligatorio cuando pais=ESP (pág.67)
    codigo_municipio: Optional[str] = None  # String(5), código INE
    # nombreMunicipio: obligatorio cuando pais != ESP (pág.67)
    nombre_municipio: Optional[str] = None  # String(100)
    direccion_complementaria: Optional[str] = None  # String(100), opcional
    codigo_postal: Optional[str] = None     # String(20), obligatorio según pág.67


@dataclass
class DatosPersona:
    """Bloque <persona> dentro de <comunicacion>. Manual págs. 17–19.
    Orden de elementos (Fig. 3, pág. 16 + ejemplo Anexo I, pág. 71–72):
    rol, nombre, apellido1, apellido2, tipoDocumento, numeroDocumento, soporteDocumento,
    fechaNacimiento, nacionalidad, sexo, direccion, telefono, telefono2, correo, parentesco.
    """
    nombre: str                              # String(50), obligatorio
    apellido1: str                           # String(50), obligatorio
    # tipoDocumento: catálogo TIPO_DOCUMENTO (confirmado 2026-06-09):
    #   NIF=DNI español, NIE=NIE, PAS=Pasaporte, CIF=CIF, CIF_E=CIF extranjero, OTRO=otro extran.
    # FAQ 27 (pág.6): españoles → NIF; extranjeros no comunitarios → PAS; comunitarios → PAS u OTRO
    tipo_documento: str                      # String(5), obligatorio si mayor de edad (pág.18)
    numero_documento: str                    # String(15), obligatorio si mayor de edad (pág.18)
    fecha_nacimiento: date                   # AAAA-MM-DD, obligatorio
    direccion: DatosDireccion               # bloque dirección, obligatorio

    rol: str = "VI"                          # String(2), siempre 'VI' para viajeros (pág.17)
    # apellido2: obligatorio si tipo_documento='NIF' (pág.18)
    # POR CONFIRMAR si también aplica a 'NIE' (contradicción pág.2 vs pág.18 del manual)
    apellido2: Optional[str] = None          # String(50)
    # soporteDocumento: obligatorio si tipo_documento en {NIF, NIE} (pág.18; FAQ 28, pág.6)
    soporte_documento: Optional[str] = None  # String(9)
    nacionalidad: Optional[str] = None       # ISO 3166-1 Alfa-3, opcional
    # sexo: XSD lo marca opcional (pág.18), pero FAQ 30 (pág.6) lo exige cuando consta en el doc.
    # Catálogo SEXO confirmado (2026-06-09): H=Hombre, M=Mujer, O=Otro
    sexo: Optional[str] = None
    # Al menos uno de los tres es obligatorio (pág.18–19; FAQ 29, pág.6)
    telefono: Optional[str] = None           # String(20)
    telefono2: Optional[str] = None          # String(20)
    correo: Optional[str] = None             # String(250), formato email válido
    # parentesco: obligatorio si el viajero es menor de edad (pág.19; FAQ 25, pág.5)
    # Catálogo TIPO_PARENTESCO confirmado (2026-06-09):
    #   PM=Padre/madre, HJ=Hijo/a, CY=Cónyuge, HR=Hermano/a, AB=Abuelo/a, NI=Nieto/a,
    #   TI=Tío/a, SB=Sobrino/a, CD=Cuñado/a, SG=Suegro/a, YN=Yerno/nuera,
    #   BA=Bisabuelo/a, BN=Bisnieto/a, TU=Tutor/a, OT=Otro
    parentesco: Optional[str] = None         # String(5)


@dataclass
class DatosContrato:
    """Bloque <contrato> dentro de <comunicacion>. Manual págs. 17–18."""
    referencia: str              # String(50), obligatorio
    fecha_contrato: date         # AAAA-MM-DD, obligatorio
    fecha_entrada: datetime      # AAAA-MM-DDThh:mm:ss, obligatorio
    fecha_salida: datetime       # AAAA-MM-DDThh:mm:ss, obligatorio
    num_personas: int            # Numérico, obligatorio
    pago: DatosPago              # bloque pago, obligatorio (pág.17: campo Sí)
    num_habitaciones: Optional[int] = None  # Numérico, opcional
    internet: Optional[bool] = None         # Booleano, opcional


@dataclass
class DatosComunicacion:
    """Un bloque <comunicacion> = un contrato + sus viajeros."""
    contrato: DatosContrato
    personas: List[DatosPersona]


@dataclass
class DatosParte:
    """Raíz del parte: establecimiento + lista de comunicaciones (1..n)."""
    codigo_establecimiento: str            # String(10), obligatorio (pág.17)
    comunicaciones: List[DatosComunicacion]


# ─────────────────────────────────────────────────────────────────────────────
#  Validación
# ─────────────────────────────────────────────────────────────────────────────

class ErrorValidacion(ValueError):
    """Datos del parte inválidos según las reglas del XSD / manual MIR."""


def _check(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise ErrorValidacion(mensaje)


def _max_len(valor: Optional[str], max_len: int, nombre: str) -> None:
    if valor is not None and len(str(valor)) > max_len:
        raise ErrorValidacion(f"{nombre}: longitud {len(str(valor))} supera el máximo {max_len}.")


def _validar_nif(numero: str) -> bool:
    """Verifica que número sea un NIF con formato y letra de control correctos."""
    if not _NIF_RE.match(numero):
        return False
    return numero[-1].upper() == _NIF_LETRAS[int(numero[:8]) % 23]


def validar_parte(parte: DatosParte) -> None:
    """Valida un DatosParte completo. Lanza ErrorValidacion si algo no cumple el XSD/manual."""
    _check(parte.codigo_establecimiento, "codigo_establecimiento es obligatorio.")
    _max_len(parte.codigo_establecimiento, 10, "codigo_establecimiento")
    _check(parte.comunicaciones, "El parte debe tener al menos una comunicacion.")

    for i, com in enumerate(parte.comunicaciones, 1):
        pfx = f"comunicacion[{i}]"
        c = com.contrato

        _check(c.referencia, f"{pfx}.contrato.referencia es obligatorio.")
        _max_len(c.referencia, 50, f"{pfx}.contrato.referencia")
        _check(c.fecha_entrada < c.fecha_salida,
               f"{pfx}: fechaEntrada debe ser anterior a fechaSalida.")
        _check(c.num_personas >= 1, f"{pfx}: numPersonas debe ser >= 1.")
        _check(c.pago and c.pago.tipo_pago,
               f"{pfx}: pago.tipoPago es obligatorio (pág.17 y 68).")

        _check(com.personas, f"{pfx}: debe haber al menos una persona.")

        for j, p in enumerate(com.personas, 1):
            pp = f"{pfx}.persona[{j}]"

            _check(p.nombre, f"{pp}.nombre es obligatorio.")
            _max_len(p.nombre, 50, f"{pp}.nombre")
            _check(p.apellido1, f"{pp}.apellido1 es obligatorio.")
            _max_len(p.apellido1, 50, f"{pp}.apellido1")
            _check(p.tipo_documento, f"{pp}.tipoDocumento es obligatorio (mayor de edad, pág.18).")
            _check(p.numero_documento, f"{pp}.numeroDocumento es obligatorio (mayor de edad, pág.18).")
            _max_len(p.numero_documento, 15, f"{pp}.numeroDocumento")

            if p.tipo_documento == "NIF":
                _check(p.apellido2,
                       f"{pp}: apellido2 obligatorio cuando tipoDocumento=NIF (pág.18).")
                _check(p.soporte_documento,
                       f"{pp}: soporteDocumento obligatorio cuando tipoDocumento=NIF (pág.18).")
                _check(_validar_nif(p.numero_documento),
                       f"{pp}: numeroDocumento '{p.numero_documento}' no es un NIF válido "
                       "(8 dígitos + letra de control correcta).")
            if p.tipo_documento == "NIE":
                # POR CONFIRMAR si apellido2 es también obligatorio para NIE
                # (contradicción manual pág.18 vs changelog pág.2)
                _check(p.soporte_documento,
                       f"{pp}: soporteDocumento obligatorio cuando tipoDocumento=NIE (pág.18).")

            _check(p.telefono or p.telefono2 or p.correo,
                   f"{pp}: obligatorio al menos uno de telefono, telefono2, correo (pág.18; FAQ 29).")

            d = p.direccion
            _check(d.direccion, f"{pp}.direccion.direccion es obligatorio.")
            _max_len(d.direccion, 100, f"{pp}.direccion.direccion")
            _check(d.pais, f"{pp}.direccion.pais es obligatorio.")

            if d.pais == "ESP":
                _check(d.codigo_municipio,
                       f"{pp}.direccion: codigoMunicipio obligatorio cuando pais=ESP (pág.67).")
            else:
                _check(d.nombre_municipio,
                       f"{pp}.direccion: nombreMunicipio obligatorio cuando pais != ESP (pág.67).")


# ─────────────────────────────────────────────────────────────────────────────
#  Generador de XML
# ─────────────────────────────────────────────────────────────────────────────

def _sub(parent: ET.Element, tag: str, texto: Optional[str]) -> None:
    """Añade <tag>texto</tag> a parent solo si texto no es None."""
    if texto is not None:
        ET.SubElement(parent, tag).text = texto


def _fecha(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _fecha_hora(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def generar_xml_parte(parte: DatosParte, validar: bool = True) -> str:
    """Genera el XML de un parte de viajeros listo para ZIP+Base64 y envío.

    Devuelve una cadena unicode con el XML (sin declaración; se añade en zip_base64).
    Si validar=True (por defecto), llama a validar_parte() antes de generar.
    El orden de los elementos sigue Fig. 3 (pág.16) y el ejemplo del Anexo I (págs.71–72).
    """
    if validar:
        validar_parte(parte)

    ET.register_namespace("alt", NS_ALTA_PARTE)
    raiz = ET.Element(f"{{{NS_ALTA_PARTE}}}peticion")
    solicitud = ET.SubElement(raiz, "solicitud")
    ET.SubElement(solicitud, "codigoEstablecimiento").text = parte.codigo_establecimiento

    for com_datos in parte.comunicaciones:
        com_el = ET.SubElement(solicitud, "comunicacion")

        # ── contrato ────────────────────────────────────────────────────────
        c = com_datos.contrato
        cont_el = ET.SubElement(com_el, "contrato")
        ET.SubElement(cont_el, "referencia").text = c.referencia
        ET.SubElement(cont_el, "fechaContrato").text = _fecha(c.fecha_contrato)
        ET.SubElement(cont_el, "fechaEntrada").text = _fecha_hora(c.fecha_entrada)
        ET.SubElement(cont_el, "fechaSalida").text = _fecha_hora(c.fecha_salida)
        ET.SubElement(cont_el, "numPersonas").text = str(c.num_personas)
        _sub(cont_el, "numHabitaciones",
             str(c.num_habitaciones) if c.num_habitaciones is not None else None)
        if c.internet is not None:
            ET.SubElement(cont_el, "internet").text = "true" if c.internet else "false"

        pago_el = ET.SubElement(cont_el, "pago")
        ET.SubElement(pago_el, "tipoPago").text = c.pago.tipo_pago
        _sub(pago_el, "fechaPago", _fecha(c.pago.fecha_pago) if c.pago.fecha_pago else None)
        _sub(pago_el, "medioPago", c.pago.medio_pago)
        _sub(pago_el, "titular", c.pago.titular)
        _sub(pago_el, "caducidadTarjeta", c.pago.caducidad_tarjeta)

        # ── personas ─────────────────────────────────────────────────────────
        for p in com_datos.personas:
            pers_el = ET.SubElement(com_el, "persona")

            # Orden confirmado: Fig.3 (pág.16) + ejemplo Anexo I (págs.71–72)
            ET.SubElement(pers_el, "rol").text = p.rol
            ET.SubElement(pers_el, "nombre").text = p.nombre
            ET.SubElement(pers_el, "apellido1").text = p.apellido1
            _sub(pers_el, "apellido2", p.apellido2)
            ET.SubElement(pers_el, "tipoDocumento").text = p.tipo_documento
            ET.SubElement(pers_el, "numeroDocumento").text = p.numero_documento
            _sub(pers_el, "soporteDocumento", p.soporte_documento)
            ET.SubElement(pers_el, "fechaNacimiento").text = _fecha(p.fecha_nacimiento)
            _sub(pers_el, "nacionalidad", p.nacionalidad)
            _sub(pers_el, "sexo", p.sexo)

            d = p.direccion
            dir_el = ET.SubElement(pers_el, "direccion")
            ET.SubElement(dir_el, "direccion").text = d.direccion
            _sub(dir_el, "direccionComplementaria", d.direccion_complementaria)
            _sub(dir_el, "codigoMunicipio", d.codigo_municipio)
            _sub(dir_el, "nombreMunicipio", d.nombre_municipio)
            _sub(dir_el, "codigoPostal", d.codigo_postal)
            ET.SubElement(dir_el, "pais").text = d.pais

            _sub(pers_el, "telefono", p.telefono)
            _sub(pers_el, "telefono2", p.telefono2)
            _sub(pers_el, "correo", p.correo)
            _sub(pers_el, "parentesco", p.parentesco)  # último, solo si menor con adulto no-progenitor

    return ET.tostring(raiz, encoding="unicode", xml_declaration=False)
